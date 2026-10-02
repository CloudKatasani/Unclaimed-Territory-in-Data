"""Electric-utility demo domain. Everything sector-specific lives in tessera/seed/ (docs/08)."""

from __future__ import annotations

from typing import Any

REGIONS = ["NORTH", "CENTRAL", "SOUTH"]
SUBSTATIONS_PER_REGION = 4
FEEDERS_PER_SUBSTATION = 5
N_METERS = 5000
N_DAYS = 90
N_RANDOM_OUTAGES = 399  # plus the scripted S-04 outage = 400

USERS: list[dict[str, Any]] = [
    {"user_id": "alice", "display_name": "Alice", "role": "ops_manager", "region": "NORTH"},
    {"user_id": "maria", "display_name": "Maria", "role": "analyst", "region": "NORTH"},
    {"user_id": "deshawn", "display_name": "Deshawn", "role": "ops_manager", "region": "NORTH"},
    {"user_id": "raj", "display_name": "Raj", "role": "data_engineer", "region": None},
    {"user_id": "priya", "display_name": "Priya", "role": "ops_director", "region": None},
    {"user_id": "admin", "display_name": "Admin", "role": "admin", "region": None},
]
APPROVERS = {"raj", "admin"}
UNRESTRICTED_ROLES = ["admin", "data_engineer", "ops_director"]

# Scripted outage whose head-end estimates are later corrected by the vendor (drives the recall).
SCRIPTED_OUTAGE = {
    "feeder_id": "F-018",  # substation S-04, region NORTH
    "start_ts": "2026-08-14 18:00:00",
    "end_ts": "2026-08-14 23:00:00",
    "cause_code": "WEATHER",
    "estimated_from": "2026-08-14 20:00:00",  # head-end filled reads with estimates from here
}

RAW_TABLES: dict[str, dict[str, Any]] = {
    "raw.feeders": {
        "description": "Distribution feeders and the substation and region they belong to",
        "columns": {
            "feeder_id": ("VARCHAR", "Feeder identifier, e.g. F-017"),
            "substation_id": ("VARCHAR", "Substation the feeder is served from, e.g. S-04"),
            "region": ("VARCHAR", "Operating region: NORTH, CENTRAL or SOUTH"),
            "voltage_kv": ("DOUBLE", "Nominal feeder voltage in kV"),
        },
    },
    "raw.meters": {
        "description": "AMI meters, the premise they serve and the feeder they are connected to",
        "columns": {
            "meter_id": ("VARCHAR", "Meter identifier, e.g. M-00042"),
            "premise_id": ("VARCHAR", "Service premise identifier"),
            "feeder_id": ("VARCHAR", "Feeder the meter is connected to"),
            "install_date": ("DATE", "Meter install date"),
            "meter_type": ("VARCHAR", "residential, commercial or industrial"),
            "service_address": ("VARCHAR", "Street address of the premise (PII)"),
        },
    },
    "raw.outage_events": {
        "description": "Outage events from the outage management system, one row per feeder outage",
        "columns": {
            "outage_id": ("VARCHAR", "Outage identifier"),
            "feeder_id": ("VARCHAR", "Feeder that was out"),
            "start_ts": ("TIMESTAMP", "Outage start time (UTC)"),
            "end_ts": ("TIMESTAMP", "Outage end time reported by OMS (UTC)"),
            "cause_code": ("VARCHAR", "WEATHER, EQUIPMENT, VEGETATION, ANIMAL or PLANNED"),
            "customers_affected": ("INTEGER", "Customers interrupted by the outage"),
        },
    },
    "raw.head_end_vendor_feed": {
        "description": "Landing table for interval reads delivered by the AMI head-end vendor",
        "columns": {
            "meter_id": ("VARCHAR", "Meter identifier"),
            "read_ts": ("TIMESTAMP", "Interval read timestamp (UTC)"),
            "kwh_delivered": (
                "DOUBLE",
                "Energy delivered to the premise in the interval; zero means no usage",
            ),
            "kwh_received": ("DOUBLE", "Energy received from the premise (e.g. rooftop solar export)"),
            "quality_flag": ("VARCHAR", "ACT actual, EST estimated by the head-end"),
        },
        "landing": True,
    },
    "raw.ami_interval_reads": {
        "description": "Conformed AMI interval reads: meter usage per interval (kWh consumption)",
        "columns": {
            "meter_id": ("VARCHAR", "Meter identifier"),
            "read_ts": ("TIMESTAMP", "Interval read timestamp (UTC)"),
            "kwh_delivered": (
                "DOUBLE",
                "Energy delivered to the premise in the interval; zero means no usage",
            ),
            "kwh_received": ("DOUBLE", "Energy received from the premise (e.g. rooftop solar export)"),
            "quality_flag": ("VARCHAR", "ACT actual, EST estimated by the head-end"),
        },
    },
}
PII_COLUMNS = {("raw.meters", "service_address")}

