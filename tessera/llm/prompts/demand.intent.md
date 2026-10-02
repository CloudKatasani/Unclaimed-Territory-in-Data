# demand.intent

Several business questions could not be answered. Summarise what they jointly ask for.

Return the business intent behind the cluster: a short human label, the entities involved (meter,
feeder, outage, substation), the domain concepts (from the glossary when possible), the measures
needed, the grain (key columns) and the time window most questions use.

Output JSON schema:
```json
{"label": "str", "entities": ["meter"], "concepts": ["zero_usage"], "measures": ["zero_usage"],
 "grain": ["meter_id", "outage_id"], "time_window": "last_month | null"}
```

Example — questions "Which meters reported zero usage during outages last month?", "How many meters
showed no consumption while their feeder was out?":
```json
{"label": "Meters reporting zero usage during outages", "entities": ["meter", "outage", "feeder"],
 "concepts": ["outage", "zero_usage"], "measures": ["zero_usage"], "grain": ["meter_id", "outage_id"],
 "time_window": "last_month"}
```

Variables:
{{variables}}
