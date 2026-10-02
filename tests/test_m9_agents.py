"""M9 acceptance: agent evidence — evaluation, shadow trial, promotion through the gate."""

from __future__ import annotations

from pathlib import Path

import pytest

from tessera.market.agents import evaluate_agent
from tessera.market.trials import PromotionRejected
from tessera.platform import Tessera
from tests.storyline import build_exposure, fresh_app


def test_evaluate_agent_v1_meets_minimum_and_card_shows_sentence(app: Tessera) -> None:
    spec = app.market.agent_spec("outage_copilot", 1)
    ev = evaluate_agent(app.wh, app.answer, spec)
    assert ev["accuracy"] >= 0.95 and ev["n"] >= 200
    card = next(c for c in app.market.listings("agent") if c["listing_id"] == "agent:outage_copilot@1")
    expected = f"Verified accuracy {ev['accuracy'] * 100:.1f}% on {ev['n']:,} replayed questions (evaluated 2 Oct 2026)"
    assert card["evidence"]["sentence"] == expected
    # evaluations are signed
    assert len(str(ev["signature"])) == 128


def test_shadow_trial_v2_zero_divergence_elsewhere_and_promotes(template: Path) -> None:
    app = fresh_app(template, "m9-agents")
    try:
        for q in (
            "Which meters reported zero usage during outages last month?",
            "How many meters showed no consumption while their feeder was out?",
        ):
            app.answer.ask(q, "alice")
        with pytest.raises(PromotionRejected):
            app.trials.promote("outage_copilot", "raj")  # no trial yet
        build_exposure(app)
        summary = app.trials.start("outage_copilot", 2)
        assert summary["divergent_other"] == 0  # zero divergence on questions v1 already answered
        assert summary["new_capability"] >= 3  # v2 answers the zero-usage questions v1 could not
        assert all("zero" in q or "no consumption" in q for q in summary["new_capability_examples"])
        assert summary["gate"]["passed"], summary["gate"]
        out = app.trials.promote("outage_copilot", "raj")
        assert out["promoted"] == "agent:outage_copilot@2"
        statuses = {
            r["listing_id"]: r["status"]
            for r in app.wh.rows("SELECT * FROM meta.listings WHERE kind = 'agent'")
        }
        assert statuses == {"agent:outage_copilot@1": "superseded", "agent:outage_copilot@2": "published"}
        card = next(c for c in app.market.listings("agent") if c["status"] == "published")
        assert card["evidence"]["trial"]["status"] == "promoted"
        assert app.ledger.verify()["ok"]
    finally:
        app.close()