FOREIGN_KEYS = [
    ("raw.meters", "feeder_id", "raw.feeders", "feeder_id"),
    ("raw.outage_events", "feeder_id", "raw.feeders", "feeder_id"),
    ("raw.ami_interval_reads", "meter_id", "raw.meters", "meter_id"),
    ("raw.head_end_vendor_feed", "meter_id", "raw.meters", "meter_id"),
]

# The ingestion mapping from the vendor landing table to the conformed reads. Drift patches edit this.
AMI_MAPPING_SQL = """
SELECT meter_id, read_ts, kwh_delivered, kwh_received, quality_flag
FROM raw.head_end_vendor_feed
""".strip()

CONSUMPTION_SQL = """
SELECT CAST(r.read_ts AS DATE) AS read_date, r.meter_id, m.feeder_id, f.region,
       SUM(r.kwh_delivered) AS kwh_delivered, SUM(r.kwh_received) AS kwh_received,
       COUNT(*) AS read_count
FROM raw.ami_interval_reads AS r
JOIN raw.meters AS m ON r.meter_id = m.meter_id
JOIN raw.feeders AS f ON m.feeder_id = f.feeder_id
GROUP BY CAST(r.read_ts AS DATE), r.meter_id, m.feeder_id, f.region
""".strip()

# Outage durations are AMI-verified: an outage counts as restored at the first interval in which at
# least half of the feeder's meters report usage again, or at the OMS end time, whichever is first.
RELIABILITY_SQL = """
WITH meter_counts AS (
  SELECT m.feeder_id, COUNT(*) AS customers_served FROM raw.meters AS m GROUP BY m.feeder_id
), months AS (
  SELECT DISTINCT CAST(date_trunc('month', o.start_ts) AS DATE) AS month FROM raw.outage_events AS o
), interval_status AS (
  SELECT o.outage_id, r.read_ts, COUNT(*) FILTER (WHERE r.kwh_delivered > 0) AS meters_on
  FROM raw.outage_events AS o
  JOIN raw.meters AS m ON m.feeder_id = o.feeder_id
  JOIN raw.ami_interval_reads AS r
    ON r.meter_id = m.meter_id AND r.read_ts >= o.start_ts AND r.read_ts < o.end_ts
  GROUP BY o.outage_id, r.read_ts
), verified AS (
  SELECT o.outage_id, o.feeder_id, o.start_ts, o.customers_affected,
         COALESCE(MIN(s.read_ts) FILTER (WHERE s.meters_on >= 0.5 * mc.customers_served), o.end_ts)
           AS restored_ts
  FROM raw.outage_events AS o
  JOIN meter_counts AS mc ON mc.feeder_id = o.feeder_id
  LEFT JOIN interval_status AS s ON s.outage_id = o.outage_id
  GROUP BY o.outage_id, o.feeder_id, o.start_ts, o.end_ts, o.customers_affected
), outage_agg AS (
  SELECT v.feeder_id, CAST(date_trunc('month', v.start_ts) AS DATE) AS month,
         COUNT(*) AS outage_count,
         COUNT(*) FILTER (WHERE date_diff('minute', v.start_ts, v.restored_ts) > 240) AS outage_count_over_4h,
         SUM(v.customers_affected) AS customer_interruptions,
         SUM(v.customers_affected * date_diff('minute', v.start_ts, v.restored_ts))
           AS customer_minutes_interrupted
  FROM verified AS v
  GROUP BY v.feeder_id, CAST(date_trunc('month', v.start_ts) AS DATE)
)
SELECT mo.month, f.feeder_id, f.substation_id, f.region, mc.customers_served,
       COALESCE(a.outage_count, 0) AS outage_count,
       COALESCE(a.outage_count_over_4h, 0) AS outage_count_over_4h,
       COALESCE(a.customer_interruptions, 0) AS customer_interruptions,
       CAST(COALESCE(a.customer_minutes_interrupted, 0) AS DOUBLE) AS customer_minutes_interrupted,
       COALESCE(a.customer_minutes_interrupted, 0) / mc.customers_served AS saidi,
       COALESCE(a.customer_interruptions, 0) / mc.customers_served AS saifi,
       COALESCE(a.customer_minutes_interrupted, 0) / NULLIF(a.customer_interruptions, 0) AS caidi
FROM raw.feeders AS f
CROSS JOIN months AS mo
JOIN meter_counts AS mc ON mc.feeder_id = f.feeder_id
LEFT JOIN outage_agg AS a ON a.feeder_id = f.feeder_id AND a.month = mo.month
""".strip()

