"""Seeded history: what happened before the demo starts.

* 28 Sep — Alice exports August SAIDI by substation (her region). The vendor later restates an
  August S-04 outage, so this export is what the recall job finds (docs/07 capability 1).
* 29 Sep — Raj exports August SAIFI by region (unaffected by the restatement: no notice for Raj).
* The benchmark replay set with ground truth, marketplace listings, and the v1 agent evaluation.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from tessera import clock
from tessera.governance.semantic import Plan
from tessera.market.agents import evaluate_agent, seed_benchmark

EXPORTS = [
    (
        "2026-09-28T14:05:00",
        "alice",
        "Export: SAIDI by substation, my region, last month",
        Plan(
            product="dp.outage_reliability",
            metrics=["saidi"],
            dimensions=["substation_id"],
            time_window="last_month",
        ),
    ),
    (
        "2026-09-29T15:30:00",
        "raj",
        "Export: SAIFI by region, last month",
        Plan(
            product="dp.outage_reliability",
            metrics=["saifi"],
            dimensions=["region"],
            time_window="last_month",
        ),
    ),
]


def seed_history(db_path: Path) -> None:
    from tessera.platform import Tessera

    app = Tessera(db_path)
    try:
        for when, user, text, plan in EXPORTS:
            with clock.at(datetime.fromisoformat(when)):
                app.answer.run_plan(plan, user, text, kind="export")
        app.bus.queue.clear()  # history, not live events
        app.wh.execute("DELETE FROM meta.events")
        with clock.at(clock.demo_now() - timedelta(hours=2)):
            seed_benchmark(app.wh, app.answer)
            app.market.seed()
            evaluate_agent(app.wh, app.answer, app.market.agent_spec("outage_copilot", 1))
        app.wh.execute("CHECKPOINT")
    finally:
        app.close()
