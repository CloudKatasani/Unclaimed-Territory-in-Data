"""Recall job (idea 1): restated data -> re-execute affected answers -> notify consumers above materiality."""

from __future__ import annotations

from datetime import UTC
from typing import Any

from tessera import clock, ids
from tessera.agents.answer import result_hash, serialise
from tessera.governance.policy import PolicyEngine, load_principal
from tessera.governance.semantic import Plan, SemanticModel, compile_plan
from tessera.jsonutil import dumps, loads
from tessera.orchestrator.notify import notify, record_metric
from tessera.warehouse.base import Warehouse

DEFAULT_MATERIALITY_PCT = 1.0
DIM_LABELS = {
    "substation_id": "substation",
    "feeder_id": "feeder",
    "meter_id": "meter",
    "region": "region",
    "month": "month",
    "read_date": "day",
    "outage_id": "outage",
}
UNITS = {"saidi": " minutes", "caidi": " minutes", "saidi_minutes": " minutes"}


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, int | float) and not isinstance(v, bool) else None


def diff_results(
    old: list[dict[str, Any]], new: list[dict[str, Any]], dims: list[str], metrics: list[str]
) -> dict[str, Any]:
    """Cell-level diff keyed on the plan's dimensions."""

    def key(r: dict[str, Any]) -> tuple[Any, ...]:
        return tuple(r.get(d) for d in dims)

    o = {key(r): r for r in old}
    n = {key(r): r for r in new}
    cells: list[dict[str, Any]] = []
    max_pct = 0.0
    for k in sorted(set(o) | set(n), key=lambda x: tuple(str(i) for i in x)):
        if k not in o or k not in n:
            cells.append({"key": dict(zip(dims, k, strict=True)), "change": "added" if k in n else "removed"})
            max_pct = max(max_pct, 100.0)
            continue
        for m in metrics:
            a, b = _num(o[k].get(m)), _num(n[k].get(m))
            if a is None or b is None:
                if o[k].get(m) != n[k].get(m):
                    cells.append(
                        {
                            "key": dict(zip(dims, k, strict=True)),
                            "metric": m,
                            "old": o[k].get(m),
                            "new": n[k].get(m),
                            "pct": 100.0,
                        }
                    )
                    max_pct = max(max_pct, 100.0)
                continue
            if abs(a - b) < 1e-9:
                continue
            pct = abs(b - a) / max(abs(a), 1e-9) * 100
            cells.append(
                {
                    "key": dict(zip(dims, k, strict=True)),
                    "metric": m,
                    "old": a,
                    "new": b,
                    "pct": round(pct, 4),
                }
            )
            max_pct = max(max_pct, pct)
    cells.sort(key=lambda c: -float(c.get("pct", 100.0)))
    return {"cells": cells, "max_pct": round(max_pct, 4)}


