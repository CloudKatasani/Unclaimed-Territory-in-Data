"""M10 acceptance: verified narratives (docs/07 capability 4)."""

from __future__ import annotations

from pathlib import Path

from tessera.platform import Tessera
from tests.storyline import Q1, build_exposure, drift_and_approve, fresh_app


def test_every_sentence_has_a_binding(app: Tessera) -> None:
    ans = app.answer.ask(Q1, "alice")
    assert ans.certificate and len(ans.narrative) == 3
    bindings = app.answer.narrator.for_cert(ans.certificate["cert_id"])
    assert [b["sentence"] for b in bindings] == [s["sentence"] for s in ans.narrative]
    assert {b["query_ref"] for b in bindings} == {"main", "total", "previous_period"}
    assert all(b["result_hash"] for b in bindings)


def test_unbound_sentence_is_dropped_and_logged(app: Tessera) -> None:
    app.llm.override(
        "narrative.write",
        [
            {
                "sentences": [
                    {"sentence": "S-04 had the highest SAIDI.", "query_ref": "main"},
                    {"sentence": "Crews were slow in S-04.", "query_ref": None},
                    {"sentence": "Outages doubled.", "query_ref": "made_up_query"},
                ]
            }
        ],
    )
    ans = app.answer.ask(Q1, "alice")
    assert [s["sentence"] for s in ans.narrative] == ["S-04 had the highest SAIDI."]
    logged = app.wh.rows("SELECT * FROM meta.rejections WHERE source = 'narrative.write'")
    assert len(logged) == 2


def test_s04_sentence_turns_amber_after_restatement(template: Path) -> None:
    app = fresh_app(template, "m10-narrative")
    try:
        step3 = app.answer.ask(Q1, "alice")
        assert step3.certificate
        build_exposure(app)
        drift_and_approve(app)  # vendor restates the August S-04 outage
        rows = app.answer.narrator.for_cert(step3.certificate["cert_id"])
        status = {r["query_ref"]: r["status"] for r in rows}
        assert status == {"main": "current", "total": "current", "previous_period": "restated"}
        amber = next(r for r in rows if r["status"] == "restated")
        assert "S-04" in amber["sentence"]
    finally:
        app.close()


def test_answer_payload_carries_narrative_and_decision(app: Tessera) -> None:
    d = app.answer.ask(Q1, "alice").as_dict()
    assert len(d["narrative"]) == 3 and "decision" in d
    m = app.answer.ask("Should I request mutual aid for substation S-04 tonight?", "priya").as_dict()
    assert m["decision"]["decision"] == "storm_restoration_crew_allocation" and m["narrative"]
