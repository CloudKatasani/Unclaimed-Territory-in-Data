"""Seed generator: synthetic electric-utility warehouse with a fixed random seed.

python -m tessera.seed.generate [--db PATH]
"""

from __future__ import annotations

import argparse
import shutil
import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

from tessera import clock
from tessera.config import Settings, get_settings
from tessera.governance import contracts as contracts_mod
from tessera.governance import ingestion
from tessera.governance.catalog import Catalog
from tessera.governance.lineage import Lineage
from tessera.governance.provenance import Artifact, ProvenanceLedger
from tessera.governance.schema import create_meta
from tessera.governance.semantic import SemanticModel
from tessera.governance.signing import generate_key
from tessera.jsonutil import dumps
from tessera.publisher.promote import materialize
from tessera.publisher.refresh import record_quality, register_model, run_product_tests
from tessera.seed import domain as D
from tessera.warehouse.base import Warehouse
from tessera.warehouse.duckdb_wh import DuckDBWarehouse

STREETS = ["Elm", "Oak", "Maple", "Cedar", "Pine", "Birch", "Willow", "Lake", "Hill", "River"]
CAUSES = ["WEATHER", "EQUIPMENT", "VEGETATION", "ANIMAL", "PLANNED"]


def window(settings: Settings) -> tuple[datetime, datetime]:
    end = clock.naive_utc(clock.demo_now())
    start = end - timedelta(days=D.N_DAYS)
    return start, end


def _gen_dimensions(wh: Warehouse, rng: np.random.Generator) -> list[tuple[str, int]]:
    feeders: list[tuple[str, str, str, float]] = []
    fno = 0
    for ri, region in enumerate(D.REGIONS):
        for si in range(D.SUBSTATIONS_PER_REGION):
            sub = f"S-{ri * D.SUBSTATIONS_PER_REGION + si + 1:02d}"
            for _ in range(D.FEEDERS_PER_SUBSTATION):
                fno += 1
                feeders.append((f"F-{fno:03d}", sub, region, float(rng.choice([12.47, 25.0]))))
    wh.execute(
        "CREATE TABLE raw.feeders (feeder_id VARCHAR, substation_id VARCHAR, region VARCHAR, voltage_kv DOUBLE)"
    )
    wh.load_rows("raw.feeders", ["feeder_id", "substation_id", "region", "voltage_kv"], feeders)

    feeder_idx = rng.integers(0, len(feeders), size=D.N_METERS)
    types = rng.choice(["residential", "commercial", "industrial"], p=[0.85, 0.12, 0.03], size=D.N_METERS)
    years = rng.integers(2015, 2025, size=D.N_METERS)
    days = rng.integers(0, 365, size=D.N_METERS)
    house = rng.integers(10, 9999, size=D.N_METERS)
    street = rng.integers(0, len(STREETS), size=D.N_METERS)
    solar = rng.random(D.N_METERS) < 0.10
    anomalous = rng.random(D.N_METERS) < 0.02  # mis-mapped meters keep reading during feeder outages
    meters = []
    extra = []
    for i in range(D.N_METERS):
        mid = f"M-{i + 1:05d}"
        install = (datetime(int(years[i]), 1, 1) + timedelta(days=int(days[i]))).date()
        meters.append(
            (
                mid,
                f"P-{i + 1:05d}",
                feeders[feeder_idx[i]][0],
                install,
                str(types[i]),
                f"{house[i]} {STREETS[street[i]]} St",
            )
        )
        extra.append((mid, i + 1, bool(solar[i]), bool(anomalous[i])))
    wh.execute(
        "CREATE TABLE raw.meters (meter_id VARCHAR, premise_id VARCHAR, feeder_id VARCHAR, install_date DATE, "
        "meter_type VARCHAR, service_address VARCHAR)"
    )
    wh.load_rows(
        "raw.meters",
        ["meter_id", "premise_id", "feeder_id", "install_date", "meter_type", "service_address"],
        meters,
    )
    wh.execute(
        "CREATE TABLE seed_tmp.meter_attrs (meter_id VARCHAR, idx BIGINT, solar BOOLEAN, anomalous BOOLEAN)"
    )
    wh.load_rows("seed_tmp.meter_attrs", ["meter_id", "idx", "solar", "anomalous"], extra)
    return [(f[0], 0) for f in feeders]


