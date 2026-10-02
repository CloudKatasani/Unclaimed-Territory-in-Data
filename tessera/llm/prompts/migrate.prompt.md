# migrate.prompt

A data product the agent depends on published a breaking change. Rewrite the agent's prompt text so
every reference to a renamed column or semantic element uses its new name (`renames` maps old -> new).
Change nothing else: keep wording, examples and JSON structure identical.

Output JSON schema: `{"text": "<the full rewritten prompt>"}`

Variables:
{{variables}}
