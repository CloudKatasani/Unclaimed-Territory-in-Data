"""M10 acceptance: decision-first self-serve with sensitivity (docs/07 capability 3)."""

from __future__ import annotations

from tessera.platform import Tessera

MUTUAL_AID = "Should I request mutual aid for substation S-04 tonight?"


def test_mutual_aid_resolves_to_pack_with_hand_computed_margins(app: Tessera) -> None:
    ans = app.answer.ask(MUTUAL_AID, "priya")
    assert ans.decision and ans.decision["decision"] == "storm_restoration_crew_allocation"
    by_metric = {t["metric"]: t for t in ans.decision["thresholds"]}

    # hand computation straight from the product (October to date, NORTH / S-04, sentinels excluded)
    real = "feeder_id NOT LIKE 'SNTL-%' AND month = DATE '2026-10-01'"
    cmi, served = app.wh.rows(
        f"SELECT sum(customer_minutes_interrupted) AS c, sum(customers_served) AS s "
        f"FROM dp.outage_reliability WHERE region = 'NORTH' AND {real}"
    )[0].values()
    saidi = cmi / served
    s = by_metric["saidi_minutes_mtd"]
    assert s["scope_value"] == "NORTH" and abs(s["current"] - saidi) < 1e-3
    assert abs(s["margin"] - (120 - saidi)) < 1e-3 and not s["breached"]

    out = app.wh.scalar(
        f"SELECT sum(customer_interruptions) FROM dp.outage_reliability WHERE substation_id = 'S-04' "
        f"AND {real}"
    )
    c = by_metric["customers_out"]
    assert c["scope_value"] == "S-04" and c["current"] == out and c["margin"] == 2000 - out

    # flip: each extra outage over four hours on an average NORTH feeder adds avg_customers * 241 minutes
    avg = app.wh.scalar(
        f"SELECT avg(customers_served) FROM dp.outage_reliability WHERE region = 'NORTH' AND {real}"
    )
    n = 1
    while (cmi + n * avg * 241) / served <= 120:
        n += 1
    assert s["flip_delta"] == n
    words = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"]
    expected = (
        f"{words[n].capitalize()} more outages over four hours this month and NORTH breaches its SAIDI "
        f"target of 120 minutes."
    )
    assert s["sensitivity"] == expected
    assert expected in [x["sentence"] for x in ans.narrative]
    assert c["flip_delta"] == 2000 - out + 1


def test_decision_answer_is_certified(app: Tessera) -> None:
    ans = app.answer.ask(MUTUAL_AID, "priya")
    cert = ans.certificate
    assert cert and {m["name"] for m in cert["metrics"]} == {"saidi", "customers_interrupted"}
    assert all(m["certified"] for m in cert["metrics"])
    assert cert["verdict"] == "release" and app.certs.verify(cert["cert_id"])["valid"]
    assert ans.decision and ans.decision["pack_verdict"] in ("release", "release_with_warning")


def test_lookup_questions_do_not_take_the_decision_branch(app: Tessera) -> None:
    ans = app.answer.ask("What was SAIDI by substation in my region last month?", "alice")
    assert ans.decision is None and ans.plan["metrics"] == ["saidi"]
