# narrative.write

Write at most three short sentences that summarise the answer for a business reader.

Rules:
- Every sentence must cite exactly one query in `results` via `query_ref` (`main`, `total`,
  `previous_period`, …) and use only numbers that appear in that query's rows. Sentences without a valid
  `query_ref` are discarded.
- No speculation, no causes that are not in the data, no numbers you computed yourself except simple
  differences between two cited values from the same query pair.

Output JSON schema: `{"sentences": [{"sentence": "S-04 had the highest SAIDI last month at 125.5 minutes.", "query_ref": "main"}]}`

Variables:
{{variables}}
