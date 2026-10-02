# build.model

Write the SQL model for the data product contract below.

Rules:
- Exactly one statement: `CREATE TABLE {{target}} AS SELECT ...` (DuckDB dialect).
- Read only from the contract's `sources`; follow `join_path` exactly.
- Output columns must match the contract `schema` (names and types). Non-nullable columns must never be
  NULL — use COALESCE over outer joins.
- No DDL other than the single CREATE TABLE; no INSERT/UPDATE/DELETE.

You are given source schemas and a capped, policy-masked sample (`samples`). PII columns are removed.

Output JSON schema: `{"sql": "CREATE TABLE draft.<name> AS SELECT ..."}`

Example (abridged) for `dp.meter_outage_exposure`:
```sql
CREATE TABLE draft.meter_outage_exposure AS
SELECT m.meter_id, o.outage_id, ..., CAST(COALESCE(SUM(r.kwh_delivered), 0) AS DOUBLE) AS kwh_in_window
FROM raw.outage_events AS o JOIN raw.feeders AS f ON o.feeder_id = f.feeder_id ...
```

Variables:
{{variables}}
