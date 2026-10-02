"""M9 acceptance: sentinel records (docs/07 capability 2)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from tessera.platform import Tessera
from tests.storyline import GAP_QUESTIONS, SEED_QUESTIONS, build_exposure, drift_and_approve, fresh_app


def test_fourteen_sentinel_records_and_expectations_match(app: Tessera) -> None:
    assert len(app.sentinels.records()) == 14
    results = app.sentinels.verify_all()
    for fqn in ("dp.outage_reliability", "dp.meter_consumption_daily"):
        assert results[fqn]["passed"] and results[fqn]["checked"] > 0, results[fqn]
    matrix = app.sentinels.matrix()
    assert len(matrix["records"]) == 14
    assert all(c is None or c["passed"] for r in matrix["records"] for c in r["cells"].values())


def test_tampered_product_fails_sentinel_verification(app: Tessera) -> None:
    app.wh.execute(
        "UPDATE dp.meter_consumption_daily SET kwh_delivered = round(kwh_delivered) "
        "WHERE meter_id LIKE 'SNTL-%'"
    )
    r = app.sentinels.verify("dp.meter_consumption_daily")
    assert not r["passed"] and any(m["column"] == "kwh_delivered" for m in r["mismatches"])


@pytest.fixture(scope="module")
def storyline(template: Path) -> Iterator[Tessera]:
    app = fresh_app(template, "m9-sentinel")
    for q in SEED_QUESTIONS:
        app.answer.ask(q["text"], q["asked_by"])
    build_exposure(app)
    drift_and_approve(app)
    yield app
    app.close()


def test_lossy_candidate_fails_sentinels_before_divergence(storyline: Tessera) -> None:
    app = storyline
    assert app.sentinels.verify("dp.meter_outage_exposure")["passed"]
    job = str(app.wh.rows("SELECT job_id FROM meta.patches")[0]["job_id"])
    cands = app.publisher.patch(job)["evidence_json"]["candidates"]
    lossy = next(c for c in cands if "INTEGER" in c["expression"])
    obj = lossy["objects"]["dp.meter_outage_exposure"]
    assert not obj["passed"] and obj["divergence"] is None
    assert obj["error"].startswith("sentinel verification failed")
    assert any(m["column"] == "kwh_in_window" for m in obj["sentinel"]["mismatches"])
    selected = app.publisher.patch(job)["selected_candidate"]
    good = next(c for c in cands if c["candidate_no"] == selected)
    assert all(s["passed"] for s in good["sentinel"].values())


def test_no_served_answer_contains_a_sentinel_key(storyline: Tessera) -> None:
    app = storyline
    extra = [
        "SAIDI by feeder for the last 3 months",
        "Daily kWh by meter last week",
        "How many outages by region last month?",
        GAP_QUESTIONS[0]["text"],
    ]
    for user in ("alice", "admin", "priya"):
        for q in extra:
            app.answer.ask(q, user)
    for snap in app.wh.rows("SELECT result_json FROM meta.answer_snapshots"):
        assert "SNTL-" not in str(snap["result_json"])
    # every certificate on a sentinel-verified product records the sentinel status
    certs = app.wh.rows("SELECT sentinel_status FROM meta.certificates WHERE sentinel_status <> '{}'")
    assert certs and all(all(v["passed"] for v in json.loads(c["sentinel_status"]).values()) for c in certs)
