# 07 — New capabilities for the client demo

Five additions on top of the Tessera base (`03-agents.md`). Each one is a "wow moment" in the demo
script and maps to an idea in the ideas doc.

## 1. Data recall notices (idea 1)

**What the audience sees.** After the drift fix, Alice opens her Exports page and finds one export
marked *Restated*: "SAIDI for substation S-04 (exported 28 Sep) changed from 118.2 to 131.4 minutes.
Reason: vendor feed field rename, corrected 2 Oct." A banner on the Ask screen says 1 of her past
answers was recalled.

**Mechanism.**

- Every served answer already has a certificate with `lineage_nodes` and a `result_hash`. Add
  `meta.answer_snapshots` (`cert_id`, `plan_json`, `result_parquet_path`, `served_at`).
- On `patch.merged` or any publish that changes a product (`product.published` with `restated=true`),
  the **Recall job** selects certificates whose `lineage_nodes` intersect the changed objects and whose
  `served_at` falls in the affected window, re-executes `plan_json` against current data, and diffs.
- Materiality: per consumer setting `materiality_pct` (default 1%). Only diffs above it produce a
  `meta.recall_notices` row (`notice_id`, `cert_id`, `consumer`, `old_hash`, `new_hash`, `delta_json`,
  `reason`, `issued_at`, `acknowledged_at`).
- Notices appear in the UI inbox and on the original answer; narrative sentences bound to a recalled
  query turn amber (capability 4).

**Acceptance (`tests/test_m8_recall.py`).** Step 6 of the storyline produces exactly one notice for
Alice (the S-04 export) and none for Raj; a 0.5% delta below materiality produces none; acknowledging
clears the banner.

## 2. Sentinel records (idea 3)

**What the audience sees.** A "Pipeline truth" panel: 14 sentinel rows injected at `raw.ami_interval_reads`
and `raw.outage_events`, expected values at every downstream product, all green. When the lossy
INTEGER-cast candidate is shadow-run in the drift scenario, the sentinel check on
`dp.meter_consumption_daily` goes red before the distribution gate even runs.

**Mechanism.**

- `sentinel.generate(contract)` creates rows per source with keys in a reserved range
  (`meter_id LIKE 'SNTL-%'`, `outage_id LIKE 'SNTL-%'`) and chosen values (zeros, boundary timestamps,
  max precision decimals).
- `sentinel.expect(model_sql, sentinel_rows)` evaluates the parsed SQL (sqlglot AST) over the sentinel
  rows only, in DuckDB, to produce expected output rows per product. This is the symbolic step: run the
  transformation on the sentinel subset in isolation.
- `sentinel.verify(product)` queries the published product for sentinel keys and compares to expected.
- The Policy Engine adds `AND key NOT LIKE 'SNTL-%'` to every served query, so sentinels never reach
  an answer. The certificate records `sentinel_status` per lineage node.

**Acceptance (`tests/test_m9_sentinel.py`).** Expected rows match actual for both seeded products;
the lossy candidate fails sentinel verification on `kwh_in_window`; no answer in the storyline contains
a sentinel key.

## 3. Decision-first self-serve with sensitivity (idea 18)

**What the audience sees.** Alice types "Should I request mutual aid for substation S-04 tonight?" and
gets: the registered thresholds for that decision, the current values with margin, and the sentence
"Two more outages over four hours this month and NORTH breaches its SAIDI target of 120 minutes."

**Mechanism.**

- Decision packs register thresholds (`06-marketplace.md`). The Answer Agent's plan step gains a
  `decision` branch: if the question matches a decision (synonym + embedding match on
  `question_examples`), it evaluates each threshold metric at the stated scope.
- Sensitivity: for each threshold, a counterfactual sweep increments the driving measure in the
  semantic model (outage count, duration, customers affected) until the comparator flips; the smallest
  increment is reported in plain words using the metric's `sensitivity_template` from the contract.
- The certificate covers every metric used, and the decision pack's aggregated verdict is shown.

**Acceptance (`tests/test_m10_decision.py`).** The mutual-aid question resolves to the
`storm_restoration_crew_allocation` pack; margins match a hand-computed value; the flip sentence is
produced from the template with the correct number.

## 4. Verified narratives (idea 19)

**What the audience sees.** Under every answer, a three-sentence summary. Hovering a sentence shows the
query behind it and its certificate verdict. After the recall, one sentence is amber with "restated".

**Mechanism.**

- The Answer Agent calls `prompt: narrative.write` with the result table and plan; the output schema
  is a list of `{sentence, query_ref}` where `query_ref` must name a sub-query the agent already ran
  (the main plan, or a declared drill-down). Sentences with no `query_ref` are dropped and logged.
- `meta.narrative_bindings` (`cert_id`, `sentence_no`, `query_ref`, `result_hash`).
- The Recall job also diffs bound sub-queries, so sentences can be recalled individually.

**Acceptance (`tests/test_m10_narrative.py`).** Every sentence has a binding; a fixture that returns an
unbound sentence yields a narrative one sentence shorter and a log entry; the S-04 sentence is amber
after step 6.

## 5. Consumer migration agent (idea 17)

**What the audience sees.** Raj publishes `dp.outage_reliability` v2, which renames `saidi` to
`saidi_minutes` and adds `cause_category`. The Builds screen shows three consumers (Alice's saved
question, the restoration dashboard, `outage_copilot` v2) each with a proposed rewrite, a shadow-run
diff of zero, and an Accept button. Raj clicks Accept-all.

**Mechanism.**

- Consumer lineage: `meta.consumers` (`consumer_id`, `kind` = saved_question | dashboard | agent,
  `fqn_refs`, `artifact_text`).
- On publish with `breaking=true`, the **Migration agent** parses each consumer artifact (SQL via sqlglot,
  semantic references via the semantic model, agent prompts via `prompt: migrate.prompt`), applies the
  rename map from the contract diff, shadow-runs old vs new, and writes `meta.migrations`
  (`consumer_id`, `old_text`, `new_text`, `divergence`, `status`).
- Accept swaps the artifact and records provenance; reject holds the consumer on v1 with a sunset date.

**Acceptance (`tests/test_m11_migration.py`).** Three migrations are proposed; all show zero
divergence; accepting updates the saved question so step 7's re-ask still works.

## Prompt files added

`narrative.write`, `migrate.prompt`, `decision.match`. Mock fixtures must cover the demo script in
`08-client-demo.md` end to end.
