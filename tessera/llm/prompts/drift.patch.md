# drift.patch

A source table changed shape (see `changes`). Propose expressions that map the new column back onto
the column the ingestion mapping (`mapping_sql`) expects, so every downstream product keeps its
contract. Deterministic candidates (`deterministic`) are already being tried; propose different ones.

Each candidate is a SQL expression over the source table's columns (DuckDB dialect). Candidates are
shadow-run and gated on divergence against the last-known-good outputs, so prefer loss-free casts.

Output JSON schema:
```json
{"candidates": [{"label": "short name", "expression": "CAST(energy_kwh_del AS DOUBLE)", "rationale": "why"}]}
```

Variables:
{{variables}}
