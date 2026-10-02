"""Publisher: the only component that writes to `dp`. Promotes builds and patches after their gate."""

from __future__ import annotations

from typing import Any

from tessera import clock
from tessera.governance import contracts as contracts_mod
from tessera.governance import ingestion
from tessera.governance.contract_tests import TestResult, quality_score
from tessera.governance.lineage import Lineage
from tessera.governance.provenance import Artifact, ProvenanceLedger
from tessera.governance.semantic import SemanticModel
from tessera.jsonutil import dumps, loads
from tessera.orchestrator.bus import Bus
from tessera.orchestrator.notify import record_metric
from tessera.publisher.promote import materialize, swap_in
from tessera.publisher.refresh import hold_product, refresh_product, register_model
from tessera.seed.domain import UNRESTRICTED_ROLES
from tessera.seed.generate import snapshot_lkg
from tessera.warehouse.base import Warehouse

AUTO_GATE = "auto-gate"
TIME_TYPES = {"timestamp": "timestamp", "date": "day"}


class GateRejected(RuntimeError):
    pass


class Publisher:
    def __init__(
        self, wh: Warehouse, lineage: Lineage, ledger: ProvenanceLedger, semantic: SemanticModel, bus: Bus
    ) -> None:
        self.wh = wh
        self.lineage = lineage
        self.ledger = ledger
        self.semantic = semantic
        self.bus = bus

    # -- builds --------------------------------------------------------------------------------------
    def gate_build(self, job_id: str) -> str:
        job = self.wh.rows("SELECT * FROM meta.build_jobs WHERE job_id = ?", [job_id])[0]
        contract, row = contracts_mod.load(self.wh, str(job["contract_id"]))
        tests = loads(job["tests_json"]) or []
        failing = [t["name"] for t in tests if not t["passed"]]
        if row["status"] != "approved":
            raise GateRejected(f"contract is {row['status']}, not approved")
        if job["status"] != "gate" or failing or not tests:
            raise GateRejected(f"build {job_id} not ready: status={job['status']} failing={failing}")
        draft = f"draft_{job_id.lower()}"
        fqn = contract.product
        self.wh.execute("CREATE SCHEMA IF NOT EXISTS dp")
        swap_in(self.wh, f"{draft}.{contract.table_name}", fqn)
        model_art, _test_art = loads(job["artifact_ids"])
        register_model(self.wh, fqn, "table", str(job["sql"]), model_art)
        self.lineage.rebuild_for(fqn, str(job["sql"]), model_art)
        self._register_semantics(contract)
        self._register_policies(contract)
        self.wh.execute("DELETE FROM meta.criticality WHERE fqn = ?", [fqn])
        self.wh.execute(
            "INSERT INTO meta.criticality VALUES (?, ?, ?)",
            [fqn, contract.criticality, f"declared in contract v{contract.version}"],
        )
        main = [TestResult(t["name"], t["kind"], t["passed"]) for t in tests if t["kind"] != "property"]
        self.wh.execute("DELETE FROM meta.products WHERE fqn = ?", [fqn])
        self.wh.execute(
            "INSERT INTO meta.products VALUES (?, ?, ?, ?, ?, ?, ?, '[]', 'ok', NULL)",
            [
                fqn,
                contract.title,
                contract.owner,
                job["contract_id"],
                clock.naive_utc(clock.now()),
                contract.quality.freshness_sla_hours,
                quality_score(main),
            ],
        )
        contracts_mod.set_status(self.wh, str(job["contract_id"]), "published")
        self.wh.drop_schema(draft)
        timeline = loads(job["timeline_json"]) + [{"status": "published", "at": clock.iso(clock.now())}]
        self.wh.execute(
            "UPDATE meta.build_jobs SET status = 'published', timeline_json = ? WHERE job_id = ?",
            [dumps(timeline), job_id],
        )
        record_metric(
            self.wh,
            "a1.build_published",
            1,
            {"job_id": job_id, "product": fqn, "repairs": len(loads(job["attempts_json"])) - 1},
        )
        self.semantic.refresh()
        self.bus.emit(
            "product.published",
            {
                "fqn": fqn,
                "contract_id": job["contract_id"],
                "intent_id": contract.demand_intent_id,
                "restated": False,
                "job_id": job_id,
            },
        )
        return fqn

    def _register_semantics(self, contract: contracts_mod.Contract) -> None:
        fqn = contract.product
        self.wh.execute("DELETE FROM meta.semantic_model WHERE product_fqn = ?", [fqn])
        sem = contract.semantic
        if sem is None:
            return
        self.semantic.add_element(
            "entity",
            sem.entity,
            fqn,
            sem.entity,
            description=contract.title,
            synonyms=[contract.title.lower()],
        )
        for m in sem.measures:
            self.semantic.add_element(
                "measure",
                m.name,
                fqn,
                m.expression,
                certified=m.certified,
                certified_by=contract.owner if m.certified else None,
                synonyms=sem.synonyms.get(m.name, []),
                description=contract.description,
            )
        for d in sem.dimensions:
            col = contract.column(d)
            grain = TIME_TYPES.get(col.type.lower()) if col else None
            base = d.removesuffix("_id").replace("_", " ")
            syns = sem.synonyms.get(d, [base, base + "s"] if not grain else [base])
            self.semantic.add_element("dimension", d, fqn, d, grain=grain, synonyms=syns)

    def _register_policies(self, contract: contracts_mod.Contract) -> None:
        fqn = contract.product
        self.wh.execute("DELETE FROM meta.policies WHERE target_fqn = ?", [fqn])
        for i, p in enumerate(contract.policies, start=1):
            pid = f"pol_{contract.table_name}_{i}"
            if p.kind == "row_filter" and p.expression:
                attr = p.expression.split(":user.", 1)[1].split()[0] if ":user." in p.expression else "region"
                self.wh.execute(
                    "INSERT INTO meta.policies VALUES (?, 'row_filter', ?, ?, ?, NULL, ?, NULL)",
                    [pid, fqn, attr, p.expression, dumps(UNRESTRICTED_ROLES)],
                )
            elif p.kind == "column_mask" and p.column:
                self.wh.execute(
                    "INSERT INTO meta.policies VALUES (?, 'column_mask', ?, NULL, ?, 'PII', ?, ?)",
                    [pid, fqn, p.expression or "'***'", dumps(["admin"]), p.column],
                )

    # -- patches -------------------------------------------------------------------------------------
    def patch(self, job_id: str) -> dict[str, Any]:
        rows = self.wh.rows("SELECT * FROM meta.patches WHERE job_id = ?", [job_id])
        if not rows:
            raise KeyError(job_id)
        out = dict(rows[0])
        for k in ("evidence_json", "merged_products", "held_products"):
            out[k] = loads(out[k])
        return out

    def gate_patch(self, job_id: str) -> dict[str, Any]:
        """Apply a healed ingestion mapping if every affected object is under its threshold.

        Criticality <= 3 products refresh immediately (approved_by = auto-gate). Criticality-4 products
        stay held until a human approves; until then certificate rule 4 blocks answers on their path.
        """
        p = self.patch(job_id)
        ev = p["evidence_json"]
        sel = next(c for c in ev["candidates"] if c["candidate_no"] == p["selected_candidate"])
        over = [f for f, o in sel["objects"].items() if not o["passed"]]
        if over:
            raise GateRejected(f"objects over threshold: {over}")
        cand = self.wh.rows(
            "SELECT * FROM meta.patch_candidates WHERE job_id = ? AND candidate_no = ?",
            [job_id, p["selected_candidate"]],
        )[0]
        target = str(p["target_fqn"])
        patched_sql = str(cand["patch_sql"])
        crit = {str(r["fqn"]): int(r["criticality"]) for r in self.wh.rows("SELECT * FROM meta.criticality")}
        affected = sorted(sel["objects"])
        with self.wh.transaction():
            materialize(self.wh, target, patched_sql, "view")
            artifact = self.ledger.record(
                Artifact(
                    "patch",
                    target,
                    patched_sql,
                    agent="drift_healer@0.1.0",
                    model_name="mock" if cand["source"] == "llm" else "deterministic",
                    prompt_id="drift.patch" if cand["source"] == "llm" else None,
                    input_refs={
                        "job_id": job_id,
                        "event_id": p["event_id"],
                        "candidate": p["selected_candidate"],
                        "label": cand["label"],
                    },
                    tests_run={
                        f: {"passed": o["passed"], "divergence": o["divergence"], "threshold": o["threshold"]}
                        for f, o in sel["objects"].items()
                    },
                    gate_evidence={
                        "candidates": [
                            {k: c[k] for k in ("candidate_no", "label", "max_divergence", "passed")}
                            for c in ev["candidates"]
                        ],
                        "selected": p["selected_candidate"],
                        "objects": {
                            f: {
                                "divergence": o["divergence"],
                                "threshold": o["threshold"],
                                "criticality": o["criticality"],
                            }
                            for f, o in sel["objects"].items()
                        },
                    },
                    approved_by=AUTO_GATE,
                )
            )
            register_model(self.wh, target, "view", patched_sql, artifact)
            self.lineage.rebuild_for(target, patched_sql, artifact)
        merged = [f for f in affected if crit.get(f, 2) <= 3]
        held = [f for f in affected if crit.get(f, 2) >= 4]
        for f in merged:
            refresh_product(self.wh, f)
        for f in held:
            hold_product(self.wh, f, f"awaiting human approval of patch {job_id}")
        ingestion.snapshot(self.wh, [str(ev["blast_radius"]["source"])])
        status = "awaiting_approval" if held else "merged"
        self.wh.execute(
            "UPDATE meta.patches SET status = ?, artifact_id = ?, merged_products = ?, held_products = ?,"
            " decided_at = ? WHERE job_id = ?",
            [status, artifact, dumps(merged), dumps(held), clock.naive_utc(clock.now()), job_id],
        )
        self.wh.execute(
            "UPDATE meta.drift_events SET status = ? WHERE event_id = ?",
            ["healed" if not held else "awaiting_approval", p["event_id"]],
        )
        record_metric(self.wh, "id2.refreshes_held", len(held), {"job_id": job_id, "products": held})
        record_metric(self.wh, "id2.refreshes_corrupted", 0, {"job_id": job_id})
        for f in merged:
            self.bus.emit(
                "product.published",
                {"fqn": f, "restated": True, "patch_job_id": job_id, "reason": self._reason(p)},
            )
        if not held:
            snapshot_lkg(self.wh)
            self.bus.emit("patch.merged", {"job_id": job_id, "products": merged})
        else:
            self.bus.emit("patch.held", {"job_id": job_id, "products": held})
        return self.patch(job_id)

    def _reason(self, p: dict[str, Any]) -> str:
        ch = p["evidence_json"]["blast_radius"]
        cols = ", ".join(ch["source_columns"])
        when = clock.naive_utc(clock.now()).strftime("%-d %b")
        return f"vendor feed field rename ({cols}), corrected {when}"

    def approve_patch(self, job_id: str, approver: str) -> dict[str, Any]:
        p = self.patch(job_id)
        if p["status"] != "awaiting_approval":
            raise GateRejected(f"patch {job_id} is {p['status']}")
        approved = self.ledger.approve(str(p["artifact_id"]), approver)
        held = list(p["held_products"])
        for f in held:
            refresh_product(self.wh, f)
        self.wh.execute(
            "UPDATE meta.patches SET status = 'merged', approved_by = ?, artifact_id = ?, "
            "merged_products = ?, held_products = '[]', decided_at = ? WHERE job_id = ?",
            [
                approver,
                approved,
                dumps(list(p["merged_products"]) + held),
                clock.naive_utc(clock.now()),
                job_id,
            ],
        )
        self.wh.execute("UPDATE meta.drift_events SET status = 'healed' WHERE event_id = ?", [p["event_id"]])
        snapshot_lkg(self.wh)
        for f in held:
            self.bus.emit(
                "product.published",
                {
                    "fqn": f,
                    "restated": True,
                    "patch_job_id": job_id,
                    "reason": self._reason(p),
                    "approved_by": approver,
                },
            )
        self.bus.emit(
            "patch.merged",
            {"job_id": job_id, "products": list(p["merged_products"]) + held, "approved_by": approver},
        )
        return self.patch(job_id)

    def reject_patch(self, job_id: str, approver: str) -> dict[str, Any]:
        self.wh.execute(
            "UPDATE meta.patches SET status = 'rejected', approved_by = ?, decided_at = ? WHERE job_id = ?",
            [approver, clock.naive_utc(clock.now()), job_id],
        )
        self.bus.emit("patch.rejected", {"job_id": job_id, "by": approver})
        return self.patch(job_id)

    def products(self) -> list[dict[str, Any]]:
        return self.wh.rows("SELECT * FROM meta.products ORDER BY fqn")
