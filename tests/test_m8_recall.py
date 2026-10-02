"""M8 acceptance: data recall notices (docs/07 capability 1)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from tessera.jsonutil import dumps, loads
from tessera.platform import Tessera
from tests.storyline import Q1, build_exposure, drift_and_approve, fresh_app


@pytest.fixture(scope="module")
def restated(template: Path) -> Iterator[Tessera]:
    app = fresh_app(template, "m8-recall")
    app.answer.ask(Q1, "alice")
    build_exposure(app)
    drift_and_approve(app)
    yield app
    app.close()


def test_one_notice_for_alice_none_for_raj(restated: Tessera) -> None:
    app = restated
    notices = app.recall.inbox("alice")
    assert len(notices) == 1
    n = notices[0]
    snap = app.wh.rows("SELECT * FROM meta.answer_snapshots WHERE cert_id = ?", [n["cert_id"]])[0]
    assert snap["kind"] == "export" and snap["served_at"].day == 28
    delta = loads(n["delta_json"])
    changed = {c["key"]["substation_id"] for c in delta["cells"]}
    assert changed == {"S-04"}
    cell = delta["cells"][0]
    assert cell["new"] > cell["old"]  # estimates replaced by actual zero reads: the outage lasted longer
    assert n["message"].startswith("SAIDI for substation S-04 (exported 28 Sep) changed from ")
    assert "vendor feed field rename" in n["message"]
    assert app.recall.inbox("raj") == []
    # Deshawn's zero-usage answer had no time window, so it covered the restated August outage too
    for other in app.wh.rows("SELECT * FROM meta.recall_notices WHERE consumer <> 'alice'"):
        assert other["consumer"] == "deshawn" and "dp.meter_outage_exposure" in app.wh.scalar(
            "SELECT lineage_nodes FROM meta.certificates WHERE cert_id = ?", [other["cert_id"]]
        )
    assert any(
        x["kind"] == "recall"
        for x in app.wh.rows("SELECT kind FROM meta.notifications WHERE user_id = 'alice'")
    )


def test_acknowledge_clears_banner(restated: Tessera) -> None:
    app = restated
    assert app.recall.open_count("alice") == 1
    app.recall.acknowledge(str(app.recall.inbox("alice")[0]["notice_id"]), "alice")
    assert app.recall.open_count("alice") == 0


def test_sub_materiality_delta_produces_no_notice(restated: Tessera) -> None:
    app = restated
    ans = app.answer.ask(Q1, "alice")
    assert ans.certificate
    cid = ans.certificate["cert_id"]

    def tweak(factor: float) -> None:
        rows = [{**r, "saidi": round(r["saidi"] * factor, 4)} for r in ans.rows]
        app.wh.execute(
            "UPDATE meta.answer_snapshots SET result_json = ?, result_hash = ? WHERE cert_id = ?",
            [dumps(rows), f"tweaked-{factor}", cid],
        )

    before = len(app.wh.rows("SELECT * FROM meta.recall_notices"))
    tweak(1.005)  # 0.5% < default 1% materiality
    assert app.recall.run("dp.outage_reliability", "test") == []
    tweak(1.05)
    assert len(app.recall.run("dp.outage_reliability", "test")) == 1
    assert len(app.wh.rows("SELECT * FROM meta.recall_notices")) == before + 1
