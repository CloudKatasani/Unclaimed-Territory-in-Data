"""M0 acceptance: seed row counts, certified products, meta schema."""

from __future__ import annotations

from tessera.config import get_settings
from tessera.governance.schema import META_DDL
from tessera.seed import domain as D
from tessera.warehouse.duckdb_wh import DuckDBWarehouse


def test_seed_row_counts(wh: DuckDBWarehouse) -> None:
    assert wh.scalar("SELECT count(DISTINCT region) FROM raw.feeders") == 3
    assert wh.scalar("SELECT count(DISTINCT substation_id) FROM raw.feeders") == 12
    assert wh.scalar("SELECT count(*) FROM raw.feeders") == 60
    assert wh.scalar("SELECT count(*) FROM raw.meters") == 5000
    outages = wh.scalar("SELECT count(*) FROM raw.outage_events")
    assert abs(outages - 400) <= 4  # ±1%
    per_day = 24 * 60 // get_settings().read_interval_min
    assert wh.scalar("SELECT count(*) FROM raw.ami_interval_reads") == 5000 * D.N_DAYS * per_day
    assert wh.scalar("SELECT count(DISTINCT CAST(read_ts AS DATE)) FROM raw.ami_interval_reads") in (90, 91)


def test_seed_is_deterministic(wh: DuckDBWarehouse) -> None:
    # fixed random seed: these fingerprints must never change between runs
    assert wh.scalar("SELECT feeder_id FROM raw.meters WHERE meter_id = 'M-00001'") is not None
    assert wh.scalar("SELECT substation_id FROM raw.feeders WHERE feeder_id = 'F-017'") == "S-04"
    assert wh.scalar("SELECT region FROM raw.feeders WHERE feeder_id = 'F-017'") == "NORTH"


def test_certified_products_exist(wh: DuckDBWarehouse) -> None:
    for fqn in ("dp.outage_reliability", "dp.meter_consumption_daily"):
        assert wh.table_exists(fqn)
        assert wh.scalar(f"SELECT count(*) FROM {fqn}") > 0
        n = wh.scalar(
            "SELECT count(*) FROM meta.semantic_model WHERE product_fqn = ? AND element_type = 'metric' "
            "AND certified",
            [fqn],
        )
        assert n >= 1
    certified = {
        r["name"]
        for r in wh.rows("SELECT name FROM meta.semantic_model WHERE certified AND element_type = 'metric'")
    }
    assert {"saidi", "saifi", "caidi", "daily_kwh"} <= certified


def test_meta_tables_exist_and_runtime_tables_empty(wh: DuckDBWarehouse) -> None:
    tables = set(wh.list_tables("meta"))
    assert set(META_DDL) <= tables
    for runtime in (
        "demand_intents",
        "build_jobs",
        "drift_events",
        "patches",
        "notifications",
        "events",
        "recall_notices",
        "subscriptions",
        "leases",
        "sla_ledger",
    ):
        assert wh.scalar(f"SELECT count(*) FROM meta.{runtime}") == 0, runtime
    # the only questions/certificates are seeded history: two exports served before the demo starts
    assert wh.scalar("SELECT count(*) FROM meta.questions WHERE asked_at >= TIMESTAMP '2026-10-02'") == 0
    assert wh.scalar("SELECT count(*) FROM meta.answer_snapshots WHERE kind = 'export'") == 2
    assert wh.scalar("SELECT count(*) FROM meta.keys") == 1
    assert wh.scalar("SELECT count(*) FROM meta.contracts WHERE status = 'published'") == 2
