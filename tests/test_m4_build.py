"""M4 acceptance: Pipeline Builder + Publisher (A1)."""

from __future__ import annotations

import sqlglot

from tessera.governance.lineage import referenced_tables
from tessera.platform import Tessera
from tests.storyline import mine_and_draft


def _approve_and_run(app: Tessera) -> tuple[str, str, str]:
    intent_id, cid = mine_and_draft(app)
    app.approvals.approve_contract(cid, "raj")
    app.bus.drain()
    job = app.wh.rows("SELECT job_id FROM meta.build_jobs WHERE contract_id = ?", [cid])[0]["job_id"]
    return intent_id, cid, str(job)


def test_build_sql_valid_and_only_declared_sources(app: Tessera) -> None:
    _, _, job_id = _approve_and_run(app)
    job = app.builder.get(job_id)
    assert job["status"] == "published"
    sqlglot.parse_one(job["sql"], dialect="duckdb")
    assert referenced_tables(job["sql"]) <= {
        "raw.meters",
        "raw.feeders",
        "raw.outage_events",
        "raw.ami_interval_reads",
    }


def test_derived_tests_cover_every_kind(app: Tessera) -> None:
    _, _, job_id = _approve_and_run(app)
    tests = app.builder.get(job_id)["tests_json"]
    names = {t["name"] for t in tests}
    kinds = {t["kind"] for t in tests}
    assert {"schema", "key", "expectation", "referential", "property"} <= kinds
    for e in ("window_valid", "kwh_non_negative", "zero_flag_consistent", "reads_non_negative"):
        assert f"expect.{e}" in names and f"property.expect.{e}" in names
    assert {"fk.meter_id", "fk.outage_id", "unique.meter_id+outage_id", "schema.types"} <= names
    assert all(t["passed"] for t in tests)


def test_bad_first_sql_triggers_exactly_one_repair(app: Tessera) -> None:
    _, _, job_id = _approve_and_run(app)
    attempts = app.builder.get(job_id)["attempts_json"]
    assert len(attempts) == 2
    assert not attempts[0]["passed"] and attempts[1]["passed"]
    failed = {f["name"] for f in attempts[0]["failures"]}
    assert "schema.types" in failed


def test_publish_promotes_rebuilds_lineage_and_records_provenance(app: Tessera) -> None:
    _, _, job_id = _approve_and_run(app)
    assert app.wh.table_exists("dp.meter_outage_exposure")
    assert not any(
        s.startswith("draft_")
        for s in [
            r["schema_name"] for r in app.wh.rows("SELECT schema_name FROM information_schema.schemata")
        ]
    )
    up = set(app.lineage.upstream_columns("dp.meter_outage_exposure", ["kwh_in_window"]))
    assert "raw.ami_interval_reads.kwh_delivered" in up
    prov = app.wh.rows(
        "SELECT * FROM meta.provenance WHERE target_fqn = 'dp.meter_outage_exposure' "
        "AND artifact_kind IN ('model_sql', 'test')"
    )
    assert len(prov) == 2 and all(p["approved_by"] == "raj" for p in prov)
    assert all(p["agent"] == "pipeline_builder@0.1.0" for p in prov)
    assert app.ledger.verify()["ok"]


def test_replay_resolves_all_three_questions(app: Tessera) -> None:
    intent_id, _, _ = _approve_and_run(app)
    assert app.demand.closure(intent_id) == {"resolved": 3, "total": 3}
    assert app.demand.intent(intent_id)["status"] == "closed"
    notes = app.wh.rows("SELECT user_id FROM meta.notifications WHERE kind = 'question_resolved' ORDER BY 1")
    assert [n["user_id"] for n in notes] == ["alice", "deshawn", "maria"]
    ans = app.answer.ask("Which meters reported zero usage during outages last month?", "alice")
    assert ans.outcome == "answered" and ans.rows
    cert = ans.certificate
    assert cert is not None
    authored = [a for a in cert["agent_authored"] if a["target_fqn"] == "dp.meter_outage_exposure"]
    assert authored and authored[0]["approved_by"] == "raj"
    assert cert["verdict"] == "release_with_warning"  # new product's measures are not certified yet
    assert all(r.get("region", "NORTH") == "NORTH" for r in ans.rows)