class RecallJob:
    def __init__(self, wh: Warehouse, semantic: SemanticModel, policy: PolicyEngine) -> None:
        self.wh = wh
        self.semantic = semantic
        self.policy = policy
        self.sentinel_keys: dict[str, str] = {}

    def materiality(self, consumer: str) -> float:
        v = self.wh.scalar(
            "SELECT materiality_pct FROM meta.consumer_settings WHERE consumer = ?", [consumer]
        )
        return float(v) if v is not None else DEFAULT_MATERIALITY_PCT

    def affected(self, fqn: str) -> list[dict[str, Any]]:
        rows = self.wh.rows(
            "SELECT s.*, c.lineage_nodes FROM meta.answer_snapshots s JOIN meta.certificates c USING (cert_id) "
            "WHERE s.served_at <= ? ORDER BY s.served_at, s.cert_id",
            [clock.naive_utc(clock.now())],
        )
        return [r for r in rows if any(str(n).startswith(fqn + ".") for n in loads(r["lineage_nodes"]) or [])]

    def reexecute(self, snap: dict[str, Any]) -> list[dict[str, Any]]:
        plan = Plan.model_validate(loads(snap["plan_json"]))
        served = snap["served_at"].replace(tzinfo=UTC)
        sql = compile_plan(self.semantic, plan, now=served)  # relative windows resolve as of serve time
        rewritten, _ = self.policy.rewrite(
            sql, load_principal(self.wh, str(snap["consumer"])), sentinel_keys=self.sentinel_keys
        )
        return serialise(self.wh.rows(rewritten))

    def run(self, fqn: str, reason: str) -> list[str]:
        issued = []
        checked = 0
        for snap in self.affected(fqn):
            checked += 1
            plan = loads(snap["plan_json"])
            new_rows = self.reexecute(snap)
            new_hash = result_hash(new_rows)
            if new_hash == snap["result_hash"]:
                continue
            if self.wh.scalar(
                "SELECT count(*) FROM meta.recall_notices WHERE cert_id = ? AND new_hash = ?",
                [snap["cert_id"], new_hash],
            ):
                continue
            d = diff_results(
                loads(snap["result_json"]) or [],
                new_rows,
                plan.get("dimensions", []),
                plan.get("metrics", []),
            )
            if d["max_pct"] <= self.materiality(str(snap["consumer"])):
                continue
            message = self.message(snap, d, reason)
            nid = ids.new_id(self.wh, "recall")
            self.wh.execute(
                "INSERT INTO meta.recall_notices VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
                [
                    nid,
                    snap["cert_id"],
                    snap["consumer"],
                    snap["result_hash"],
                    new_hash,
                    dumps(d),
                    reason,
                    message,
                    clock.naive_utc(clock.now()),
                ],
            )
            notify(self.wh, str(snap["consumer"]), "recall", message, nid)
            issued.append(nid)
        self.recall_sentences(fqn)
        record_metric(self.wh, "recall.checked", checked, {"fqn": fqn})
        record_metric(self.wh, "recall.notices", len(issued), {"fqn": fqn})
        return issued

    def recall_sentences(self, fqn: str) -> int:
        """Narrative sentences are recalled individually: re-run each bound sub-query on the product."""
        n = 0
        for b in self.wh.rows(
            "SELECT * FROM meta.narrative_bindings WHERE status = 'current' ORDER BY cert_id, sentence_no"
        ):
            plan_data = loads(b["plan_json"]) or {}
            if plan_data.get("product") != fqn:
                continue
            plan = Plan.model_validate(plan_data)
            snap = {"plan_json": b["plan_json"], "served_at": b["served_at"], "consumer": b["consumer"]}
            rows = self.reexecute(snap)
            if result_hash(rows) == b["result_hash"]:
                continue
            old = [r for r in self._bound_rows(b, plan)]
            d = (
                diff_results(old, rows, plan.dimensions, plan.metrics)
                if old
                else {"cells": [], "max_pct": 100.0}
            )
            if old and float(str(d["max_pct"])) <= self.materiality(str(b["consumer"])):
                continue
            self.wh.execute(
                "UPDATE meta.narrative_bindings SET status = 'restated', restated_at = ?, "
                "restated_detail = ? WHERE cert_id = ? AND sentence_no = ?",
                [clock.naive_utc(clock.now()), dumps(d), b["cert_id"], b["sentence_no"]],
            )
            n += 1
        return n

    def _bound_rows(self, b: dict[str, Any], plan: Plan) -> list[dict[str, Any]]:
        """Rows the sentence was bound to: for the main query they are in the answer snapshot."""
        if b["query_ref"] == "main":
            r = self.wh.rows(
                "SELECT result_json FROM meta.answer_snapshots WHERE cert_id = ?", [b["cert_id"]]
            )
            return list(loads(r[0]["result_json"]) or []) if r else []
        return []

    def message(self, snap: dict[str, Any], d: dict[str, Any], reason: str) -> str:
        served = snap["served_at"]
        verb = "exported" if snap["kind"] == "export" else "answered"
        when = f"{verb} {served.day} {served.strftime('%b')}"
        cell = next((c for c in d["cells"] if "metric" in c), None)
        if cell is None:
            return f'Your answer "{snap["question"]}" ({when}) was restated. Reason: {reason}.'
        what = " ".join(f"{DIM_LABELS.get(k, k)} {v}" for k, v in cell["key"].items())
        unit = UNITS.get(cell["metric"], "")
        metric = cell["metric"].upper() if len(cell["metric"]) <= 6 else cell["metric"].replace("_", " ")
        subject = f"{metric} for {what}" if what else metric
        return (
            f"{subject} ({when}) changed from {cell['old']:,.1f} to {cell['new']:,.1f}{unit}. "
            f"Reason: {reason}."
        )

    def acknowledge(self, notice_id: str, user_id: str) -> None:
        self.wh.execute(
            "UPDATE meta.recall_notices SET acknowledged_at = ? WHERE notice_id = ? AND consumer = ?",
            [clock.naive_utc(clock.now()), notice_id, user_id],
        )

    def inbox(self, user_id: str) -> list[dict[str, Any]]:
        return self.wh.rows(
            "SELECT * FROM meta.recall_notices WHERE consumer = ? ORDER BY issued_at DESC", [user_id]
        )

    def open_count(self, user_id: str) -> int:
        return int(
            self.wh.scalar(
                "SELECT count(*) FROM meta.recall_notices WHERE consumer = ? AND acknowledged_at IS NULL",
                [user_id],
            )
            or 0
        )
