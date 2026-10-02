"""The seven-step storyline from docs/00, runnable as code (used by fixtures, tests and the conductor)."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from tessera.platform import Tessera

EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "seed-questions.csv"


def seed_questions() -> list[dict[str, str]]:
    return list(csv.DictReader(EXAMPLES.read_text().splitlines()))


def step1_certified_answer(app: Tessera) -> dict[str, Any]:
    q = seed_questions()[0]
    return app.answer.ask(q["text"], q["asked_by"]).as_dict()


def step2_gap(app: Tessera) -> list[dict[str, Any]]:
    out = [
        app.answer.ask(q["text"], q["asked_by"]).as_dict()
        for q in seed_questions()
        if q["expected_outcome"] == "missing_data"
    ]
    app.bus.drain()
    return out


def step3_demand(app: Tessera) -> dict[str, Any]:
    intents = app.demand.mine()
    contract_id = app.demand.draft_contract(intents[0]) if intents else None
    app.bus.drain()
    return {"intents": intents, "contract_id": contract_id}


def step4_build(app: Tessera, contract_id: str, approver: str = "raj") -> dict[str, Any]:
    app.approvals.approve_contract(contract_id, approver)
    app.bus.drain()
    job = app.wh.rows("SELECT job_id, status FROM meta.build_jobs WHERE contract_id = ?", [contract_id])
    return job[0] if job else {}


def step6_drift(app: Tessera) -> dict[str, Any]:
    return app.simulate_drift()


def step6_approve(app: Tessera, approver: str = "raj") -> dict[str, Any]:
    rows = app.wh.rows("SELECT job_id FROM meta.patches WHERE status = 'awaiting_approval'")
    return app.approve_patch(str(rows[0]["job_id"]), approver) if rows else {}


def run_all(app: Tessera) -> dict[str, Any]:
    s1 = step1_certified_answer(app)
    step2_gap(app)
    s3 = step3_demand(app)
    s4 = step4_build(app, str(s3["contract_id"]))
    for q in seed_questions()[4:]:
        app.answer.ask(q["text"], q["asked_by"])
    s6 = step6_drift(app)
    step6_approve(app)
    s7 = step1_certified_answer(app)
    return {
        "step1": s1["certificate"]["cert_id"],
        "contract_id": s3["contract_id"],
        "build": s4,
        "drift": s6,
        "step7": s7["certificate"]["cert_id"],
        "same_answer": s1["rows"] == s7["rows"],
    }