MODELS: list[dict[str, Any]] = [
    {"fqn": "raw.ami_interval_reads", "kind": "view", "sql": AMI_MAPPING_SQL},
    {"fqn": "dp.meter_consumption_daily", "kind": "table", "sql": CONSUMPTION_SQL},
    {"fqn": "dp.outage_reliability", "kind": "table", "sql": RELIABILITY_SQL},
]

CRITICALITY = [
    ("dp.outage_reliability", 4, "SAIDI/SAIFI are reported to the regulator"),
    ("dp.meter_consumption_daily", 3, "Feeds billing reconciliation"),
    ("raw.ami_interval_reads", 3, "Conformed AMI reads"),
]

ROW_FILTER_EXPR = "region = :user.region"

CONTRACTS: dict[str, str] = {
    "dp.outage_reliability": """
product: dp.outage_reliability
version: 1
title: Outage reliability (SAIDI, SAIFI, CAIDI)
description: >
  Monthly reliability indices by feeder, with substation and region for roll-up. Outage durations are
  verified against AMI reads (restored when half the feeder's meters report usage again).
owner: raj
origin: human
grain: [month, feeder_id]
primary_key: [month, feeder_id]
sources: [raw.feeders, raw.meters, raw.outage_events, raw.ami_interval_reads]
schema:
  - {name: month, type: date, nullable: false}
  - {name: feeder_id, type: varchar, nullable: false}
  - {name: substation_id, type: varchar, nullable: false}
  - {name: region, type: varchar, nullable: false}
  - {name: customers_served, type: integer, nullable: false}
  - {name: outage_count, type: integer, nullable: false}
  - {name: outage_count_over_4h, type: integer, nullable: false}
  - {name: customer_interruptions, type: integer, nullable: false}
  - {name: customer_minutes_interrupted, type: double, nullable: false}
  - {name: saidi, type: double, nullable: false}
  - {name: saifi, type: double, nullable: false}
  - {name: caidi, type: double, nullable: true}
quality:
  freshness_sla_hours: 24
  expectations:
    - {name: saidi_non_negative, sql: saidi >= 0}
    - {name: saifi_non_negative, sql: saifi >= 0}
    - {name: customers_positive, sql: customers_served > 0}
foreign_keys:
  - {column: feeder_id, references: raw.feeders.feeder_id}
policies:
  - {kind: row_filter, expression: "region = :user.region", applies_to_roles: [ops_manager, analyst]}
sla:
  credits_per_breach: 5
criticality: 4
""",
    "dp.meter_consumption_daily": """
product: dp.meter_consumption_daily
version: 1
title: Daily meter consumption
description: Daily kWh delivered and received per meter, with feeder and region.
owner: raj
origin: human
grain: [read_date, meter_id]
primary_key: [read_date, meter_id]
sources: [raw.ami_interval_reads, raw.meters, raw.feeders]
schema:
  - {name: read_date, type: date, nullable: false}
  - {name: meter_id, type: varchar, nullable: false}
  - {name: feeder_id, type: varchar, nullable: false}
  - {name: region, type: varchar, nullable: false}
  - {name: kwh_delivered, type: double, nullable: false}
  - {name: kwh_received, type: double, nullable: false}
  - {name: read_count, type: integer, nullable: false}
quality:
  freshness_sla_hours: 24
  expectations:
    - {name: kwh_non_negative, sql: kwh_delivered >= 0}
    - {name: reads_positive, sql: read_count > 0}
foreign_keys:
  - {column: meter_id, references: raw.meters.meter_id}
policies:
  - {kind: row_filter, expression: "region = :user.region", applies_to_roles: [ops_manager, analyst]}
sla:
  credits_per_breach: 2
criticality: 3
""",
}

