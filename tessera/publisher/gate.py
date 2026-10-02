"""Publisher: the only component that writes to `dp`. Promotes builds and patches after their gate."""

from __future__ import annotations

from typing import Any

from tessera import clock
from tessera.governance import contracts as contracts_mod
from tessera.governance.contract_tests import TestResult, quality_score
from tessera.governance.lineage import Lineage
from tessera.governance.provenance import ProvenanceLedger
from tessera.governance.semantic import SemanticModel
from tessera.jsonutil import dumps, loads
from tessera.orchestrator.bus import Bus
from tessera.orchestrator.notify import record_metric
from tessera.publisher.promote import swap_in
from tessera.publisher.refresh import register_model
from tessera.seed.domain import UNRESTRICTED_ROLES
from tessera.warehouse.base import Warehouse

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

    def products(self) -> list[dict[str, Any]]:
        return self.wh.rows("SELECT * FROM meta.products ORDER BY fqn")