def _gen_outages(wh: Warehouse, rng: np.random.Generator, start: datetime, end: datetime) -> None:
    feeders = [str(r["feeder_id"]) for r in wh.rows("SELECT feeder_id FROM raw.feeders ORDER BY feeder_id")]
    so = D.SCRIPTED_OUTAGE
    s_start = datetime.fromisoformat(str(so["start_ts"]))
    s_end = datetime.fromisoformat(str(so["end_ts"]))
    taken: dict[str, list[tuple[datetime, datetime]]] = {f: [] for f in feeders}
    taken[str(so["feeder_id"])].append((s_start - timedelta(hours=6), s_end + timedelta(hours=6)))
    events: list[tuple[str, datetime, datetime, str]] = [
        (str(so["feeder_id"]), s_start, s_end, str(so["cause_code"]))
    ]
    slots = int((end - start - timedelta(hours=10)).total_seconds() // 900)
    while len(events) < D.N_RANDOM_OUTAGES + 1:
        f = feeders[int(rng.integers(0, len(feeders)))]
        st = start + timedelta(minutes=15 * int(rng.integers(0, slots)))
        dur = int(np.clip(round(float(rng.lognormal(np.log(45), 0.75)) / 15) * 15, 15, 480))
        en = st + timedelta(minutes=dur)
        if any(st < b + timedelta(hours=1) and en > a - timedelta(hours=1) for a, b in taken[f]):
            continue
        taken[f].append((st, en))
        cause = CAUSES[int(rng.choice(len(CAUSES), p=[0.4, 0.25, 0.2, 0.1, 0.05]))]
        events.append((f, st, en, cause))
    events.sort(key=lambda e: (e[1], e[0]))
    counts = {
        str(r["feeder_id"]): int(r["n"])
        for r in wh.rows("SELECT feeder_id, count(*) AS n FROM raw.meters GROUP BY feeder_id")
    }
    wh.execute(
        "CREATE TABLE raw.outage_events (outage_id VARCHAR, feeder_id VARCHAR, start_ts TIMESTAMP, "
        "end_ts TIMESTAMP, cause_code VARCHAR, customers_affected INTEGER)"
    )
    rows = [(f"O-{i + 1:04d}", f, st, en, c, counts.get(f, 0)) for i, (f, st, en, c) in enumerate(events)]
    wh.load_rows(
        "raw.outage_events",
        ["outage_id", "feeder_id", "start_ts", "end_ts", "cause_code", "customers_affected"],
        rows,
    )


def _gen_reads(wh: Warehouse, start: datetime, end: datetime, interval: int) -> None:
    n_slots = int((end - start).total_seconds() // (interval * 60))
    so = D.SCRIPTED_OUTAGE
    # slots during outages (reads are zero) and the head-end estimate window (reads non-zero, EST)
    wh.execute(f"""
        CREATE TABLE seed_tmp.outage_slots AS
        SELECT o.feeder_id, s.slot
        FROM raw.outage_events o,
             LATERAL (SELECT unnest(range(
                 CAST(ceil(date_diff('second', TIMESTAMP '{start}', o.start_ts) / {interval * 60}.0) AS BIGINT),
                 CAST(ceil(date_diff('second', TIMESTAMP '{start}', o.end_ts) / {interval * 60}.0) AS BIGINT)))
                 AS slot) s
    """)
    wh.execute(f"""
        CREATE TABLE seed_tmp.est_slots AS
        SELECT '{so["feeder_id"]}' AS feeder_id, unnest(range(
            CAST(ceil(date_diff('second', TIMESTAMP '{start}', TIMESTAMP '{so["estimated_from"]}') / {interval * 60}.0)
                 AS BIGINT),
            CAST(ceil(date_diff('second', TIMESTAMP '{start}', TIMESTAMP '{so["end_ts"]}') / {interval * 60}.0)
                 AS BIGINT))) AS slot
    """)
    scale = interval / 60.0
    wh.execute(f"""
        CREATE TABLE raw.head_end_vendor_feed AS
        WITH m AS (
            SELECT m.meter_id, m.feeder_id, a.idx, a.solar, a.anomalous,
                   CASE m.meter_type WHEN 'industrial' THEN 15.0 WHEN 'commercial' THEN 4.0 ELSE 0.9 END AS base
            FROM raw.meters m JOIN seed_tmp.meter_attrs a USING (meter_id)
        ), sl AS (
            SELECT s AS slot, TIMESTAMP '{start}' + to_minutes(s * {interval}) AS read_ts,
                   (hour(TIMESTAMP '{start}' + to_minutes(s * {interval})) + 20) % 24 AS local_hour
            FROM range(0, {n_slots}) t(s)
        ), g AS (
            SELECT m.meter_id, sl.read_ts, m.anomalous, m.solar, m.base, sl.local_hour,
                   (hash(m.idx * 1000003 + sl.slot) % 10000) / 10000.0 AS u1,
                   (hash(m.idx * 7919 + sl.slot * 31 + 17) % 10000) / 10000.0 AS u2,
                   o.slot IS NOT NULL AS in_outage, e.slot IS NOT NULL AS estimated
            FROM m CROSS JOIN sl
            LEFT JOIN seed_tmp.outage_slots o ON o.feeder_id = m.feeder_id AND o.slot = sl.slot
            LEFT JOIN seed_tmp.est_slots e ON e.feeder_id = m.feeder_id AND e.slot = sl.slot
        )
        SELECT meter_id, read_ts,
               CASE WHEN in_outage AND NOT anomalous AND NOT estimated THEN 0.0
                    ELSE round(base * {scale} * (0.55 + 0.45 * sin(pi() * greatest(0, local_hour - 5) / 19))
                               * (0.7 + 0.6 * u1), 3) END AS kwh_delivered,
               CASE WHEN in_outage AND NOT anomalous THEN 0.0
                    WHEN solar AND local_hour BETWEEN 10 AND 16 THEN round(0.8 * {scale} * u2, 3)
                    ELSE 0.0 END AS kwh_received,
               CASE WHEN estimated THEN 'EST' WHEN u2 < 0.002 THEN 'EST' ELSE 'ACT' END AS quality_flag
        FROM g
        ORDER BY read_ts, meter_id
    """)


def _register_catalog(wh: Warehouse) -> None:
    cat = Catalog(wh)
    for fqn, spec in D.RAW_TABLES.items():
        cols = []
        for name, (dtype, desc) in spec["columns"].items():
            cls = "PII" if (fqn, name) in D.PII_COLUMNS else None
            cols.append((name, dtype, desc, cls))
        cat.register_columns(fqn, cols, searchable=not spec.get("landing", False))
    for s, sc, d, dc in D.FOREIGN_KEYS:
        wh.execute("INSERT INTO meta.foreign_keys VALUES (?, ?, ?, ?)", [s, sc, d, dc])


def _register_policies(wh: Warehouse) -> None:
    n = 0
    for fqn in D.CONTRACTS:
        n += 1
        wh.execute(
            "INSERT INTO meta.policies VALUES (?, 'row_filter', ?, 'region', ?, NULL, ?, NULL)",
            [f"pol_rowfilter_{n}", fqn, D.ROW_FILTER_EXPR, dumps(D.UNRESTRICTED_ROLES)],
        )
    for fqn, col in sorted(D.PII_COLUMNS):
        n += 1
        wh.execute(
            "INSERT INTO meta.policies VALUES (?, 'column_mask', ?, NULL, '''***''', 'PII', ?, ?)",
            [f"pol_mask_{n}", fqn, dumps(["admin"]), col],
        )


def seed(db_path: Path, settings: Settings | None = None, verbose: bool = False) -> None:
    settings = settings or get_settings()
    clock.reset()
    t0 = time.time()
    for p in (db_path, Path(str(db_path) + ".wal")):
        if p.exists():
            p.unlink()
    wh = DuckDBWarehouse(db_path)
    try:
        for s in ("raw", "dp", "lkg", "seed_tmp"):
            wh.execute(f"CREATE SCHEMA {s}")
        create_meta(wh)
        generate_key(wh, settings.key_seed)
        for u in D.USERS:
            wh.execute(
                "INSERT INTO meta.users VALUES (?, ?, ?, ?)",
                [u["user_id"], u["display_name"], u["role"], u["region"]],
            )
        for fqn, crit, reason in D.CRITICALITY:
            wh.execute("INSERT INTO meta.criticality VALUES (?, ?, ?)", [fqn, crit, reason])
        _register_catalog(wh)
        _register_policies(wh)

        rng = np.random.default_rng(settings.random_seed)
        start, end = window(settings)
        _gen_dimensions(wh, rng)
        _gen_outages(wh, rng, start, end)
        _gen_reads(wh, start, end, settings.read_interval_min)
        wh.drop_schema("seed_tmp")
        if verbose:
            print(f"raw data generated in {time.time() - t0:.1f}s")

        ledger = ProvenanceLedger(wh)
        lineage = Lineage(wh)
        sem = SemanticModel(wh)
        for m in D.MODELS:
            materialize(wh, m["fqn"], m["sql"], m["kind"])
            aid = ledger.record(
                Artifact(
                    "model_sql",
                    m["fqn"],
                    m["sql"],
                    agent="human:raj",
                    approved_by="raj",
                    input_refs={"seed": True},
                )
            )
            register_model(wh, m["fqn"], m["kind"], m["sql"], aid)
            lineage.rebuild_for(m["fqn"], m["sql"], aid)
        for fqn, spec_yaml in D.CONTRACTS.items():
            contract = contracts_mod.parse(spec_yaml)
            cid = contracts_mod.save(wh, contract, "published")
            contracts_mod.set_status(wh, cid, "published", approved_by="raj")
            wh.execute(
                "INSERT INTO meta.products VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, 'ok', NULL)",
                [
                    fqn,
                    contract.title,
                    contract.owner,
                    cid,
                    clock.naive_utc(clock.now() - timedelta(hours=3)),
                    contract.quality.freshness_sla_hours,
                ],
            )
            record_quality(wh, fqn, run_product_tests(wh, fqn), refreshed=False)
        for e in D.SEMANTIC:
            sem.add_element(
                e["type"],
                e["name"],
                e["product"],
                e.get("expression", e["name"]),
                grain=e.get("grain"),
                certified=bool(e.get("certified")),
                certified_by="raj" if e.get("certified") else None,
                synonyms=e.get("synonyms", []),
                description=e.get("description", ""),
            )
        ingestion.snapshot(wh)
        snapshot_lkg(wh, settings)
        wh.execute("CHECKPOINT")
        if verbose:
            print(f"seed complete in {time.time() - t0:.1f}s -> {db_path}")
    finally:
        wh.close()


def snapshot_lkg(wh: Warehouse, settings: Settings | None = None) -> None:
    """Keep the last-known-good vendor feed for the drift-gate window (the baseline for shadow runs)."""
    settings = settings or get_settings()
    hi = clock.naive_utc(clock.now())
    lo = hi - timedelta(days=settings.drift_window_days)
    wh.execute("CREATE SCHEMA IF NOT EXISTS lkg")
    wh.clone_table(
        "raw.head_end_vendor_feed",
        "lkg.head_end_vendor_feed",
        where=f"read_ts >= TIMESTAMP '{lo}' AND read_ts < TIMESTAMP '{hi}'",
    )


def ensure_template(settings: Settings | None = None, force: bool = False) -> Path:
    settings = settings or get_settings()
    tpl = settings.seed_template
    if force or not tpl.exists():
        seed(tpl, settings)
    return tpl


def install(settings: Settings | None = None, force_seed: bool = False) -> Path:
    """Copy the seeded template into the live database path (fast reset)."""
    settings = settings or get_settings()
    tpl = ensure_template(settings, force=force_seed)
    db = settings.db_path
    db.parent.mkdir(parents=True, exist_ok=True)
    wal = Path(str(db) + ".wal")
    if wal.exists():
        wal.unlink()
    shutil.copyfile(tpl, db)
    clock.reset()
    return db


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=None)
    ap.add_argument("--install", action="store_true", help="also copy the template to the live DB")
    args = ap.parse_args()
    settings = get_settings()
    seed(args.db or settings.seed_template, settings, verbose=True)
    if args.install:
        install(settings)


if __name__ == "__main__":
    main()
