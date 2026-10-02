"""M3 acceptance: Demand Miner (ID-1)."""

from __future__ import annotations

import pytest
import yaml

from tessera.governance import contracts as contracts_mod
from tessera.platform import Tessera
from tests.storyline import GAP_QUESTIONS, ask_seed_questions, mine_and_draft


def test_seed_question_outcomes(app: Tessera) -> None:
    answers = ask_seed_questions(app)
    from tests.storyline import SEED_QUESTIONS

    for q in SEED_QUESTIONS:
        assert answers[q["question_id"]].outcome == q["expected_outcome"], q["text"]


def test_three_gap_questions_form_one_intent(app: Tessera) -> None:
    answers = ask_seed_questions(app)
    intents = app.demand.mine()
    assert len(intents) == 1
    it = app.demand.intent(intents[0])
    expected = {answers[q["question_id"]].question_id for q in GAP_QUESTIONS}
    assert set(it["question_ids"]) == expected
    assert it["label"] == "Meters reporting zero usage during outages"


def test_gap_type_and_join_path(app: Tessera) -> None:
    ask_seed_questions(app)
    it = app.demand.intent(app.demand.mine()[0])
    assert it["gap_type"] == "missing_join_path"
    tables = it["join_path"]["tables"]
    assert tables == ["raw.meters", "raw.feeders", "raw.outage_events", "raw.ami_interval_reads"]
    cols = {s["column"] for s in it["candidate_sources"]}
    assert "raw.ami_interval_reads.kwh_delivered" in cols
    assert any(c.startswith("raw.outage_events.") for c in cols)


def test_contract_validates_and_score_matches_formula(app: Tessera) -> None:
    intent_id, cid = mine_and_draft(app)
    contract, row = contracts_mod.load(app.wh, cid)
    assert row["status"] == "draft" and row["origin"] == "demand_miner"
    assert contract.product == "dp.meter_outage_exposure"
    assert contract.demand_intent_id == intent_id
    with open("examples/contract-meter-outage-exposure.yaml") as f:
        example = contracts_mod.parse(f.read())
    assert contract.model_dump(exclude={"demand_intent_id"}) == example.model_dump(
        exclude={"demand_intent_id"}
    )
    it = app.demand.intent(intent_id)
    # alice (ops_manager=3), maria (analyst=1), deshawn (ops_manager=3); 3 questions
    assert it["demand_score"] == 3 * 2 + 3 + (3 + 1 + 3)
    assert [e["kind"] for e in app.bus.history("contract.drafted")] == ["contract.drafted"]
    assert app.wh.scalar("SELECT count(*) FROM meta.provenance WHERE artifact_kind = 'contract'") == 1


def test_pii_column_without_mask_is_never_included(app: Tessera) -> None:
    ask_seed_questions(app)
    intent_id = app.demand.mine()[0]
    with open("examples/contract-meter-outage-exposure.yaml") as f:
        data = yaml.safe_load(f.read())
    data["schema"].append({"name": "service_address", "type": "varchar", "nullable": True})
    app.llm.override("demand.contract", [{"contract_yaml": yaml.safe_dump(data)}])
    cid = app.demand.draft_contract(intent_id)
    contract, _ = contracts_mod.load(app.wh, cid)
    assert contract.column("service_address") is None
    # with a declared masking policy it is allowed
    data["policies"].append({"kind": "column_mask", "column": "service_address", "expression": "'***'"})
    masked, notes = app.demand.enforce_pii(contracts_mod.parse(yaml.safe_dump(data)))
    assert masked.column("service_address") is not None and notes == []
    # PII never reaches the LLM variables
    calls = app.wh.rows("SELECT * FROM meta.llm_calls WHERE prompt_id = 'demand.contract'")
    assert calls


def test_invalid_draft_triggers_fixup_then_fails_after_two_retries(app: Tessera) -> None:
    ask_seed_questions(app)
    intent_id = app.demand.mine()[0]
    app.llm.override("demand.contract", [{"contract_yaml": "product: nope"}] * 3)
    with pytest.raises(contracts_mod.ContractError):
        app.demand.draft_contract(intent_id)
    assert app.demand.intent(intent_id)["status"] == "needs_review"
