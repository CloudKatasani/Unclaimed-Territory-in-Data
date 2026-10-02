"""M1 acceptance: policy rewrite, column lineage, provenance tamper detection."""

from __future__ import annotations

import pytest

from tessera.governance import contracts as contracts_mod
from tessera.governance.lineage import Lineage
from tessera.governance.policy import PolicyEngine, SQLRejected, guard_generated_sql, load_principal
from tessera.governance.provenance import Artifact, ProvenanceLedger
from tessera.seed import domain as D
from tessera.warehouse.duckdb_wh import DuckDBWarehouse

SQL = "SELECT substation_id, SUM(customer_minutes_interrupted) AS cmi FROM dp.outage_reliability GROUP BY substation_id"


def test_policy_rewrite_alice_vs_admin(wh: DuckDBWarehouse) -> None:
    engine = PolicyEngine(wh)
    alice_sql, alice_dec = engine.rewrite(SQL, load_principal(wh, "alice"))
    assert "region = 'NORTH'" in alice_sql
    assert any(d.applied and d.sentence == "Filtered to region NORTH for alice" for d in alice_dec)
    admin_sql, admin_dec = engine.rewrite(SQL, load_principal(wh, "admin"))
    assert "region" not in admin_sql.lower().split("from")[1]
    assert not any(d.applied for d in admin_dec)
    regions = {
        r["region"]
        for r in wh.rows(
            alice_sql.replace("substation_id,", "region, substation_id,", 1).replace(
                "GROUP BY substation_id", "GROUP BY region, substation_id"
            )
        )
    }
    assert regions == {"NORTH"}


def test_column_mask_applies_to_pii(wh: DuckDBWarehouse) -> None:
    sql, dec = PolicyEngine(wh).rewrite(
        "SELECT meter_id, service_address FROM raw.meters", load_principal(wh, "raj")
    )
    assert "'***'" in sql
    assert {r["service_address"] for r in wh.rows(sql + " LIMIT 5")} == {"***"}
    assert any(d.kind == "column_mask" and d.applied for d in dec)


def test_lineage_saidi_reaches_outage_columns(wh: DuckDBWarehouse) -> None:
    up = set(Lineage(wh).upstream_columns("dp.outage_reliability", ["saidi"]))
    for col in ("start_ts", "end_ts", "customers_affected"):
        assert f"raw.outage_events.{col}" in up
    # AMI-verified restoration means SAIDI also depends on the vendor feed through the ingestion view
    assert "raw.ami_interval_reads.kwh_delivered" in up
    assert "raw.head_end_vendor_feed.kwh_delivered" in up


def test_provenance_verifies_and_detects_tampering(wh: DuckDBWarehouse) -> None:
    ledger = ProvenanceLedger(wh)
    assert ledger.verify()["ok"]
    a1 = ledger.record(
        Artifact("model_sql", "dp.x", "select 1", agent="pipeline_builder@0.1.0", model_name="mock")
    )
    a2 = ledger.record(
        Artifact("model_sql", "dp.x", "select 2", agent="pipeline_builder@0.1.0", model_name="mock")
    )
    assert ledger.get(a2)["prev_artifact_id"] == a1  # type: ignore[index]
    assert ledger.verify()["ok"]
    for field, value in [
        ("approved_by", "'mallory'"),
        ("agent", "'human:raj'"),
        ("target_fqn", "'dp.y'"),
        ("content_sha256", "'00'"),
        ("tests_run", '\'{"forged": "pass"}\''),
    ]:
        original = wh.scalar(f"SELECT {field} FROM meta.provenance WHERE artifact_id = ?", [a1])
        wh.execute(f"UPDATE meta.provenance SET {field} = {value} WHERE artifact_id = ?", [a1])
        res = ledger.verify()
        assert not res["ok"], field
        assert a1 in res["bad_signatures"] + res["chain_breaks"]
        wh.execute(f"UPDATE meta.provenance SET {field} = ? WHERE artifact_id = ?", [original, a1])
    assert ledger.verify()["ok"]


def test_provenance_chain_break_detected(wh: DuckDBWarehouse) -> None:
    ledger = ProvenanceLedger(wh)
    ids = [ledger.record(Artifact("patch", "raw.t", f"v{i}", agent="drift_healer@0.1.0")) for i in range(3)]
    wh.execute("DELETE FROM meta.provenance WHERE artifact_id = ?", [ids[1]])
    res = ledger.verify()
    assert not res["ok"] and ids[2] in res["chain_breaks"]


def test_contract_yaml_roundtrip_and_validation() -> None:
    with open("examples/contract-meter-outage-exposure.yaml") as f:
        c = contracts_mod.parse(f.read())
    assert c.product == "dp.meter_outage_exposure" and c.primary_key == ["meter_id", "outage_id"]
    assert contracts_mod.parse(c.to_yaml()) == c
    with pytest.raises(contracts_mod.ContractError):
        contracts_mod.parse(
            "product: dp.x\ntitle: t\nowner: raj\ngrain: [a]\nprimary_key: [a]\nsources: []\n"
        )
    for spec in D.CONTRACTS.values():
        contracts_mod.parse(spec)


def test_sql_guard() -> None:
    guard_generated_sql("CREATE TABLE draft_1.x AS SELECT 1 AS a", ("draft_",))
    for bad in [
        "DROP TABLE dp.outage_reliability",
        "CREATE TABLE dp.x AS SELECT 1",
        "INSERT INTO raw.meters SELECT * FROM raw.meters",
        "SELECT 1; SELECT 2",
        "DELETE FROM meta.provenance",
    ]:
        with pytest.raises(SQLRejected):
            guard_generated_sql(bad, ("draft_",))
