# 01 — Architecture

```
                 ┌──────────────────────── UI (React) ────────────────────────┐
                 │  Ask   │  Certificate  │  Demand Board  │  Builds  │ Drift  │
                 └────────────────────────────┬───────────────────────────────┘
                                              │ REST (FastAPI)
┌─────────────────────────────────────────────┴──────────────────────────────────────┐
│                                   Orchestrator                                     │
│   event bus (in-process, asyncio queues) · job runner · approval workflow           │
├───────────────┬───────────────┬────────────────┬───────────────┬───────────────────┤
│ Answer Agent  │ Demand Miner  │ Pipeline       │ Drift Healer  │ Publisher (gate)  │
│ (B1)          │ (B2)          │ Builder (A1)   │ (A2)          │                   │
└──────┬────────┴──────┬────────┴───────┬────────┴──────┬────────┴─────────┬─────────┘
       │               │                │               │                  │
┌──────┴───────────────┴────────────────┴───────────────┴──────────────────┴─────────┐
│ Governance spine                                                                     │
│  Catalog + Contracts │ Semantic Model │ Policy Engine │ Lineage Graph │ Provenance   │
│                                                                         Ledger (A6)  │
├──────────────────────────────────────────────────────────────────────────────────────┤
│ Warehouse interface:  DuckDB (default)  |  Snowflake (optional)                     │
│ schemas: raw · draft_<job_id> · shadow_<job_id> · dp (published) · meta              │
└──────────────────────────────────────────────────────────────────────────────────────┘
```

## Package layout

```
tessera/
  api/            FastAPI routers: ask, questions, demand, contracts, builds, drift, lineage, certs
  agents/         answer.py, demand_miner.py, pipeline_builder.py, drift_healer.py
  governance/     catalog.py, contracts.py, semantic.py, policy.py, lineage.py, provenance.py
  publisher/      gate.py, promote.py
  warehouse/      base.py (Warehouse protocol), duckdb_wh.py, snowflake_wh.py
  llm/            client.py (live|mock), prompts/, fixtures/
  seed/           generate.py, drift.py
  orchestrator/   bus.py, jobs.py, approvals.py
ui/               Vite React app
tests/            one test module per acceptance criterion in docs/05-build-plan.md
```

## Key interfaces

```python
class Warehouse(Protocol):
    def query(self, sql: str, params: dict | None = None) -> pa.Table: ...
    def execute(self, sql: str) -> None: ...
    def clone_schema(self, src: str, dst: str) -> None: ...      # Snowflake: zero-copy CLONE; DuckDB: CTAS copy
    def table_schema(self, fqn: str) -> list[Column]: ...
    def drop_schema(self, name: str) -> None: ...

class LLM(Protocol):
    def complete_json(self, prompt_id: str, variables: dict, schema: type[BaseModel]) -> BaseModel: ...
    # mock mode resolves (prompt_id, hash(variables)) -> fixture file; live mode calls Anthropic with
    # the prompt template and validates the JSON against `schema`. Every call returns token usage
    # and the prompt hash so the caller can write provenance.
```

## Events on the bus

| Event | Emitted by | Consumed by |
| --- | --- | --- |
| `question.logged` | Answer Agent | Demand Miner (batched every N questions or on demand) |
| `contract.drafted` | Demand Miner | Approvals (Raj) |
| `contract.approved` | Approvals | Pipeline Builder |
| `build.ready_for_gate` | Pipeline Builder | Publisher |
| `product.published` | Publisher | Demand Miner (replay), Answer Agent (refresh semantic model) |
| `drift.detected` | ingestion check | Drift Healer |
| `patch.ready_for_gate` | Drift Healer | Publisher |
| `patch.merged` / `patch.rejected` | Publisher | UI, Provenance |

## Trust boundaries

- The LLM never sees raw row data beyond a capped sample (≤ 20 rows, PII columns masked by policy).
- Generated SQL is parsed with sqlglot before execution; only SELECT / CREATE TABLE AS SELECT / CREATE VIEW
  in draft or shadow schemas are allowed. Anything else is rejected and logged.
- Policy (row filters, column masks) is applied by the Policy Engine rewriting the plan, never by the LLM.
- Signing key for certificates and provenance lives in `meta.keys`, generated at setup; public key is
  exposed at `/certs/public-key` so certificates can be verified offline.
