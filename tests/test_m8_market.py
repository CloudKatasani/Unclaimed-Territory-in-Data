"""M8 acceptance: marketplace listings, entitlement bundles, SLA ledger."""

from __future__ import annotations

from pathlib import Path

from tessera.platform import Tessera
from tests.storyline import build_exposure, drift_and_approve, fresh_app

COPILOT = "agent:outage_copilot@1"


def test_listings_carry_evidence(app: Tessera) -> None:
    cards = {c["listing_id"]: c for c in app.market.listings()}
    assert {
        "product:dp.outage_reliability",
        "product:dp.meter_consumption_daily",
        COPILOT,
        "pack:storm_restoration_crew_allocation",
    } <= set(cards)
    rel = cards["product:dp.outage_reliability"]["evidence"]
    assert rel["verdict"] == "release" and rel["quality_score"] == 1.0 and rel["criticality"] == 4
    agent = cards[COPILOT]["evidence"]
    assert (
        agent["sentence"].startswith("Verified accuracy ")
        and "replayed questions (evaluated 2 Oct 2026)" in agent["sentence"]
    )
    assert agent["meets_minimum"]
    pack = cards["pack:storm_restoration_crew_allocation"]["evidence"]
    assert len(pack["thresholds"]) == 2
    assert pack["product_verdicts"]["dp.meter_outage_exposure"] == "unavailable"


def test_subscribe_issues_two_leases_and_one_budget(app: Tessera) -> None:
    bundle = app.market.subscribe(COPILOT, "alice")
    assert bundle["status"] == "active"
    assert sorted(lease["fqn"] for lease in bundle["leases"]) == [
        "dp.meter_consumption_daily",
        "dp.outage_reliability",
    ]
    assert all(lease["scope_json"]["row_filter"] == "region = 'NORTH'" for lease in bundle["leases"])
    assert app.wh.scalar("SELECT count(*) FROM meta.budgets WHERE bundle_id = ?", [bundle["bundle_id"]]) == 1
    assert bundle["budget"]["max_credits_per_run"] == 0.05
    assert app.market.subscribe(COPILOT, "alice")["bundle_id"] == bundle["bundle_id"]  # idempotent
    assert app.market.unsubscribe(COPILOT, "alice") == 2
    assert app.wh.scalar("SELECT count(*) FROM meta.leases WHERE status = 'active'") == 0
    assert app.market.access("alice")[0]["status"] == "revoked"


def test_pack_with_unpublished_product_is_pending(app: Tessera) -> None:
    bundle = app.market.subscribe("pack:storm_restoration_crew_allocation", "priya")
    assert bundle["status"] == "pending_approval"
    assert {m["fqn"] for m in bundle["missing"]} == {"dp.meter_outage_exposure"}
    assert bundle["leases"] == []


def test_held_refresh_writes_one_sla_entry(template: Path) -> None:
    app = fresh_app(template, "m8-sla")
    try:
        build_exposure(app)
        drift_and_approve(app, approve=False)
        app.answer.ask("What was SAIDI by substation in my region last month?", "alice")  # during the hold
        entries = app.market.ledger()
        assert len(entries) == 1
        e = entries[0]
        assert e["fqn"] == "dp.outage_reliability" and e["breach_kind"] == "freshness" and e["credits"] == 5
        assert app.certs.verify(str(e["evidence_cert_id"]))["signature_valid"]
        card = next(c for c in app.market.listings("data_product") if c["fqn"] == "dp.outage_reliability")
        assert card["evidence"]["sla_credits_30d"] == 5
    finally:
        app.close()
