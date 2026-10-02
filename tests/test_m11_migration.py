"""M11 acceptance: consumer migration on a breaking publish (docs/07 capability 5)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from tessera.platform import Tessera
from tessera.seed import domain as D
from tests.storyline import Q1, fresh_app


@pytest.fixture(scope="module")
def published(template: Path) -> Iterator[tuple[Tessera, dict[str, object], list[dict[str, object]]]]:
    app = fresh_app(template, "m11-migration")
    before = app.answer.ask(Q1, "alice")
    out = app.publish_version(D.RELIABILITY_V2_CONTRACT, D.RELIABILITY_V2_SQL, "raj")
    yield app, out, before.rows
    app.close()


def test_three_migrations_proposed_with_zero_divergence(
    published: tuple[Tessera, dict[str, object], list],
) -> None:
    app, out, _ = published
    assert out["breaking"] and out["renames"] == {"saidi": "saidi_minutes"} and out["removed"] == ["saidi"]
    rows = app.wh.rows("SELECT * FROM meta.migrations ORDER BY consumer_id")
    assert [r["consumer_id"] for r in rows] == [
        "agent:outage_copilot@2",
        "dash.restoration_board",
        "saved:alice:saidi_by_substation",
    ]
    assert all(r["divergence"] == 0.0 and r["status"] == "proposed" for r in rows)
    dash = next(r for r in rows if r["consumer_id"] == "dash.restoration_board")
    assert "MAX(saidi_minutes) AS worst_feeder_saidi" in dash["new_text"]
    agent = next(r for r in rows if r["consumer_id"].startswith("agent:"))
    assert '"saidi"' in agent["old_text"] and '"saidi_minutes"' in agent["new_text"]
    assert app.wh.table_exists("dp_archive.outage_reliability__v1")
    assert "cause_category" in {c.name for c in app.wh.table_schema("dp.outage_reliability")}


def test_dashboard_breaks_without_migration(published: tuple[Tessera, dict[str, object], list]) -> None:
    app, _, _ = published
    old_sql = app.wh.scalar(
        "SELECT artifact_text FROM meta.consumers WHERE consumer_id = 'dash.restoration_board'"
    )
    with pytest.raises(Exception, match="saidi"):
        app.wh.rows(str(old_sql))


def test_accept_all_keeps_saved_question_and_step7_working(
    published: tuple[Tessera, dict[str, object], list],
) -> None:
    app, _, before = published
    assert app.migration.accept_all("dp.outage_reliability", "raj") == 3
    saved = app.wh.scalar(
        "SELECT artifact_text FROM meta.consumers WHERE consumer_id = 'saved:alice:saidi_by_substation'"
    )
    assert '"saidi_minutes"' in str(saved)
    run = app.migration.run_saved("saved:alice:saidi_by_substation", "alice", app.answer)
    assert run.outcome == "answered" and [list(r.values()) for r in run.rows] == [
        list(r.values()) for r in before
    ]
    step7 = app.answer.ask(Q1, "alice")
    assert step7.outcome == "answered" and step7.plan["metrics"] == ["saidi_minutes"]
    assert [list(r.values()) for r in step7.rows] == [list(r.values()) for r in before]
    dash = app.wh.scalar(
        "SELECT artifact_text FROM meta.consumers WHERE consumer_id = 'dash.restoration_board'"
    )
    assert app.wh.rows(str(dash))
    migs = app.wh.rows("SELECT * FROM meta.provenance WHERE artifact_kind = 'migration'")
    assert len(migs) == 3 and all(m["approved_by"] == "raj" for m in migs)


def test_reject_pins_consumer_with_sunset(template: Path) -> None:
    app = fresh_app(template, "m11-reject")
    try:
        app.publish_version(D.RELIABILITY_V2_CONTRACT, D.RELIABILITY_V2_SQL, "raj")
        mid = app.wh.scalar(
            "SELECT migration_id FROM meta.migrations WHERE consumer_id = 'dash.restoration_board'"
        )
        app.migration.reject(str(mid), "raj")
        c = app.wh.rows("SELECT * FROM meta.consumers WHERE consumer_id = 'dash.restoration_board'")[0]
        assert c["pinned_version"] == 1 and c["sunset_at"] is not None
    finally:
        app.close()
