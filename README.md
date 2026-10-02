# Tessera

Prototype of a governed, agentic data platform. Business users ask questions in plain English and
get answers with a signed trust certificate; unanswered questions become new data products that AI
agents build; agents heal pipelines under schema drift behind a verified gate.

> **Confidential.** Supports pending invention disclosures — see `CLAUDE.md`. Keep this repository private.

Specs: `docs/00-overview.md` … `docs/08-client-demo.md`. Demo script: `scripts/demo_walkthrough.md`.

## Quick start

```
make setup      # venv, deps, seed DuckDB, npm install
make test       # pytest, mock LLM mode, no network
make demo       # API on :8000, UI on :5173
make reset      # drop and reseed the demo warehouse
make drift      # simulate the vendor schema change and run the Drift Healer
make fixtures   # re-record mock LLM fixtures by replaying the storyline
make check      # ruff + mypy --strict + pytest
```

## Status

| Milestone | Scope | State |
| --- | --- | --- |
| M0 | Skeleton, DuckDB warehouse, meta schema, seed | done — `tests/test_m0_seed.py` |
| M1 | Governance spine: contracts, semantic model, policy engine, lineage, provenance | done — `tests/test_m1_governance.py` |
| M2 | Answer Agent + signed certificates (ID-3) | done — `tests/test_m2_answer.py` |
| M3 | Demand Miner (ID-1) | done — `tests/test_m3_demand.py` |
| M4 | Pipeline Builder + Publisher (A1) | done — `tests/test_m4_build.py` |
| M5 | Drift Healer (ID-2) | done — `tests/test_m5_drift.py` |
| M6 | UI + end-to-end storyline | done — `tests/test_m6_e2e.py` (API-level) |
| M7 | Snowflake adapter (optional) | adapter skeleton only |
| M8–M11 | Marketplace, recall, sentinels, decision-first, narratives, migration, conductor | not started |

## Design notes and deviations

- **Read granularity.** Seed defaults to hourly AMI reads (10.8M rows), the down-sample the spec allows
  for laptops; set `TESSERA_READ_INTERVAL_MIN=15` for 43.2M rows. Recorded fixtures are keyed to the
  hourly sample; at 15 minutes the mock planner's rule-based responder produces the same plans.
- **AMI-verified SAIDI.** `dp.outage_reliability` counts an outage as restored when half of the feeder's
  meters report usage again (or at the OMS end time). That is why the vendor feed drift reaches the
  regulator-reported product, and why the vendor's restatement of the 14 Aug S-04 outage changes August
  SAIDI (the hook for the M8 recall) while September — storyline step 1 — is unchanged.
- **Ingestion mapping.** `raw.ami_interval_reads` is a view over the landing table
  `raw.head_end_vendor_feed`; drift patches edit that mapping. The gate shadow-runs each candidate over
  the last 7 days against a last-known-good snapshot of the feed (`lkg` schema).
- **Mock LLM.** `LLM_MODE=mock` first looks up `tessera/llm/fixtures/<prompt>/<hash>.json`, then falls back
  to the deterministic responders in `tessera/llm/heuristics.py` (which are what `make fixtures` records).
  `tests/test_m6_e2e.py` asserts the storyline is served from recorded fixtures only.
- **Determinism.** Frozen clock (`DEMO_NOW`), deterministic ULIDs from persisted counters, signing key
  derived from `TESSERA_KEY_SEED`. Float aggregates are compared at 1e-9 in the drift gate because
  DuckDB's parallel summation changes the last bits between runs.
- **Extra meta tables** beyond `docs/02`: `models`, `products`, `catalog_columns`, `foreign_keys`,
  `schema_snapshots`, `drift_events`, `patch_candidates`, `patches`, `build_jobs`, `notifications`,
  `metrics`, `events`, `rejections`, `llm_calls`.
