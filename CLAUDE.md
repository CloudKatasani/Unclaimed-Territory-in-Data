# Tessera — Claude Code project instructions

Tessera is a prototype of a governed, agentic data platform. AI agents build and repair
pipelines; business users ask questions in plain English and get answers that carry a
signed trust certificate. Unanswered questions automatically become new data products.

Read the specs in this order before writing code:

1. `docs/00-overview.md` — what we are building and the demo storyline
2. `docs/01-architecture.md` — components and how they call each other
3. `docs/02-data-model.md` — metadata tables (contracts, lineage, provenance, questions, certificates)
4. `docs/03-agents.md` — the five agents, their inputs, outputs and guardrails
5. `docs/04-ui.md` — screens
6. `docs/05-build-plan.md` — milestones with acceptance tests; build in this order
7. `docs/06-marketplace.md` — data products, agents and decision packs as listings with evidence (M8+)
8. `docs/07-new-capabilities.md` — recall notices, sentinel records, decision-first answers, verified narratives, consumer migration (M8–M11)
9. `docs/08-client-demo.md` — the 12-step client demo, conductor panel and reset (M11)

## Ground rules

- Build milestone by milestone. Do not start a milestone until the previous one's acceptance tests pass.
- Every agent must run in two modes: `LLM_MODE=live` (Anthropic API) and `LLM_MODE=mock`
  (deterministic fixtures in `tessera/llm/fixtures/`). All tests run in mock mode with no network.
- Never let an agent write to a published table directly. Agents write to a draft schema; only the
  Publisher promotes, and only after its gate passes.
- Every artifact an agent creates gets a provenance record (see `docs/02-data-model.md`). No exceptions.
- Keep the warehouse behind the `Warehouse` interface. DuckDB is the default; the Snowflake adapter
  is optional and must not be required for tests.
- Python 3.11, type hints everywhere, `ruff` + `mypy --strict` clean, `pytest` for tests.
- Do not invent business numbers in UI copy; everything shown comes from the seeded data.

## Commands (create these in M0)

```
make setup      # venv, deps, seed DuckDB
make test       # pytest, mock mode
make demo       # API on :8000, UI on :5173
make reset      # drop and reseed the demo warehouse
```

## Confidentiality

This prototype supports pending invention disclosures. Keep the repository private. Do not push
to a public remote, publish packages, or post screenshots until counsel confirms a filing.
