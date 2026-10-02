"""Trust certificates (B1 + A6): build, apply rules, sign, verify."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from tessera import clock, ids
from tessera.governance.lineage import Lineage
from tessera.governance.provenance import ProvenanceLedger, is_agent
from tessera.governance.signing import Signer
from tessera.jsonutil import canonical, dumps, loads
from tessera.warehouse.base import Warehouse

SIGNED = [
    "cert_id",
    "question_id",
    "asked_by",
    "metrics",
    "policy_decisions",
    "freshness",
    "quality",
    "lineage_nodes",
    "agent_authored",
    "agent_share_unapproved",
    "verdict",
    "rule_trace",
    "result_hash",
    "sentinel_status",
    "issued_at",
]
JSON_FIELDS = {
    "metrics",
    "policy_decisions",
    "freshness",
    "quality",
    "lineage_nodes",
    "agent_authored",
    "rule_trace",
    "sentinel_status",
}
AUTO_GATE = "auto-gate"


def _payload(cert: dict[str, Any]) -> str:
    body = {}
    for k in SIGNED:
        v = cert.get(k)
        if k in JSON_FIELDS and isinstance(v, str):
            v = loads(v)
        if isinstance(v, datetime):
            v = clock.iso(v.replace(tzinfo=UTC) if v.tzinfo is None else v)
        body[k] = v
    return canonical(body)


def evaluate_rules(cert: dict[str, Any], criticality: dict[str, int]) -> tuple[str, list[dict[str, Any]]]:
    """Certificate rules (docs/02), evaluated in order; the worst outcome wins."""
    order = {"release": 0, "release_with_warning": 1, "block": 2}
    verdict = "release"
    trace: list[dict[str, Any]] = []

    def fire(rule: int, outcome: str, reason: str) -> None:
        nonlocal verdict
        trace.append({"rule": rule, "outcome": outcome, "reason": reason})
        if order[outcome] > order[verdict]:
            verdict = outcome

    for m in cert["metrics"]:
        if not m["certified"]:
            fire(1, "release_with_warning", f"metric {m['name']} is not certified")
    for fqn, f in cert["freshness"].items():
        if not f["pass"]:
            fire(
                2, "release_with_warning", f"{fqn} last loaded {f['age_hours']}h ago (SLA {f['sla_hours']}h)"
            )
    for fqn, q in cert["quality"].items():
        if q["score"] < 0.7:
            fire(3, "block", f"{fqn} quality {q['score']} < 0.7")
        elif q["score"] < 0.9:
            fire(3, "release_with_warning", f"{fqn} quality {q['score']} < 0.9")
    products = list(cert["freshness"].keys())
    if any(criticality.get(p, 2) >= 4 for p in products):
        for a in cert["agent_authored"]:
            if a["approved_by"] in (None, AUTO_GATE):
                fire(
                    4,
                    "block",
                    f"unapproved agent artifact {a['artifact_id']} ({a['agent']}) on the path of a "
                    f"criticality-4 product",
                )
    if not trace:
        trace.append({"rule": 5, "outcome": "release", "reason": "all checks passed"})
    return verdict, trace


class CertificateService:
    def __init__(self, wh: Warehouse, lineage: Lineage, ledger: ProvenanceLedger) -> None:
        self.wh = wh
        self.lineage = lineage
        self.ledger = ledger
        self.signer = Signer(wh)

    def criticality(self) -> dict[str, int]:
        return {str(r["fqn"]): int(r["criticality"]) for r in self.wh.rows("SELECT * FROM meta.criticality")}

    def product_status(self, products: list[str]) -> tuple[dict[str, Any], dict[str, Any]]:
        fresh: dict[str, Any] = {}
        qual: dict[str, Any] = {}
        now = clock.naive_utc(clock.now())
        for fqn in products:
            rows = self.wh.rows("SELECT * FROM meta.products WHERE fqn = ?", [fqn])
            if not rows:
                continue
            p = rows[0]
            last: datetime | None = p["last_refreshed_at"]
            sla = float(p["freshness_sla_hours"] or 24)
            age = (now - last).total_seconds() / 3600 if last else 1e9
            fresh[fqn] = {
                "last_load": clock.iso(last.replace(tzinfo=UTC)) if last else None,
                "sla_hours": sla,
                "age_hours": round(age, 2),
                # a held refresh is a freshness breach: consumers are not getting current data
                "pass": age <= sla and p["refresh_status"] != "held",
                "refresh_status": p["refresh_status"],
                "held_reason": p["held_reason"],
            }
            qual[fqn] = {
                "score": float(p["quality_score"] if p["quality_score"] is not None else 1.0),
                "failing_tests": loads(p["failing_tests"]) or [],
            }
        return fresh, qual

    def path_artifacts(self, nodes: list[str]) -> list[dict[str, Any]]:
        """Latest provenance (model and patch) for every table on the lineage path."""
        tables = sorted({n.rsplit(".", 1)[0] for n in nodes})
        out = []
        for t in tables:
            for kind in ("model_sql", "patch"):
                head = self.ledger.head(t, kind)
                if head:
                    out.append(
                        {
                            "artifact_id": head["artifact_id"],
                            "artifact_kind": kind,
                            "target_fqn": t,
                            "agent": head["agent"],
                            "approved_by": head["approved_by"],
                            "is_agent": is_agent(str(head["agent"])),
                            "gate_evidence": loads(head["gate_evidence"]),
                            "created_at": clock.iso(head["created_at"].replace(tzinfo=UTC)),
                        }
                    )
        return out

    def issue(
        self,
        *,
        question_id: str,
        asked_by: str,
        metrics: list[dict[str, Any]],
        products: list[str],
        policy_decisions: list[dict[str, Any]],
        lineage_nodes: list[str],
        result_hash: str | None,
        sentinel_status: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        fresh, qual = self.product_status(products)
        arts = self.path_artifacts(lineage_nodes)
        agent_arts = [a for a in arts if a["is_agent"]]
        unapproved_tables = {a["target_fqn"] for a in agent_arts if a["approved_by"] is None}
        share = (
            sum(1 for n in lineage_nodes if n.rsplit(".", 1)[0] in unapproved_tables) / len(lineage_nodes)
            if lineage_nodes
            else 0.0
        )
        cert: dict[str, Any] = {
            "cert_id": ids.new_id(self.wh, "cert"),
            "question_id": question_id,
            "asked_by": asked_by,
            "metrics": metrics,
            "policy_decisions": policy_decisions,
            "freshness": fresh,
            "quality": qual,
            "lineage_nodes": lineage_nodes,
            "agent_authored": agent_arts,
            "agent_share_unapproved": round(share, 4),
            "result_hash": result_hash,
            "sentinel_status": sentinel_status or {},
            "issued_at": clock.naive_utc(clock.now()),
        }
        verdict, trace = evaluate_rules(cert, self.criticality())
        cert["verdict"] = verdict
        cert["rule_trace"] = trace
        cert["signature"] = self.signer.sign(_payload(cert))
        self.wh.execute(
            "INSERT INTO meta.certificates VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                cert["cert_id"],
                question_id,
                asked_by,
                dumps(metrics),
                dumps(policy_decisions),
                dumps(fresh),
                dumps(qual),
                dumps(lineage_nodes),
                dumps(agent_arts),
                cert["agent_share_unapproved"],
                verdict,
                dumps(trace),
                result_hash,
                dumps(cert["sentinel_status"]),
                cert["signature"],
                cert["issued_at"],
            ],
        )
        clock.advance(timedelta(milliseconds=1))
        self._record_sla_breaches(cert)
        return self.get(cert["cert_id"])

    def _record_sla_breaches(self, cert: dict[str, Any]) -> None:
        """SLA credits (idea 21): one ledger entry per breach episode of a product that declares credits."""
        from tessera.governance import contracts as contracts_mod

        for fqn, f in cert["freshness"].items():
            breaches = []
            if not f["pass"]:
                breaches.append(("freshness", f.get("held_reason") or f.get("last_load")))
            q = cert["quality"].get(fqn, {})
            if q and q["score"] < 0.9:
                breaches.append(("quality", ",".join(q["failing_tests"])))
            if not breaches:
                continue
            found = contracts_mod.latest_for(self.wh, fqn)
            credits = found[0].sla.credits_per_breach if found and found[0].sla else 0
            if not credits:
                continue
            for kind, episode in breaches:
                key = f"{fqn}|{kind}|{episode}"
                if self.wh.scalar("SELECT count(*) FROM meta.sla_ledger WHERE dedupe_key = ?", [key]):
                    continue
                self.wh.execute(
                    "INSERT INTO meta.sla_ledger VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        ids.new_id(self.wh, "sla"),
                        fqn,
                        cert["asked_by"],
                        kind,
                        cert["cert_id"],
                        float(credits),
                        clock.naive_utc(clock.now()),
                        key,
                    ],
                )

    def assess(self, products: list[str]) -> dict[str, Any]:
        """Unsigned status verdict for products (marketplace evidence); same rules as certificates."""
        nodes: list[str] = []
        metrics = []
        for p in products:
            nodes += self.lineage.upstream_columns(p, [c.name for c in self.wh.table_schema(p)])
            for r in self.wh.rows(
                "SELECT name, certified, certified_by FROM meta.semantic_model WHERE product_fqn = ? "
                "AND element_type IN ('metric', 'measure')",
                [p],
            ):
                metrics.append(
                    {"name": r["name"], "certified": bool(r["certified"]), "certified_by": r["certified_by"]}
                )
        fresh, qual = self.product_status(products)
        arts = self.path_artifacts(sorted(set(nodes)))
        cert = {
            "metrics": metrics,
            "freshness": fresh,
            "quality": qual,
            "agent_authored": [a for a in arts if a["is_agent"]],
        }
        verdict, trace = evaluate_rules(cert, self.criticality())
        return {"verdict": verdict, "rule_trace": trace, "freshness": fresh, "quality": qual}

    def get(self, cert_id: str) -> dict[str, Any]:
        rows = self.wh.rows("SELECT * FROM meta.certificates WHERE cert_id = ?", [cert_id])
        if not rows:
            raise KeyError(cert_id)
        r = rows[0]
        out: dict[str, Any] = {k: (loads(v) if k in JSON_FIELDS else v) for k, v in r.items()}
        out["issued_at"] = clock.iso(r["issued_at"].replace(tzinfo=UTC))
        out["built_by"] = self._built_by(out)
        out["public_key"] = self.signer.public_key_hex
        return out

    def _built_by(self, cert: dict[str, Any]) -> list[dict[str, Any]]:
        arts = cert["agent_authored"]
        out = []
        for n in cert["lineage_nodes"]:
            t = n.rsplit(".", 1)[0]
            node_arts = [a for a in arts if a["target_fqn"] == t]
            if any(a["approved_by"] is None for a in node_arts):
                status = "agent_unapproved"
            elif node_arts:
                status = "agent_approved"
            else:
                status = "human"
            out.append({"node": n, "status": status})
        return out

    def verify(self, cert_id: str) -> dict[str, Any]:
        rows = self.wh.rows("SELECT * FROM meta.certificates WHERE cert_id = ?", [cert_id])
        if not rows:
            raise KeyError(cert_id)
        r = rows[0]
        sig_ok = self.signer.verify(_payload(r), str(r["signature"]))
        arts = [a["artifact_id"] for a in loads(r["agent_authored"]) or []]
        chain = self.ledger.verify_artifacts(arts)
        return {
            "cert_id": cert_id,
            "signature_valid": sig_ok,
            "chain_intact": chain["chain_intact"],
            "artifact_signatures_valid": chain["signatures_valid"],
            "public_key": self.signer.public_key_hex,
            "valid": sig_ok and chain["chain_intact"] and chain["signatures_valid"],
        }