# Semantic model: entity / dimension / measure / metric rows.
SEMANTIC: list[dict[str, Any]] = [
    {
        "type": "entity",
        "name": "outage_reliability",
        "product": "dp.outage_reliability",
        "description": "Feeder reliability per month",
        "synonyms": ["reliability", "outage reliability"],
    },
    {
        "type": "metric",
        "name": "saidi",
        "product": "dp.outage_reliability",
        "certified": True,
        "expression": "SUM(customer_minutes_interrupted) / NULLIF(SUM(customers_served) / COUNT(DISTINCT month), 0)",
        "description": "System Average Interruption Duration Index, minutes per customer",
        "synonyms": [
            "saidi",
            "system average interruption duration",
            "average outage duration per customer",
            "outage minutes per customer",
            "saidi minutes",
        ],
    },
    {
        "type": "metric",
        "name": "saifi",
        "product": "dp.outage_reliability",
        "certified": True,
        "expression": "SUM(customer_interruptions) / NULLIF(SUM(customers_served) / COUNT(DISTINCT month), 0)",
        "description": "System Average Interruption Frequency Index, interruptions per customer",
        "synonyms": ["saifi", "system average interruption frequency", "outage frequency per customer"],
    },
    {
        "type": "metric",
        "name": "caidi",
        "product": "dp.outage_reliability",
        "certified": True,
        "expression": "SUM(customer_minutes_interrupted) / NULLIF(SUM(customer_interruptions), 0)",
        "description": "Customer Average Interruption Duration Index, minutes per interruption",
        "synonyms": ["caidi", "average restoration time", "customer average interruption duration"],
    },
    {
        "type": "measure",
        "name": "outage_count",
        "product": "dp.outage_reliability",
        "certified": True,
        "expression": "SUM(outage_count)",
        "description": "Number of outages",
        "synonyms": ["number of outages", "outage count", "how many outages", "count of outages"],
    },
    {
        "type": "measure",
        "name": "customers_interrupted",
        "product": "dp.outage_reliability",
        "certified": True,
        "expression": "SUM(customer_interruptions)",
        "description": "Customer interruptions",
        "synonyms": ["customers out", "customers interrupted", "customers affected by outages"],
    },
    {
        "type": "dimension",
        "name": "month",
        "product": "dp.outage_reliability",
        "grain": "month",
        "expression": "month",
        "synonyms": ["month", "monthly", "by month"],
    },
    {
        "type": "dimension",
        "name": "feeder_id",
        "product": "dp.outage_reliability",
        "expression": "feeder_id",
        "synonyms": ["feeder", "feeders", "circuit"],
    },
    {
        "type": "dimension",
        "name": "substation_id",
        "product": "dp.outage_reliability",
        "expression": "substation_id",
        "synonyms": ["substation", "substations"],
    },
    {
        "type": "dimension",
        "name": "region",
        "product": "dp.outage_reliability",
        "expression": "region",
        "synonyms": ["region", "regions"],
    },
    {
        "type": "entity",
        "name": "meter_consumption_daily",
        "product": "dp.meter_consumption_daily",
        "description": "Daily energy per meter",
        "synonyms": ["meter consumption", "daily consumption"],
    },
    {
        "type": "metric",
        "name": "daily_kwh",
        "product": "dp.meter_consumption_daily",
        "certified": True,
        "expression": "SUM(kwh_delivered)",
        "description": "kWh delivered (consumption)",
        "synonyms": ["kwh", "daily kwh", "consumption", "usage", "energy delivered", "energy use"],
    },
    {
        "type": "measure",
        "name": "kwh_received",
        "product": "dp.meter_consumption_daily",
        "certified": True,
        "expression": "SUM(kwh_received)",
        "description": "kWh exported by the premise",
        "synonyms": ["kwh received", "solar export", "energy exported"],
    },
    {
        "type": "dimension",
        "name": "read_date",
        "product": "dp.meter_consumption_daily",
        "grain": "day",
        "expression": "read_date",
        "synonyms": ["day", "daily", "date", "per day"],
    },
    {
        "type": "dimension",
        "name": "meter_id",
        "product": "dp.meter_consumption_daily",
        "expression": "meter_id",
        "synonyms": ["meter", "meters"],
    },
    {
        "type": "dimension",
        "name": "feeder_id",
        "product": "dp.meter_consumption_daily",
        "expression": "feeder_id",
        "synonyms": ["feeder", "feeders", "circuit"],
    },
    {
        "type": "dimension",
        "name": "region",
        "product": "dp.meter_consumption_daily",
        "expression": "region",
        "synonyms": ["region", "regions"],
    },
]
