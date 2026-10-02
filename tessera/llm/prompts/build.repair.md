# build.repair

The model SQL below failed validation or contract-derived tests. Fix only what the failures describe
and return the complete corrected statement (same target, same sources).

Typical fixes: cast to the contract type (e.g. `double`, not DECIMAL), COALESCE aggregates over LEFT
JOINs for non-nullable columns, keep flags consistent with the expectations.

Output JSON schema: `{"sql": "CREATE TABLE draft.<name> AS SELECT ..."}`

Variables:
{{variables}}
