"""M5 acceptance: Drift Healer (ID-2). Runs storyline steps 1-6 once, then the drift scenario."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from tessera import clock
from tessera.platform import Tessera
from tests.storyline import Q1, mine_and_draft


@pytest.fixture(scope="module")
def drifted(template: Path) -> Iterator[dict[str, object]]:
    path = Path("/tmp/tessera-tests/m5.duckdb")
    path.unlink(missing_ok=True)
    shutil.copyfile(template, path)
    clock.reset()
    app = Tessera(path)
    step1 = app.answer.ask(Q1, "alice")
    _, cid = mine_and_draft(app)
    app.approvals.approve_contract(cid, "raj")
    app.bus.drain()
    drift = app.simulate_drift()
    yield {"app": app, "step1": step1, "drift": drift}
    app.close()
    path.unlink(missing_ok=True)


def _patch(app: Tessera) -> dict[str, object]:
    job = app.wh.rows("SELECT job_id FROM meta.patches")[0]["job_id"]
    return app.publisher.patch(str(job))


def test_drift_detected_as_rename_and_type_change(drifted: dict[str, object]) -> None:
    app: Tessera = drifted["app"]  # type: ignore[assignment]
    events = app.bus.history("drift.detected")
    assert len(events) == 1
    ev = app.healer.event(str(drifted["drift"]["events"][0]))  # type: ignore[index]
    kinds = sorted(c["kind"] for c in ev["changes"])
    assert kinds == ["rename", "type_change"]
    rename = next(c for c in ev["changes"] if c["kind"] == "rename")
    assert (rename["old_column"], rename["new_column"]) == ("kwh_delivered", "energy_kwh_del")


def test_blast_radius_has_three_products_with_criticality(drifted: dict[str, object]) -> None:
    app: Tessera = drifted["app"]  # type: ignore[assignment]
    ev = app.healer.event(str(drifted["drift"]["events"][0]))  # type: ignore[index]
    objs = {o["fqn"]: o["criticality"] for o in ev["blast_radius"]["objects"] if o["fqn"].startswith("dp.")}
    assert objs == {
        "dp.outage_reliability": 4,
        "dp.meter_consumption_daily": 3,
        "dp.meter_outage_exposure": 2,
    }


def test_lossy_integer_cast_fails_with_evidence(drifted: dict[str, object]) -> None:
    app: Tessera = drifted["app"]  # type: ignore[assignment]
    cands = _patch(app)["evidence_json"]["candidates"]  # type: ignore[index]
    assert len(cands) == 3
    lossy = next(c for c in cands if "INTEGER" in c["expression"])
    obj = lossy["objects"]["dp.meter_consumption_daily"]
    assert not lossy["passed"] and not obj["passed"]
    assert obj["threshold"] == 0.001
    assert obj.get("divergence") is None or obj["divergence"] > 0.001
    alias_only = next(c for c in cands if c["expression"] == "energy_kwh_del")
    assert not alias_only["passed"]


def test_correct_candidate_auto_merges_low_criticality_and_holds_crit4(drifted: dict[str, object]) -> None:
    app: Tessera = drifted["app"]  # type: ignore[assignment]
    patch = _patch(app)
    ev = patch["evidence_json"]
    chosen = next(c for c in ev["candidates"] if c["candidate_no"] == patch["selected_candidate"])  # type: ignore[index]
    assert chosen["expression"] == "CAST(energy_kwh_del AS DOUBLE)"
    assert chosen["passed"] and chosen["max_divergence"] == 0.0
    assert patch["status"] == "awaiting_approval"
    assert sorted(patch["merged_products"]) == ["dp.meter_consumption_daily", "dp.meter_outage_exposure"]  # type: ignore[arg-type]
    assert patch["held_products"] == ["dp.outage_reliability"]
    prod = app.wh.rows("SELECT refresh_status FROM meta.products WHERE fqn = 'dp.outage_reliability'")[0]
    assert prod["refresh_status"] == "held"
    # while held, the auto-gated patch on the path of a criticality-4 product blocks answers (rule 4)
    blocked = app.answer.ask(Q1, "alice")
    assert blocked.certificate and blocked.certificate["verdict"] == "block"


def test_after_raj_approves_step7_equals_step1(drifted: dict[str, object]) -> None:
    app: Tessera = drifted["app"]  # type: ignore[assignment]
    job_id = str(_patch(app)["job_id"])
    app.approve_patch(job_id, "raj")
    assert _patch(app)["status"] == "merged"
    step1 = drifted["step1"]
    step7 = app.answer.ask(Q1, "alice")
    assert step7.rows == step1.rows  # type: ignore[attr-defined]
    cert = step7.certificate
    assert cert is not None and cert["verdict"] == "release"
    patch_arts = [a for a in cert["agent_authored"] if a["artifact_kind"] == "patch"]
    assert patch_arts and patch_arts[0]["approved_by"] == "raj"
    assert patch_arts[0]["target_fqn"] == "raw.ami_interval_reads"
    assert patch_arts[0]["gate_evidence"]["selected"] == _patch(app)["selected_candidate"]
    assert app.certs.verify(cert["cert_id"])["valid"]
    assert app.ledger.verify()["ok"]
