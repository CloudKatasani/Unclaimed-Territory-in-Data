# 03 — Agents

Each agent is a Python class with `run(input) -> output`, pure with respect to its inputs plus the
governance spine. LLM calls go through `tessera.llm`. Every agent writes provenance for what it creates.

---

## 1. Answer Agent (B1)

**Input:** question text, user principal (id, role, region).
**Output:** result table, certificate, question log row.

Steps:

1. Retrieve candidate semantic elements by synonym match + TF-IDF over names/descriptions (top 15).
2. LLM (`prompt: answer.plan`) returns a structured plan: metrics, dimensions, filters, time window,
   grain. The plan may only reference retrieved elements; unknown references → outcome `missing_data`
   with the unmatched terms recorded in `plan_json.unmatched`.
3. Compile plan → SQL deterministically from the semantic model (no free-form LLM SQL).
4. Policy Engine rewrites the SQL: inject row filters, apply column masks. Record each decision.
5. Execute. Collect freshness and quality from product metadata.
6. Walk lineage from the result columns to raw sources; collect provenance for each node.
7. Build and sign certificate; apply certificate rules; set verdict.
8. Log the question with outcome and confidence.

Confidence = plan coverage (matched / requested terms) × retrieval score; < 0.6 → `low_confidence`.

---

## 2. Demand Miner (B2) — core of ID-1

**Trigger:** on demand from UI, or every 5 new unresolved questions.
**Input:** questions with outcome in (`missing_data`, `low_confidence`, `abandoned`) and no intent.
**Output:** demand intents; draft contracts.

Steps:

1. Embed questions (mock: TF-IDF; live: LLM-normalized intent text then TF-IDF). Agglomerative
   clustering, cosine distance threshold 0.45 (config).
2. LLM (`prompt: demand.intent`) per cluster: label, required entities, measures, grain, time window.
3. Gap classification against the semantic model (deterministic): which required elements exist,
   which do not, and why (`gap_type`).
4. Source resolution: search catalog columns by name/description similarity for each missing element;
   then use the lineage graph and declared foreign keys to find a join path linking them to existing
   entities (networkx shortest path over entity-key graph). Record path and confidence.
5. LLM (`prompt: demand.contract`) drafts contract YAML from intent + gap + sources. Validate against
   the contract JSON schema; fix-up loop max 2 retries.
6. Compute demand score; write intent + draft contract; emit `contract.drafted`.
7. On `product.published` for a contract with `demand_intent_id`: replay every question in the
   intent through the Answer Agent; set `resolved_by_product` on success; report closure rate.

Guardrail: never draft a contract whose sources include a column classified PII unless the contract
declares a masking policy for it.

---

## 3. Pipeline Builder (A1)

**Trigger:** `contract.approved`.
**Input:** approved contract.
**Output:** model SQL + tests in `draft_<job_id>`, `build.ready_for_gate`.

Steps:

1. Read source schemas and a capped sample (policy-masked).
2. LLM (`prompt: build.model`) generates a single `CREATE TABLE AS SELECT` in the draft schema.
   sqlglot validates: statement type, referenced tables ⊆ contract sources, output columns = contract schema.
3. Derive tests from the contract deterministically (this is the A1 mechanism):
   - schema: column names, types, nullability
   - keys: uniqueness of declared primary key at declared grain
   - expectations: each `quality.expectations` entry → SQL assertion
   - referential: declared FKs exist in parents
   - property tests: generate synthetic edge-case rows from the contract (nulls where allowed,
     boundary timestamps, zero values) into a temp source copy; assert model output satisfies
     expectations on them too
4. Run model + tests in the draft schema. On failure, LLM repair loop (max 3) with test failures as input.
5. Write provenance for model and tests; emit `build.ready_for_gate`.

---

## 4. Drift Healer (A2) — core of ID-2

**Trigger:** `drift.detected` from the ingestion check (compares each raw table's live schema and
column profiles to the last snapshot in `meta`).
**Input:** drift event (table, change list).
**Output:** merged patch or PR-style held patch with evidence.

Steps:

1. Classify drift: rename (name similarity + profile similarity ≥ 0.9), type change, add, drop.
2. Blast radius: downstream fqn.columns from `lineage_edges`; join `criticality`.
3. Candidate generation: deterministic candidates (alias map, cast) + LLM candidates
   (`prompt: drift.patch`), up to 3 total. The demo must include one deliberately lossy candidate
   (`CAST(energy_kwh_del AS INTEGER)`) via mock fixture, so the gate visibly rejects it.
4. For each candidate: `clone_schema` raw + affected models into `shadow_<job>_<n>`, apply patch,
   rebuild affected models over the last 7 days.
5. Baseline: the last-known-good outputs for the same window (kept as a snapshot before drift).
6. Divergence per affected object:
   - row count ratio, null-rate delta per column, distinct-count ratio
   - numeric columns: relative difference of sum and of p50/p95, plus max key-level abs diff
   - divergence score = max of normalized metrics
7. Threshold by criticality: 4 → 0.0001, 3 → 0.001, 2 → 0.01, 1 → 0.05 (config).
8. Pick the passing candidate with lowest max divergence; emit `patch.ready_for_gate` with evidence.
   If none pass: hold downstream refreshes for affected products, open a held patch with evidence.

---

## 5. Publisher (gate)

Not an LLM agent. Promotes from draft/shadow to `dp` when:

- builds: all contract tests pass and the contract is approved
- patches: gate evidence shows every affected object under its threshold

On promote: swap tables atomically, rebuild lineage edges for touched models, write provenance with
`approved_by` = the approving human (builds) or `auto-gate` (patches, criticality ≤ 3) — for
criticality-4 objects a patch needs a human approver even if it passes, otherwise certificate rule 4
blocks answers. In the demo, Raj approves the patch for `dp.outage_reliability` in one click.

---

## Prompt files

`tessera/llm/prompts/{answer.plan, demand.intent, demand.contract, build.model, build.repair,
drift.patch}.md`. Each prompt states the JSON output schema and includes 1–2 worked examples from the
utility domain. Mock fixtures in `tessera/llm/fixtures/<prompt_id>/<variables_hash>.json` must cover the
full demo storyline.
