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
    assert len(intents) == 1, intents
    cid = app.demand.draft_contract(intents[0])
    app.bus.drain()
    return intents[0], cid
