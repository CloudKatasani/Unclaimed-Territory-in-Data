"""Pipeline Builder (A1): approved contract -> validated model SQL + contract-derived tests in a draft schema."""

from __future__ import annotations

from typing import Any

import sqlglot
from pydantic import BaseModel
from sqlglot import exp

from tessera import clock, ids
from tessera.config import Settings
from tessera.governance import contracts as contracts_mod
from tessera.governance.contract_tests import TestResult, derive_test_names, run_contract_tests
from tessera.governance.lineage import output_columns, referenced_tables
from tessera.governance.policy import PolicyEngine, Principal, SQLRejected, guard_generated_sql
from tessera.governance.property_tests import build_property_schema
from tessera.governance.provenance import Artifact, ProvenanceLedger
from tessera.jsonutil import dumps, loads
from tessera.llm.client import LLM, LLMResult
from tessera.orchestrator.bus import Bus
from tessera.publisher.promote import rewrite_tables
from tessera.warehouse.base import Warehouse

AGENT = "pipeline_builder@0.1.0"
SERVICE = Principal("pipeline_builder", "agent", None)
SAMPLE_ROWS = 5  # <= 20 per docs/01 trust boundary


class ModelSQL(BaseModel):
    sql: str


class BuildFailure(RuntimeError):
    pass


