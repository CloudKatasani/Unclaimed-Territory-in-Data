"""M2 acceptance: certified answers with signed trust certificates (ID-3)."""

from __future__ import annotations

from tessera.governance.provenance import Artifact
from tessera.platform import Tessera

Q1 = "What was SAIDI by substation in my region last month?"


def test_storyline_step1_certified_answer(app: Tessera) -> None:
    ans = app.answer.ask(Q1, "alice")
    assert ans.outcome == "answered"
    assert ans.columns == ["substation_id", "saidi"]
    assert {r["substation_id"] for r in ans.rows} == {"S-01", "S-02", "S-03", "S-04"}  # NORTH only
    admin = app.answer.ask(Q1, "admin")
    assert len({r["substation_id"] for r in admin.rows}) == 12
    cert = ans.certificate
    assert cert is not None
    assert cert["verdict"] == "release"
    assert [m["name"] for m in cert["metrics"]] == ["saidi"] and cert["metrics"][0]["certified"]
    assert any(d["sentence"] == "Filtered to region NORTH for alice" for d in cert["policy_decisions"])
    assert cert["freshness"]["dp.outage_reliability"]["pass"]
    assert cert["agent_share_unapproved"] == 0.0
    assert cert["agent_authored"] == []
    assert {b["status"] for b in cert["built_by"]} == {"human"}
    assert "raw.outage_events.customers_affected" in cert["lineage_nodes"]
    v = app.certs.verify(cert["cert_id"])
    assert v["signature_valid"] and v["valid"]
    q = app.wh.rows("SELECT * FROM meta.questions WHERE question_id = ?", [ans.question_id])[0]
    assert q["outcome"] == "answered" and q["cert_id"] == cert["cert_id"]


def test_certificate_tamper_detected(app: Tessera) -> None:
    ans = app.answer.ask(Q1, "alice")
    assert ans.certificate
    cid = ans.certificate["cert_id"]
    app.wh.execute("UPDATE meta.certificates SET verdict = 'release_with_warning' WHERE cert_id = ?", [cid])
    assert not app.certs.verify(cid)["signature_valid"]


def test_unknown_measure_logged_missing_data(app: Tessera) -> None:
    ans = app.answer.ask("What is the average transformer oil temperature by feeder?", "alice")
    assert ans.outcome == "missing_data"
    assert ans.unmatched and ans.certificate is None
    row = app.wh.rows("SELECT * FROM meta.questions WHERE question_id = ?", [ans.question_id])[0]
    assert row["outcome"] == "missing_data"
    assert "transformer" in row["plan_json"]


def test_zero_usage_question_is_missing_data(app: Tessera) -> None:
    ans = app.answer.ask("Which meters reported zero usage during outages last month?", "alice")
    assert ans.outcome == "missing_data"
    assert "zero usage" in ans.unmatched


def test_unapproved_agent_artifact_blocks_criticality4(app: Tessera) -> None:
    app.ledger.record(
        Artifact(
            "model_sql",
            "raw.ami_interval_reads",
            "SELECT * FROM raw.head_end_vendor_feed",
            agent="pipeline_builder@0.1.0",
            model_name="mock",
            approved_by=None,
        )
    )
    ans = app.answer.ask(Q1, "alice")
    assert ans.certificate and ans.certificate["verdict"] == "block"
    assert any(t["rule"] == 4 for t in ans.certificate["rule_trace"])
    assert ans.withheld and ans.rows == []
    assert ans.certificate["agent_share_unapproved"] > 0
    # criticality-3 product on the same path is not blocked by rule 4
    daily = app.answer.ask("Daily kWh for feeder F-017 last week", "alice")
    assert daily.certificate and daily.certificate["verdict"] != "block"
