"""M6 acceptance: storyline steps 1-7 end to end through the HTTP API (mock LLM mode)."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tessera import clock
from tessera.api import state
from tessera.api.app import app as api
from tessera.platform import Tessera
from tests.storyline import GAP_QUESTIONS, Q1


@pytest.fixture(scope="module")
def client(template: Path) -> Iterator[TestClient]:
    path = Path("/tmp/tessera-tests/m6.duckdb")
    path.unlink(missing_ok=True)
    shutil.copyfile(template, path)
    clock.reset()
    platform = Tessera(path)
    state.set_app(platform)
    yield TestClient(api)
    state.set_app(None)
    platform.close()
    path.unlink(missing_ok=True)


def test_storyline_end_to_end(client: TestClient) -> None:
    # 1. Certified answer
    step1 = client.post("/ask?user=alice", json={"question": Q1}).json()
    assert step1["outcome"] == "answered" and step1["certificate"]["verdict"] == "release"
    assert {r["substation_id"] for r in step1["rows"]} == {"S-01", "S-02", "S-03", "S-04"}
    assert client.get(f"/certs/{step1['certificate']['cert_id']}/verify").json()["valid"]
    assert client.get("/certs/public-key").json()["algorithm"] == "Ed25519"

    # 2. Gap: three users ask variants; each logged missing_data
    for q in GAP_QUESTIONS:
        r = client.post(f"/ask?user={q['asked_by']}", json={"question": q["text"]}).json()
        assert r["outcome"] == "missing_data" and r["unmatched"]
    assert len(client.get("/demand/unresolved").json()) == 3

    # 3. Demand mining
    intents = client.post("/demand/mine").json()["intents"]
    assert len(intents) == 1
    cid = client.post(f"/demand/intents/{intents[0]}/draft").json()["contract_id"]
    board = client.get("/demand/intents").json()
    assert board[0]["gap_type"] == "missing_join_path"
    assert board[0]["join_path"]["tables"][:3] == ["raw.meters", "raw.feeders", "raw.outage_events"]
    assert "dp.meter_outage_exposure" in client.get(f"/contracts/{cid}").json()["spec_yaml"]

    # 4. Build: alice cannot approve; raj can
    assert client.post(f"/contracts/{cid}/approve?user=alice").status_code == 403
    approved = client.post(f"/contracts/{cid}/approve?user=raj").json()
    assert approved["build"]["status"] == "published"
    job = client.get(f"/builds/{approved['build']['job_id']}").json()
    assert [t["status"] for t in job["timeline_json"]][-1] == "published"
    assert any(p["approved_by"] == "raj" for p in job["provenance"])

    # 5. Closure: all three resolved, askers notified
    board = client.get("/demand/intents").json()
    assert board[0]["closure"] == {"resolved": 3, "total": 3}
    for user in ("alice", "maria", "deshawn"):
        assert any(n["kind"] == "question_resolved" for n in client.get(f"/notifications?user={user}").json())
    gap = client.post("/ask?user=alice", json={"question": GAP_QUESTIONS[0]["text"]}).json()
    assert gap["outcome"] == "answered"
    assert any(
        a["agent"].startswith("pipeline_builder") and a["approved_by"] == "raj"
        for a in gap["certificate"]["agent_authored"]
    )

    # 6. Drift
    drift = client.post("/drift/simulate").json()
    assert drift["vendor_change"]["applied"]
    detail = client.get(f"/drift/events/{drift['events'][0]}").json()
    assert {c["kind"] for c in detail["event"]["changes"]} == {"rename", "type_change"}
    cands = detail["candidates"]
    assert len(cands) == 3 and sum(1 for c in cands if c["selected"]) == 1
    assert not next(c for c in cands if "INTEGER" in c["expression"])["passed"]
    patch = detail["patch"]
    assert patch["status"] == "awaiting_approval" and patch["held_products"] == ["dp.outage_reliability"]
    assert client.post(f"/drift/patches/{patch['job_id']}/approve?user=alice").status_code == 403
    merged = client.post(f"/drift/patches/{patch['job_id']}/approve?user=raj").json()
    assert merged["status"] == "merged"

    # 7. Proof: same answer, certificate lists the healed node with approval and gate evidence
    step7 = client.post("/ask?user=alice", json={"question": Q1}).json()
    assert step7["rows"] == step1["rows"]
    cert = step7["certificate"]
    healed = [a for a in cert["agent_authored"] if a["artifact_kind"] == "patch"]
    assert healed and healed[0]["approved_by"] == "raj" and healed[0]["gate_evidence"]["objects"]
    assert client.get(f"/certs/{cert['cert_id']}/verify").json()["valid"]
    assert client.get("/provenance/verify").json()["ok"]


def test_storyline_used_recorded_fixtures_only(client: TestClient) -> None:
    """Mock fixtures must cover the storyline: no heuristic fallback calls."""
    with state.platform() as app:
        sources = {r["source"] for r in app.wh.rows("SELECT DISTINCT source FROM meta.llm_calls")}
    assert sources <= {"fixture"}, sources
