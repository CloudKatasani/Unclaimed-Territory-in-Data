# Tessera demo walkthrough (storyline steps 1–7, M6)

Runs fully offline in `LLM_MODE=mock`. Every number on screen comes from the seeded warehouse.

```
make setup     # once: venv, deps, seed, npm install
make reset     # fresh warehouse (copies the seeded template, ~1s)
make demo      # API on :8000, UI on :5173
```

Open http://localhost:5173/?user=alice. Switch users with the selector in the header.

| # | Who | Where | Click | Say |
|---|-----|-------|-------|-----|
| 1 | Alice | Ask | Suggested question *What was SAIDI by substation in my region last month?* → **Verify** on the certificate | "Every number carries proof: certified metric, the row filter that was applied to Alice, freshness and quality, and who built each node on the path. The signature verifies offline against `/certs/public-key`." |
| 2 | Alice, Maria, Deshawn | Ask | Alice: *Which meters reported zero usage during outages last month?* Then switch to Maria (*How many meters showed no consumption while their feeder was out?*) and Deshawn (*List meters that read zero kWh during outage windows in the north region*) | "Tessera can't answer — usage and outages live in different products. It says so, shows what's missing, and logs the question instead of guessing." |
| 3 | Raj | Demand board | **Mine demand** → **Draft contract** → **View contract** | "Three people, three phrasings, one intent. The miner found the join path meters → feeders → outages → reads through the catalog and lineage, scored the demand, and drafted a contract." |
| 4 | Raj | Demand board → Builds | **Approve**, then open Builds | "A human approves; agents build. The first SQL draft failed its own contract tests (wrong type, NULLs over the outer join), the repair loop fixed it, and every derived test — schema, keys, expectations, FKs, synthetic edge cases — passes before the Publisher promotes it. Provenance shows `approved_by: raj`." |
| 5 | Alice | Notifications, Ask | Open **Notifications**, re-ask the zero-usage question | "The three original questions were replayed and resolved — closure 3/3. The certificate now shows the agent-built product and Raj's approval." |
| 6 | Raj | Drift | **Simulate vendor change** (or `make drift`) | "The vendor renamed `kwh_delivered` and sent it as text. Three candidate fixes were shadow-run against last week's known-good outputs. Rename-only doesn't build; the INTEGER cast loses decimals and the gate rejects it; rename + cast is exact and auto-merges for the two lower-criticality products. SAIDI is regulator-reported, so that refresh waits for a human." Click a red cell for the evidence drill-down, then **Approve for criticality-4 products**. |
| 7 | Alice | Ask | Re-ask question 1 | "Same answer, row for row. The certificate now lists the healed node, the patch's provenance with Raj's approval, and the gate evidence." |

Between steps 6 and the approval, asking question 1 returns a **blocked** certificate: an auto-gated agent
patch sits on the path of a criticality-4 product (certificate rule 4).

## Verifying from the command line

```
curl localhost:8000/certs/<cert_id>/verify
curl localhost:8000/provenance/verify
curl localhost:8000/metrics      # measures captured for the invention disclosures
```
