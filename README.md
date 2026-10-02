# Tessera

Prototype of a governed, agentic data platform. Business users ask questions in plain English and
get answers with a signed trust certificate; unanswered questions become new data products that AI
agents build; agents heal pipelines under schema drift behind a verified gate.

> **Confidential.** Supports pending invention disclosures — see `CLAUDE.md`. Keep this repository private.

Specs: `docs/00-overview.md` … `docs/08-client-demo.md`. Demo script (12 steps, conductor):
`scripts/demo_walkthrough.md`.

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
| M6 | UI + end-to-end storyline | done — `tests/test_m6_e2e.py` (API-level; browser run checked manually) |
| M7 | Snowflake adapter (optional) | adapter skeleton only, untested (no credentials) |
| M8 | Marketplace, entitlement bundles, SLA credits, recall notices | done — `tests/test_m8_market.py`, `tests/test_m8_recall.py` |
| M9 | Sentinel records, agent evaluation, shadow trials | done — `tests/test_m9_sentinel.py`, `tests/test_m9_agents.py` |
| M10 | Decision-first answers, verified narratives | done — `tests/test_m10_decision.py`, `tests/test_m10_narrative.py` |
| M11 | Consumer migration, demo conductor, reset/replay | done — `tests/test_m11_migration.py`, `tests/test_m11_conductor.py` |

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
- **Seeded history and scripted events.** Two exports served before the demo (Alice, 28 Sep: August SAIDI by
  substation; Raj, 29 Sep: August SAIFI) feed the recall; a 1–2 Oct storm on S-04 feeders puts NORTH's
  month-to-date SAIDI just under its 120-minute target; 14 sentinel rows (`SNTL-` keys) live in the feed and
  outage table and are excluded from every served answer and from the M0 seed counts.
- **Agent evidence.** Accuracy is measured by replaying a seeded ground-truth benchmark (gold plans executed
  under each asker's policies), not quoted. Today's mock planner scores 100% on it; the spec's 97.2% / 1,240
  figures are illustrative and are not hard-coded anywhere. outage_copilot v2 carries a tighter retrieval budget
  (`runtime.retrieval_top_k: 8`) — the measured cost change is about −2%, not the spec's illustrative −18%.
  Trials report new capability (questions only the candidate answers) separately from regressions; only
  regressions count against the promotion gate.
- **Entitlement bundles.** A bundle is the agent's declared products plus the products its own replayed queries
  touched (recorded at evaluation): that is how subscribing Alice to v1 yields the spec's two leases.
- **Recall scope.** Besides Alice's export, Deshawn's zero-usage answer (no time window, so it spans August) is
  legitimately recalled too; the acceptance tests check "exactly one for Alice, none for Raj".
- **Breaking changes.** `dp.outage_reliability` v2 renames `saidi` → `saidi_minutes` (declared via a new optional
  contract field `renames`) and adds `cause_category`; v1 is archived as `dp_archive.outage_reliability__v1`;
  the old semantic name remains a deprecated alias until consumers migrate.
