# 06 — Marketplace: data products, agents, decision packs

The marketplace is the front door of the client demo. Business users never see tables; they see
**decision packs**, **data products** and **agents**, each with evidence instead of star ratings.

## Listing kinds

| Kind | Contract file | Evidence shown on the card |
| --- | --- | --- |
| Data product | `contract.yaml` (see `02-data-model.md`) | certificate verdict, freshness, quality score, demand score, SLA credits paid last 30 days |
| Agent | `agent-contract.yaml` (see `examples/agent-contract-outage-copilot.yaml`) | verified accuracy on replayed questions, cost per run, data products required, last shadow-trial verdict |
| Decision pack | `decision-pack.yaml` (see `examples/decision-pack-storm-restoration.yaml`) | aggregated certificate across bundle, thresholds registered, subscribers |

All three are rows in `meta.listings` (`listing_id`, `kind`, `fqn`, `version`, `status`, `spec_yaml`,
`evidence_json`, `published_at`). Evidence is recomputed by a nightly job and on every publish.

## Agent product contracts (idea 13)

```yaml
agent: outage_copilot
version: 2
purpose: Answer outage, reliability and restoration questions for regional managers
inputs: [natural_language_question, user_principal]
outputs: [answer_table, narrative, certificate]
requires:
  data_products: [dp.outage_reliability, dp.meter_outage_exposure]
  policies: [row_filter.region]
  purpose: operational_reporting
budget:
  max_credits_per_run: 0.05
  max_runs_per_day: 500
evaluation:
  replay_set: meta.questions where outcome = answered and ground_truth is not null
  min_verified_accuracy: 0.95
```

### Evidence ranking

- A **replay set** is built from `meta.questions` rows that have a `ground_truth_hash` (set when a human
  marks an answer correct in the UI, or when the question is a seeded benchmark).
- `evaluate_agent(agent, version)` replays every question in mock LLM mode, compares result hashes,
  and writes `meta.agent_evaluations` (`agent`, `version`, `n`, `accuracy`, `avg_cost`, `p95_latency`,
  `evaluated_at`, `signature`).
- The store sorts by accuracy, then cost. The card shows the sentence
  `Verified accuracy 97.2% on 1,240 replayed questions (evaluated 2 Oct 2026)`.

## Entitlement bundles (idea 14)

Subscribing to an agent or decision pack calls `bundle.resolve(listing, principal)`:

1. Collect `requires.data_products`, walk lineage one level up for anything the agent's SQL touches.
2. Intersect with the principal's entitlements; anything outside → the subscription is created in
   `pending_approval` with the missing grants listed.
3. Issue one **lease** per data product (`meta.leases`: `lease_id`, `principal`, `fqn`, `purpose`,
   `scope_json`, `expires_at`, `bundle_id`) and one budget row.
4. Unsubscribe revokes every lease with the same `bundle_id`.

The Access panel in the UI lists leases grouped by bundle, so the audience sees exactly what the agent
was given.

## Shadow-mode trials (idea 15)

`trial.start(agent, candidate_version, days)` registers a trial. The orchestrator then runs every
live question for that agent through both versions; the incumbent's answer is served, the
candidate's is stored. `meta.agent_trials` keeps per-question divergence (same metric set as the
drift gate: row count ratio, numeric relative diff, key-level diffs). The listing shows a
"Trial: 3 days, 212 questions, 0.4% divergent, −18% cost" strip and a **Promote** button that
applies the gate thresholds from `03-agents.md`.

Demo fixture: `outage_copilot` v2 uses the new `dp.meter_outage_exposure` product and answers the
zero-usage questions v1 could not; divergence on all other questions is zero.

## Decision packs (idea 16)

```yaml
decision: storm_restoration_crew_allocation
owner: ops_director
question_examples:
  - Which feeders should get crews first after tonight's storm?
  - How many customers are still out by substation?
bundle:
  data_products: [dp.outage_reliability, dp.meter_outage_exposure, dp.meter_consumption_daily]
  agents: [outage_copilot]
  dashboards: [dash.restoration_board]
thresholds:
  - metric: saidi_minutes_mtd
    scope: region
    comparator: ">"
    value: 120
    meaning: Region breaches monthly SAIDI target
  - metric: customers_out
    scope: substation
    comparator: ">"
    value: 2000
    meaning: Escalate to mutual-aid request
```

Thresholds feed **decision-first self-serve** (`07-new-capabilities.md`). The pack's certificate is the
worst verdict across its bundle, and its card lists every threshold with the current margin.

## SLA credits (idea 21)

`meta.sla_ledger` (`entry_id`, `fqn`, `consumer`, `breach_kind`, `evidence_cert_id`, `credits`,
`settled_at`). The Publisher writes an entry whenever a certificate records a freshness or quality
breach for a product with `sla.credits_per_breach` in its contract. The marketplace card shows credits
paid in the last 30 days. In the demo the drift scenario's held refresh creates the first entry.

## API

```
GET  /market/listings?kind=&q=            # cards with evidence
GET  /market/listings/{id}                # detail, contract, evidence history
POST /market/listings/{id}/subscribe      # resolves bundle, issues leases
POST /market/listings/{id}/unsubscribe
POST /market/agents/{agent}/trials        # start shadow trial
POST /market/agents/{agent}/promote       # apply gate, bump version
GET  /market/ledger?consumer=             # SLA credits
```
