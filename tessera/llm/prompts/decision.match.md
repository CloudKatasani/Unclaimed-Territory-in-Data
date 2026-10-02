# decision.match

Decide whether the question asks for a business decision covered by one of the decision packs below
(their example questions show the decisions they support). If so, return the pack id and the scope the
question names (substation, feeder or region identifiers exactly as written). If not, return null.

Output JSON schema: `{"decision": "pack id | null", "scope": {"substation": "S-04"}, "confidence": 0.0}`

Example — "Should I request mutual aid for substation S-04 tonight?":
`{"decision": "storm_restoration_crew_allocation", "scope": {"substation": "S-04"}, "confidence": 0.9}`

Variables:
{{variables}}
