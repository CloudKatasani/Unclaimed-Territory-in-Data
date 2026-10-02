"""Agent provenance ledger (A6): append-only, Ed25519-signed, hash-chained per target."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from tessera import clock, ids
from tessera.governance.signing import Signer
from tessera.jsonutil import canonical, dumps, loads, sha256
from tessera.warehouse.base import Warehouse

SIGNED_FIELDS = [
    "artifact_id",
    "artifact_kind",
    "target_fqn",
    "content_sha256",
    "agent",
    "model_name",
    "prompt_id",
    "prompt_sha256",
    "input_refs",
    "tests_run",
    "gate_evidence",
    "approved_by",
    "created_at",
    "prev_artifact_id",
]
HUMAN_PREFIX = "human:"


@dataclass
class Artifact:
    artifact_kind: str
    target_fqn: str
    content: str
    agent: str
    model_name: str = "none"
    prompt_id: str | None = None
    prompt_sha256: str | None = None
    input_refs: dict[str, Any] = field(default_factory=dict)
    tests_run: dict[str, Any] = field(default_factory=dict)
    gate_evidence: dict[str, Any] | None = None
    approved_by: str | None = None


def is_agent(agent: str) -> bool:
    return not agent.startswith(HUMAN_PREFIX)


def _payload(row: dict[str, Any]) -> str:
    body = {k: row.get(k) for k in SIGNED_FIELDS}
    for k in ("input_refs", "tests_run", "gate_evidence"):
        if isinstance(body[k], str):
            body[k] = loads(body[k])
    ts = body["created_at"]
    if isinstance(ts, datetime):
        body["created_at"] = ts.isoformat()
    return canonical(body)


class ProvenanceLedger:
    def __init__(self, wh: Warehouse) -> None:
        self.wh = wh
        self.signer = Signer(wh)

    def head(self, target_fqn: str, kind: str | None = None) -> dict[str, Any] | None:
        sql = "SELECT * FROM meta.provenance WHERE target_fqn = ?"
        params: list[Any] = [target_fqn]
        if kind:
            sql += " AND artifact_kind = ?"
            params.append(kind)
        rows = self.wh.rows(sql + " ORDER BY created_at DESC, artifact_id DESC LIMIT 1", params)
        return rows[0] if rows else None

    def record(self, a: Artifact) -> str:
        prev = self.head(a.target_fqn, a.artifact_kind)
        row: dict[str, Any] = {
            "artifact_id": ids.new_id(self.wh, "artifact"),
            "artifact_kind": a.artifact_kind,
            "target_fqn": a.target_fqn,
            "content_sha256": sha256(a.content),
            "agent": a.agent,
            "model_name": a.model_name,
            "prompt_id": a.prompt_id,
            "prompt_sha256": a.prompt_sha256,
            "input_refs": a.input_refs,
            "tests_run": a.tests_run,
            "gate_evidence": a.gate_evidence,
            "approved_by": a.approved_by,
            "created_at": clock.naive_utc(clock.now()),
            "prev_artifact_id": prev["artifact_id"] if prev else None,
        }
        clock.advance(timedelta(milliseconds=1))  # keep strict ordering
        signature = self.signer.sign(_payload(row))
        self.wh.execute(
            "INSERT INTO meta.provenance VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                row["artifact_id"],
                row["artifact_kind"],
                row["target_fqn"],
                row["content_sha256"],
                row["agent"],
                row["model_name"],
                row["prompt_id"],
                row["prompt_sha256"],
                dumps(row["input_refs"]),
                dumps(row["tests_run"]),
                dumps(row["gate_evidence"]) if row["gate_evidence"] is not None else None,
                row["approved_by"],
                row["created_at"],
                signature,
                row["prev_artifact_id"],
                a.content,
            ],
        )
        return str(row["artifact_id"])

    def approve(self, artifact_id: str, approver: str) -> str:
        """Approval is a new ledger row (append-only) that re-signs the same content."""
        rows = self.wh.rows("SELECT * FROM meta.provenance WHERE artifact_id = ?", [artifact_id])
        if not rows:
            raise KeyError(artifact_id)
        r = rows[0]
        return self.record(
            Artifact(
                artifact_kind=str(r["artifact_kind"]),
                target_fqn=str(r["target_fqn"]),
                content=str(r["content"]),
                agent=str(r["agent"]),
                model_name=str(r["model_name"]),
                prompt_id=r["prompt_id"],
                prompt_sha256=r["prompt_sha256"],
                input_refs={**(loads(r["input_refs"]) or {}), "approves": artifact_id},
                tests_run=loads(r["tests_run"]) or {},
                gate_evidence=loads(r["gate_evidence"]),
                approved_by=approver,
            )
        )

    def get(self, artifact_id: str) -> dict[str, Any] | None:
        rows = self.wh.rows("SELECT * FROM meta.provenance WHERE artifact_id = ?", [artifact_id])
        return rows[0] if rows else None

    def public_view(self, row: dict[str, Any]) -> dict[str, Any]:
        out = {k: row.get(k) for k in [*SIGNED_FIELDS, "signature"]}
        for k in ("input_refs", "tests_run", "gate_evidence"):
            out[k] = loads(out[k]) if isinstance(out[k], str) else out[k]
        out["created_at"] = clock.iso(row["created_at"].replace(tzinfo=UTC))
        return out

    def verify(self) -> dict[str, Any]:
        """Recompute every signature and walk every chain."""
        rows = self.wh.rows("SELECT * FROM meta.provenance ORDER BY created_at, artifact_id")
        by_id = {r["artifact_id"]: r for r in rows}
        bad_sigs = [
            r["artifact_id"] for r in rows if not self.signer.verify(_payload(r), str(r["signature"]))
        ]
        content_mismatch = [
            r["artifact_id"]
            for r in rows
            if r["content"] is not None and sha256(str(r["content"])) != r["content_sha256"]
        ]
        chain_breaks: list[str] = []
        heads: dict[tuple[str, str], list[str]] = {}
        referenced: dict[str, str] = {}
        for r in rows:
            prev = r["prev_artifact_id"]
            key = (str(r["target_fqn"]), str(r["artifact_kind"]))
            if prev is None:
                heads.setdefault(key, []).append(str(r["artifact_id"]))
                continue
            p = by_id.get(prev)
            if p is None or (p["target_fqn"], p["artifact_kind"]) != key or p["created_at"] > r["created_at"]:
                chain_breaks.append(str(r["artifact_id"]))
            if prev in referenced:  # fork: two rows claim the same predecessor
                chain_breaks.append(str(r["artifact_id"]))
            referenced[str(prev)] = str(r["artifact_id"])
        for roots in heads.values():
            if len(roots) > 1:
                chain_breaks.extend(roots[1:])
        ok = not bad_sigs and not chain_breaks and not content_mismatch
        return {
            "ok": ok,
            "records": len(rows),
            "bad_signatures": bad_sigs,
            "chain_breaks": sorted(set(chain_breaks)),
            "content_mismatch": content_mismatch,
        }

    def verify_artifacts(self, artifact_ids: list[str]) -> dict[str, Any]:
        """Verify specific artifacts and the chain behind each of them."""
        bad: list[str] = []
        broken: list[str] = []
        for aid in artifact_ids:
            cur = self.get(aid)
            while cur is not None:
                if not self.signer.verify(_payload(cur), str(cur["signature"])):
                    bad.append(str(cur["artifact_id"]))
                prev_id = cur["prev_artifact_id"]
                if prev_id is None:
                    break
                prev = self.get(str(prev_id))
                if prev is None or prev["target_fqn"] != cur["target_fqn"]:
                    broken.append(str(cur["artifact_id"]))
                    break
                cur = prev
        return {"signatures_valid": not bad, "chain_intact": not broken, "bad": bad, "broken": broken}
