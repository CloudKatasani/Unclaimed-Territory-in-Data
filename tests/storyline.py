"""Shared storyline helpers for acceptance tests."""

from __future__ import annotations

import csv
from pathlib import Path

from tessera.agents.answer import Answer
from tessera.platform import Tessera

SEED_QUESTIONS = list(csv.DictReader(Path("examples/seed-questions.csv").read_text().splitlines()))
Q1 = SEED_QUESTIONS[0]["text"]
GAP_QUESTIONS = [q for q in SEED_QUESTIONS if q["expected_outcome"] == "missing_data"]


def ask_seed_questions(app: Tessera) -> dict[str, Answer]:
    out = {}
    for q in SEED_QUESTIONS:
        out[q["question_id"]] = app.answer.ask(q["text"], q["asked_by"])
    app.bus.drain()
    return out


def mine_and_draft(app: Tessera) -> tuple[str, str]:
    ask_seed_questions(app)
    intents = app.demand.mine()
    if not intents:  # the batch trigger (every N unresolved questions) already mined and drafted
        rows = app.wh.rows("SELECT intent_id FROM meta.demand_intents ORDER BY created_at")
        intents = [str(r["intent_id"]) for r in rows]
    assert len(intents) == 1, intents
    it = app.demand.intent(intents[0])
    cid = it["contract_id"] or app.demand.draft_contract(intents[0])
    app.bus.drain()
    return intents[0], str(cid)


def build_exposure(app: Tessera) -> str:
    _, cid = mine_and_draft(app)
    app.approvals.approve_contract(cid, "raj")
    app.bus.drain()
    return cid


def drift_and_approve(app: Tessera, approve: bool = True) -> str:
    app.simulate_drift()
    job = str(app.wh.rows("SELECT job_id FROM meta.patches")[0]["job_id"])
    if approve:
        app.approve_patch(job, "raj")
    return job


def fresh_app(template: Path, name: str) -> Tessera:
    import shutil

    from tessera import clock

    path = Path(f"/tmp/tessera-tests/{name}.duckdb")
    path.unlink(missing_ok=True)
    shutil.copyfile(template, path)
    clock.reset()
    return Tessera(path)
