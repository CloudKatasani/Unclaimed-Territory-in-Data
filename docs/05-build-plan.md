# 05 — Build plan

Build in order. Each milestone ends with its acceptance tests green in mock mode (`make test`).
Test file names are given so Claude Code can create them first, then implement to pass.

## M0 — Skeleton and seed

- Repo layout per `01-architecture.md`, Makefile, pyproject, ruff, mypy, pytest, Vite app shell.
- Warehouse protocol + DuckDB implementation. Seed generator with fixed random seed.
- `meta` schema DDL from `02-data-model.md`. Ed25519 key generation at setup.

Acceptance (`tests/test_m0_seed.py`):
- seed produces the row counts in `00-overview.md` (±1% for random-sized tables)
- `dp.outage_reliability` and `dp.meter_consumption_daily` exist with certified metrics in `semantic_model`
- `meta` tables exist and are empty except seed rows

## M1 — Governance spine

- Catalog, contracts (YAML ↔ Pydantic, JSON-schema validation), semantic model loader,
  policy engine (sqlglot rewrite), lineage builder (sqlglot column lineage → `lineage_edges`),
  provenance ledger (canonical JSON, sign, hash-chain, verify).

Acceptance (`tests/test_m1_governance.py`):
- policy rewrite adds `region = 'NORTH'` for alice and nothing for admin
- lineage for `dp.outage_reliability.saidi` reaches `raw.outage_events.start_ts`, `end_ts`, `customers_affected`
- tampering with any provenance field makes `verify()` fail; chain break is detected

## M2 — Answer Agent + certificate (ID-3)

Acceptance (`tests/test_m2_answer.py`):
- storyline step 1 returns rows only for NORTH, verdict `release`, signature verifies
- an unknown-measure question is logged `missing_data` with unmatched terms
- inserting an unapproved agent artifact upstream of `dp.outage_reliability` flips verdict to `block`

## M3 — Demand Miner (ID-1)

Acceptance (`tests/test_m3_demand.py`):
- the three seeded zero-usage-during-outage questions form one intent
- gap type = `missing_join_path`; candidate path includes meters → feeders → outage_events and ami reads
- drafted contract validates against schema; demand score matches the formula
- a PII column without a masking policy is never included

## M4 — Pipeline Builder + Publisher (A1)

Acceptance (`tests/test_m4_build.py`):
- approving the contract produces SQL that sqlglot accepts and references only declared sources
- derived tests include schema, key, each expectation, FK, and property tests
- a mock fixture with a bad first SQL triggers exactly one repair and then passes
- publish promotes the table, rebuilds lineage, writes provenance with `approved_by = raj`
- replay resolves all three questions; closure = 3/3

## M5 — Drift Healer (ID-2)

Acceptance (`tests/test_m5_drift.py`):
- `make drift` produces a `drift.detected` event classified as rename + type change
- blast radius includes all three affected products with correct criticality
- the lossy INTEGER-cast candidate fails on `dp.meter_consumption_daily` (threshold 0.001) with evidence
- the correct candidate passes; auto-merges for criticality ≤ 3; held for approval on `dp.outage_reliability`
- after Raj approves, storyline step 7 answer equals step 1 answer row-for-row, and the certificate
  lists the patch artifact with `approved_by = raj`

## M6 — UI and end-to-end demo

- Screens per `04-ui.md`.
- `make demo` + `scripts/demo_walkthrough.md`: the seven storyline steps with what to click and say.

Acceptance (`tests/test_m6_e2e.py`, Playwright, optional in CI):
- runs storyline steps 1–7 through the API and asserts each expected state

## M7 (optional) — Snowflake adapter

- `snowflake_wh.py` using CLONE for shadow schemas, Snowflake row access policies and masking policies
  mirrored from `meta.policies`, Cortex as an optional LLM backend.
- Same test suite runs with `WAREHOUSE=snowflake` when credentials are present; skipped otherwise.

## M8 — Marketplace and recall (client demo, part 1)

- `meta.listings`, `meta.leases`, `meta.sla_ledger`, `meta.answer_snapshots`, `meta.recall_notices`.
- Marketplace, Agent store, Access panel and Inbox screens per `06-marketplace.md` and `04-ui.md`.
- Entitlement bundles; SLA credits; Recall job.

Acceptance (`tests/test_m8_market.py`, `tests/test_m8_recall.py`):
- subscribing Alice to `outage_copilot` issues exactly two leases and one budget row; unsubscribing revokes both
- the drift scenario's held refresh writes one `sla_ledger` entry
- recall produces one notice for Alice and none for Raj; a sub-materiality delta produces none

## M9 — Sentinel records and agent evidence

Acceptance (`tests/test_m9_sentinel.py`, `tests/test_m9_agents.py`):
- sentinel expected rows equal actual rows for both seeded products
- the lossy candidate fails sentinel verification before the divergence gate
- no served answer contains a sentinel key
- `evaluate_agent(outage_copilot, 1)` returns accuracy ≥ 0.95 on the seeded replay set; the store card shows the sentence
- a shadow trial of v2 records 0 divergence on non-zero-usage questions and promotes through the gate

## M10 — Decision-first self-serve and verified narratives

Acceptance (`tests/test_m10_decision.py`, `tests/test_m10_narrative.py`): as listed in `07-new-capabilities.md`.

## M11 — Consumer migration and demo conductor

- Migration agent; Conductor panel; `POST /demo/reset`; frozen clock; `scripts/demo_walkthrough.md`
  regenerated from `08-client-demo.md`.

Acceptance (`tests/test_m11_migration.py`, `tests/test_m11_conductor.py`):
- three migrations proposed with zero divergence; Accept-all keeps step 7 working
- running conductor steps 0–12 twice from reset yields identical certificates, hashes and notices

## Measures to capture for the invention disclosures

Log these during the demo run into `meta.metrics` so the disclosures can cite measured effects:

- ID-1: time from first unresolved question to resolution; closure rate on replay
- ID-2: candidates generated, rejected, divergence per candidate; downstream refreshes held vs. corrupted
- ID-3: certificate generation latency; share of answers by verdict
