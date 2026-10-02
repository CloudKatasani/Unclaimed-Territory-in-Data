# demand.contract

Draft a data product contract (YAML) that would answer the intent below, using only the listed
source tables and columns (PII columns have already been removed) and the given join conditions.

Requirements:
- `product` must be `dp.<snake_case>`; `origin: demand_miner`; `owner: raj`.
- `grain` and `primary_key` must be columns in `schema`; every column has a `type` and `nullable`.
- Add `quality.expectations` as SQL predicates over output columns, `foreign_keys` back to sources,
  a `row_filter` policy on `region` when the product has a region column, and `semantic` measures
  (uncertified) with synonyms that a business user would type.
- If `errors` is non-empty, fix exactly those problems.

Output JSON schema: `{"contract_yaml": "<yaml text>"}`

Example output (abridged):
```yaml
product: dp.meter_outage_exposure
version: 1
title: Meter usage during outages
owner: raj
origin: demand_miner
grain: [meter_id, outage_id]
primary_key: [meter_id, outage_id]
sources: [raw.meters, raw.feeders, raw.outage_events, raw.ami_interval_reads]
schema:
  - {name: meter_id, type: varchar, nullable: false}
  - {name: kwh_in_window, type: double, nullable: false}
quality:
  expectations:
    - {name: kwh_non_negative, sql: kwh_in_window >= 0}
criticality: 2
```

Variables:
{{variables}}