class PipelineBuilder:
    def __init__(
        self,
        wh: Warehouse,
        llm: LLM,
        policy: PolicyEngine,
        ledger: ProvenanceLedger,
        bus: Bus,
        settings: Settings,
    ) -> None:
        self.wh = wh
        self.llm = llm
        self.policy = policy
        self.ledger = ledger
        self.bus = bus
        self.settings = settings

    # -- job bookkeeping ---------------------------------------------------------------------------
    def _job(self, job_id: str) -> dict[str, Any]:
        return self.wh.rows("SELECT * FROM meta.build_jobs WHERE job_id = ?", [job_id])[0]

    def _status(self, job_id: str, status: str, **fields: Any) -> None:
        job = self._job(job_id)
        timeline = loads(job["timeline_json"]) or []
        timeline.append({"status": status, "at": clock.iso(clock.now())})
        sets = ["status = ?", "timeline_json = ?"]
        params: list[Any] = [status, dumps(timeline)]
        for k, v in fields.items():
            sets.append(f"{k} = ?")
            params.append(v if isinstance(v, str) or v is None else dumps(v))
        self.wh.execute(f"UPDATE meta.build_jobs SET {', '.join(sets)} WHERE job_id = ?", [*params, job_id])

    def get(self, job_id: str) -> dict[str, Any]:
        job = dict(self._job(job_id))
        for k in ("timeline_json", "tests_json", "attempts_json", "artifact_ids"):
            job[k] = loads(job[k])
        return job

    # -- inputs ------------------------------------------------------------------------------------
    def source_context(self, contract: contracts_mod.Contract) -> dict[str, Any]:
        pii = {(t, c) for (t, c) in _pii(self.wh)}
        schemas = {}
        samples = {}
        for src in contract.sources:
            cols = self.wh.table_schema(src)
            schemas[src] = [{"name": c.name, "type": c.data_type} for c in cols if (src, c.name) not in pii]
            order = ", ".join(c.name for c in cols[:2])
            sql, _ = self.policy.rewrite(f"SELECT * FROM {src} ORDER BY {order} LIMIT {SAMPLE_ROWS}", SERVICE)
            rows = self.wh.rows(sql)
            samples[src] = [
                {k: (str(v) if v is not None else None) for k, v in r.items() if (src, k) not in pii}
                for r in rows
            ]
        return {"schemas": schemas, "samples": samples}

    # -- validation ------------------------------------------------------------------------------
    def validate(self, sql: str, contract: contracts_mod.Contract) -> list[str]:
        try:
            stmt = guard_generated_sql(sql, ("draft",))
        except SQLRejected as exc:
            self._reject(str(exc), sql)
            return [f"rejected: {exc}"]
        errors = []
        if not isinstance(stmt, exp.Create) or str(stmt.args.get("kind", "")).upper() != "TABLE":
            errors.append("must be a single CREATE TABLE ... AS SELECT")
            return errors
        target = stmt.this if isinstance(stmt.this, exp.Table) else stmt.this.find(exp.Table)
        assert target is not None
        if f"{target.db}.{target.name}" != f"draft.{contract.table_name}":
            errors.append(f"target must be draft.{contract.table_name}")
        select_sql = stmt.expression.sql(dialect="duckdb")
        refs = referenced_tables(select_sql)
        extra = sorted(refs - set(contract.sources))
        if extra:
            errors.append(f"references tables not declared as sources: {extra}")
        outs = output_columns(select_sql)
        expected = [c.name for c in contract.columns]
        if sorted(outs) != sorted(expected):
            errors.append(f"output columns {sorted(outs)} != contract schema {sorted(expected)}")
        return errors

    def _reject(self, reason: str, sql: str) -> None:
        self.wh.execute(
            "INSERT INTO meta.rejections VALUES (?, ?, ?, ?, ?)",
            [ids.new_id(self.wh, "rejection"), AGENT, reason, sql, clock.naive_utc(clock.now())],
        )

    # -- running ---------------------------------------------------------------------------------
    def run_tests(
        self, contract: contracts_mod.Contract, select_sql: str, draft: str, job_suffix: str
    ) -> list[TestResult]:
        table = f"{draft}.{contract.table_name}"
        self.wh.execute(f"CREATE OR REPLACE TABLE {table} AS {select_sql}")
        results = run_contract_tests(self.wh, contract, table)
        prop_schema = f"prop_{job_suffix}"
        try:
            desc = build_property_schema(self.wh, contract, prop_schema)
            mapping = {s: f"{prop_schema}.{s.split('.', 1)[1]}" for s in contract.sources}
            prop_sql = rewrite_tables(select_sql, mapping)
            prop_table = f"{prop_schema}.__output"
            self.wh.execute(f"CREATE TABLE {prop_table} AS {prop_sql}")
            prop = run_contract_tests(self.wh, contract, prop_table, prefix="property.", ref_map=mapping)
            n_cases = len(desc["cases"])
            prop = [r for r in prop if not r.name.startswith("property.schema.columns")]
            results.append(
                TestResult(
                    "property.synthetic_cases",
                    "property",
                    n_cases > 0,
                    f"{n_cases} synthetic edge cases over {desc['synthetic_sources']}",
                )
            )
            results += [TestResult(r.name, "property", r.passed, r.detail, r.violations) for r in prop]
        except Exception as exc:  # noqa: BLE001
            results.append(TestResult("property.run", "property", False, f"error: {exc}"))
        finally:
            self.wh.drop_schema(prop_schema)
        return results

    def build(self, contract_id: str) -> str:
        contract, row = contracts_mod.load(self.wh, contract_id)
        if row["status"] != "approved":
            raise BuildFailure(f"contract {contract_id} is {row['status']}")
        job_id = ids.new_id(self.wh, "build")
        suffix = job_id.lower()
        draft = f"draft_{suffix}"
        self.wh.execute(
            "INSERT INTO meta.build_jobs VALUES (?, ?, ?, 'drafted', ?, NULL, NULL, '[]', '[]', NULL, ?)",
            [
                job_id,
                contract_id,
                contract.product,
                dumps([{"status": "drafted", "at": clock.iso(clock.now())}]),
                clock.naive_utc(clock.now()),
            ],
        )
        self._status(job_id, "generating")
        ctx = self.source_context(contract)
        variables: dict[str, Any] = {
            "contract_yaml": contract.to_yaml(),
            "target": f"draft.{contract.table_name}",
            **ctx,
        }
        res: LLMResult[ModelSQL] = self.llm.complete_json("build.model", variables, ModelSQL)
        attempts: list[dict[str, Any]] = []
        self.wh.execute(f"CREATE SCHEMA IF NOT EXISTS {draft}")
        passed = False
        results: list[TestResult] = []
        sql = res.value.sql
        for attempt in range(self.settings.max_repairs + 1):
            self._status(job_id, "testing")
            errors = self.validate(sql, contract)
            if not errors:
                select_sql = _select_part(sql)
                try:
                    results = self.run_tests(contract, select_sql, draft, suffix)
                except Exception as exc:  # noqa: BLE001 - execution error is a failure the repair loop sees
                    results = [TestResult("execute", "schema", False, f"error: {exc}")]
            else:
                results = [TestResult("validate", "schema", False, e) for e in errors]
            failures = [r.as_dict() for r in results if not r.passed]
            attempts.append(
                {
                    "attempt": attempt,
                    "sql": sql,
                    "prompt_id": res.prompt_id,
                    "source": res.source,
                    "failures": failures,
                    "passed": not failures,
                }
            )
            if not failures:
                passed = True
                break
            if attempt == self.settings.max_repairs:
                break
            self._status(job_id, "repairing", attempts_json=attempts)
            res = self.llm.complete_json(
                "build.repair",
                {
                    "contract_yaml": contract.to_yaml(),
                    "target": f"draft.{contract.table_name}",
                    "sql": sql,
                    "failures": failures,
                },
                ModelSQL,
            )
            sql = res.value.sql
        tests = [r.as_dict() for r in results]
        if not passed:
            self._status(
                job_id,
                "failed",
                sql=sql,
                tests_json=tests,
                attempts_json=attempts,
                error="contract tests failing after repairs",
            )
            return job_id
        select_sql = _select_part(sql)
        approver = row["approved_by"]
        tests_run = {t["name"]: {"passed": t["passed"], "detail": t["detail"]} for t in tests}
        model_art = self.ledger.record(
            Artifact(
                "model_sql",
                contract.product,
                select_sql,
                agent=AGENT,
                model_name=res.model_name,
                prompt_id=res.prompt_id,
                prompt_sha256=res.prompt_sha256,
                input_refs={
                    "contract_id": contract_id,
                    "sources": contract.sources,
                    "job_id": job_id,
                    "repairs": len(attempts) - 1,
                },
                tests_run=tests_run,
                approved_by=approver,
            )
        )
        test_art = self.ledger.record(
            Artifact(
                "test",
                contract.product,
                dumps(derive_test_names(contract)),
                agent=AGENT,
                model_name="deterministic",
                input_refs={"contract_id": contract_id, "job_id": job_id},
                tests_run=tests_run,
                approved_by=approver,
            )
        )
        self._status(
            job_id,
            "gate",
            sql=select_sql,
            tests_json=tests,
            attempts_json=attempts,
            artifact_ids=[model_art, test_art],
        )
        self.bus.emit("build.ready_for_gate", {"job_id": job_id, "contract_id": contract_id, "draft": draft})
        return job_id


def _select_part(sql: str) -> str:
    stmt = sqlglot.parse_one(sql, dialect="duckdb")
    if isinstance(stmt, exp.Create):
        return str(stmt.expression.sql(dialect="duckdb", pretty=True))
    return str(stmt.sql(dialect="duckdb", pretty=True))


def _pii(wh: Warehouse) -> list[tuple[str, str]]:
    return [
        (str(r["fqn"]), str(r["column_name"]))
        for r in wh.rows("SELECT fqn, column_name FROM meta.catalog_columns WHERE classification = 'PII'")
    ]
