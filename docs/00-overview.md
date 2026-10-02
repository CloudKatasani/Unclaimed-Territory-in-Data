# 00 — Overview

## One-line pitch

A closed loop where business questions create governed data products, AI agents build and heal
the pipelines behind them, and every answer proves where its numbers came from.

## What the prototype demonstrates

| Capability | Idea ID | Invention disclosure |
| --- | --- | --- |
| Certified answers with a signed trust certificate | B1 + A6 | ID-3 |
| Demand-driven data product synthesis from unanswered questions | B2 | ID-1 |
| Contract-to-pipeline generation with contract-derived tests | A1 | supports ID-1 |
| Verified self-healing under schema drift (shadow run + lineage-weighted gate) | A2 | ID-2 |
| Agent provenance ledger bound to lineage | A6 | supports ID-3 |

Out of scope for the prototype: B3 (entitlement-adaptive answering), B4 (metric conflict sentinel),
A3 (cost planner), A4 (migration), A5 (incident triage). Leave extension points, do not build.

## Demo domain — electric utility

Seeded DuckDB warehouse with synthetic data (generator in `tessera/seed/`):

- `raw.ami_interval_reads` — meter_id, read_ts (15-min), kwh_delivered, kwh_received, quality_flag
- `raw.meters` — meter_id, premise_id, feeder_id, install_date, meter_type
- `raw.feeders` — feeder_id, substation_id, region, voltage_kv
- `raw.outage_events` — outage_id, feeder_id, start_ts, end_ts, cause_code, customers_affected
- `raw.head_end_vendor_feed` — the source the drift scenario will mutate

Seed size: 3 regions, 12 substations, 60 feeders, 5,000 meters, 90 days of 15-minute reads
(down-sample to hourly if laptop memory is tight; make it a config flag), ~400 outage events.

Two certified data products exist at start:

- `dp.outage_reliability` — SAIDI, SAIFI, CAIDI by feeder / substation / region / month
- `dp.meter_consumption_daily` — daily kWh by meter and feeder

Users: `alice` (regional ops manager, region = NORTH), `maria` (analyst, NORTH),
`deshawn` (ops manager, NORTH), `raj` (data engineer, approver), `admin`.
Seed questions: `examples/seed-questions.csv`.

## Demo storyline (the acceptance script for M6)

1. **Certified answer.** Alice asks "What was SAIDI by substation in my region last month?"
   The answer returns a table plus a certificate: certified metric, entitlement passed (row filter
   region = NORTH), data fresh within SLA, quality score, 0% agent-authored lineage.
2. **Gap.** Alice and two seeded users ask variants of "Which meters reported zero usage during
   outages?" Tessera cannot answer; each question is logged with outcome `missing_data`.
3. **Demand mining.** The Demand Miner clusters the questions into one intent, finds the join path
   meters → feeders → outage_events → ami_interval_reads through lineage and catalog search,
   and drafts contract `dp.meter_outage_exposure` with a demand score.
4. **Build.** Raj approves. The Pipeline Builder generates the SQL model and contract-derived tests,
   runs them on a draft schema, and the Publisher promotes it. Provenance records are written.
5. **Closure.** Tessera replays the three original questions; all now resolve. Askers get a
   notification. The certificate on these answers shows the agent-authored model and Raj's approval.
6. **Drift.** Run `make drift` to rename `kwh_delivered` → `energy_kwh_del` and change its type to
   string in the vendor feed. The Drift Healer detects it, computes blast radius (both consumption
   products and the new exposure product), generates three candidate patches, shadow-runs each,
   and auto-merges the one whose outputs stay within tolerance. A deliberately wrong candidate
   (cast that loses decimals) is rejected by the gate, visibly.
7. **Proof.** Alice re-asks question 1. Answer unchanged; certificate now lists the healed node,
   the patch's provenance, and the gate evidence.

## Tech stack

- Backend: Python 3.11, FastAPI, Pydantic v2, DuckDB, sqlglot (parsing, column lineage),
  networkx (lineage graph), `cryptography` (Ed25519 signing), scikit-learn (TF-IDF + clustering in
  mock mode), Anthropic Python SDK (live mode; model name from env, default `claude-sonnet-5-5`).
- Frontend: React + Vite + TypeScript, Tailwind, TanStack Query. No component library required.
- Optional: Snowflake adapter using `snowflake-connector-python`; zero-copy clone via `CREATE ... CLONE`.
