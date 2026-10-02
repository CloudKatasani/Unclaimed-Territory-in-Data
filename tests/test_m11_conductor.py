"""M11 acceptance: the conductor replays steps 0-12 identically from reset."""

from __future__ import annotations

from pathlib import Path

from tessera.demo.conductor import STEPS, fingerprint, run_step, run_through, steps_view
from tests.storyline import fresh_app


def _run(template: Path, name: str) -> tuple[dict[str, object], list[dict[str, object]]]:
    app = fresh_app(template, name)
    try:
        results = run_through(app, len(STEPS) - 1)
        return fingerprint(app), results
    finally:
        app.close()


def test_steps_0_to_12_twice_are_identical(template: Path) -> None:
    fp1, r1 = _run(template, "m11-conductor-a")
    fp2, r2 = _run(template, "m11-conductor-b")
    assert r1 == r2
    for key in ("certificates", "notices", "provenance", "migrations"):
        assert fp1[key] == fp2[key], key
    assert len(fp1["notices"]) >= 1 and len(fp1["certificates"]) > 5  # type: ignore[arg-type]
    by_step = {r["step"]: r for r in r1}
    assert by_step[2]["leases"] == 2
    assert by_step[10]["notices"] == 1
    assert by_step[12]["accepted"] == 3 and by_step[12]["saved_question_outcome"] == "answered"


def test_steps_are_idempotent_and_listed_with_talk_track(template: Path) -> None:
    app = fresh_app(template, "m11-conductor-c")
    try:
        first = run_step(app, 3)  # runs 0-2 first
        assert run_step(app, 3) == first
        view = steps_view(app)
        assert [s["done"] for s in view][:5] == [True, True, True, True, False]
        assert view[9]["talk"].startswith("A vendor changed a field overnight")
        assert all(s["talk"] for s in view) and len(view) == 13
    finally:
        app.close()
