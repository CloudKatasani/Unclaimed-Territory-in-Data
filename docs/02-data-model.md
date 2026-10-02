# 02 — Metadata data model (schema `meta`)

All tables live in the warehouse `meta` schema. IDs are ULIDs. Timestamps are UTC.

## contracts

| column | type | notes |
| --- | --- | --- |
| contract_id | text PK | |
| product_fqn | text | e.g. `dp.meter_outage_exposure` |
| version | int | increments on change |
| status | text | `draft` · `approved` · `published` · `deprecated` |
| spec_yaml | text | full contract, see `examples/contract-meter-outage-exposure.yaml` |
| origin | text | `human` · `demand_miner` |
| demand_intent_id | text FK null | set when origin = demand_miner |
| owner | text | |
| created_at, approved_at, approved_by | | |

## semantic_model

Entities, dimensions, measures and certified metrics, one row per element.
`element_type` (entity|dimension|measure|metric), `name`, `product_fqn`, `expression`, `grain`,
`certified` (bool), `certified_by`, `synonyms` (json).

## policies

`policy_id`, `kind` (row_filter|column_mask), `target_fqn`, `principal_attr` (e.g. `region`),
`expression` (e.g. `region = :user.region`), `classification` (e.g. `PII`).

## lineage_edges

Column-level. `src_fqn`, `src_column`, `dst_fqn`, `dst_column`, `transform_id` (FK to provenance
artifact), `created_at`. Built by parsing every model's SQL with sqlglot; rebuilt on publish.

## criticality

`fqn`, `criticality` (1 low … 4 regulatory), `reason`. Seed: `dp.outage_reliability` = 4
(SAIDI/SAIFI are regulator-reported), `dp.meter_consumption_daily` = 3, new products default 2.

## provenance (A6)

| column | type | notes |
| --- | --- | --- |
| artifact_id | text PK | |
| artifact_kind | text | `model_sql` · `test` · `contract` · `patch` |
| target_fqn | text | |
| content_sha256 | text | hash of the artifact body |
| agent | text | e.g. `pipeline_builder@0.1.0` |
| model_name | text | LLM model id, or `mock` |
| prompt_id, prompt_sha256 | text | |
| input_refs | json | contract ids, schemas sampled, parent artifacts |
| tests_run | json | name → pass/fail + metrics |
| gate_evidence | json null | for patches: per-object divergence and thresholds |
| approved_by | text null | null = no human approval |
| created_at | timestamp | |
| signature | text | Ed25519 over canonical JSON of all fields above |
| prev_artifact_id | text null | hash-chain to the previous version of the same target |

Append-only. Updates are new rows. A verification job recomputes signatures and the chain.

## questions (B2 input)

`question_id`, `asked_by`, `text`, `asked_at`, `outcome` (`answered` · `low_confidence` ·
`missing_data` · `refused_policy` · `abandoned`), `plan_json`, `confidence`, `intent_id` (set by
Demand Miner), `resolved_by_product` (set on replay), `resolved_at`.

## demand_intents

`intent_id`, `label`, `question_ids` (json), `required_elements` (json: entities, measures, grain,
time window), `gap_type` (`missing_attribute` · `missing_grain` · `missing_join_path` ·
`missing_domain`), `candidate_sources` (json: fqn.column with lineage path), `demand_score`,
`contract_id` (null until drafted), `status`.

`demand_score = distinct_askers × 2 + question_count + Σ business_weight(asker_role)`
(weights in config; ops_manager = 3, analyst = 1).

## certificates (B1 + A6)

| column | type | notes |
| --- | --- | --- |
| cert_id | text PK | |
| question_id | text FK | |
| metrics | json | name, certified, certified_by |
| policy_decisions | json | each policy applied, rewritten predicate |
| freshness | json | per product: last load, SLA, pass/fail |
| quality | json | per product: score, failing tests |
| lineage_nodes | json | every fqn.column on the answer's path |
| agent_authored | json | artifact_ids on the path with agent, approved_by |
| agent_share_unapproved | float | share of path nodes authored by agents without approval |
| verdict | text | `release` · `release_with_warning` · `block` |
| rule_trace | json | which certificate rules fired |
| signature | text | Ed25519 |
| issued_at | timestamp | |

### Certificate rules (config, evaluated in order)

1. Any metric not certified → `release_with_warning`.
2. Freshness SLA failed → `release_with_warning`.
3. Quality score < 0.9 → `release_with_warning`; < 0.7 → `block`.
4. Any unapproved agent artifact on the path of a criticality-4 product → `block`.
5. Otherwise `release`.
