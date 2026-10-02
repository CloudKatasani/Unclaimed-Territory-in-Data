# 08 — Client demo: script, conductor, reset

A 15-minute demo for a utility client's data, operations and IT leaders. It runs entirely in mock
LLM mode so it is deterministic, offline-safe and resettable in under ten seconds.

## Demo conductor

Build a **Conductor** panel (route `/conduct`, hidden from the nav, opened with `?conductor=1`):

- Lists the twelve steps below with a "Run" button each; running a step performs the backend
  actions (ask a question as a given user, trigger drift, approve) and navigates the main window to
  the right screen. Use a BroadcastChannel so the conductor can drive a second browser window on
  the projector.
- "Reset" calls `POST /demo/reset` which drops and reseeds the warehouse and `meta`, then replays
  steps 0 to N if a step number is given (so you can restart from any point).
- Each step has a 1-line talk track shown only in the conductor window.
- Keyboard: `n` next step, `r` reset.

All seeded names, numbers and timestamps must be the same on every run (fixed random seed, frozen
clock at `2026-10-02T09:00:00-04:00` exposed via `DEMO_NOW`).

## Cast

| User | Role | What they want |
| --- | --- | --- |
| Alice | Regional ops manager, NORTH | Reliable numbers, fast, without asking IT |
| Raj | Data engineer | Fewer tickets, safe automation, proof for auditors |
| Priya | Ops director | Decisions, not dashboards |

## Script

| # | Screen | Action | What the audience sees | Idea |
| --- | --- | --- | --- | --- |
| 0 | Marketplace | Open as Priya | Decision packs first: *Storm restoration crew allocation* with its certificate and three thresholds; below, products and agents with evidence strips | 16, 13 |
| 1 | Agent store | Open `outage_copilot` | "Verified accuracy 97.2% on 1,240 replayed questions", cost per run, required products and policies | 13 |
| 2 | Agent store | Subscribe as Alice | Access panel fills with exactly two leases and a budget, grouped under the bundle | 14 |
| 3 | Ask | Alice: "What was SAIDI by substation in my region last month?" | Table, chart, certificate with verdict *release*, row filter sentence, "Built by" strip all human, three-sentence verified narrative | B1, 19 |
| 4 | Ask | Alice: "Which meters reported zero usage during outages last month?" | "I can't answer this yet"; unmatched terms; logged. Conductor also fires Maria's and Deshawn's variants | B2 |
| 5 | Demand board | Open as Raj | One intent, demand score, join path chips meters → feeders → outages → reads; Draft contract; Approve | B2, ID-1 |
| 6 | Builds | Watch the job | SQL generated, contract-derived tests pass, Publisher promotes `dp.meter_outage_exposure`; provenance JSON with `approved_by: raj`; replay closes 3/3 questions; Alice gets a notification | A1, A6 |
| 7 | Agent store | Start shadow trial of `outage_copilot` v2 | Trial strip: 212 questions, 0.4% divergent, −18% cost; Promote passes the gate | 15 |
| 8 | Pipeline truth | Open as Raj | 14 sentinel rows, all green across three products | 3 |
| 9 | Drift | Conductor runs `make drift` | Rename + type change detected; blast radius graph with criticality colors; three candidates; the INTEGER cast goes red on sentinels first, then fails the divergence gate; the correct patch auto-merges for two products and waits for Raj on `dp.outage_reliability`; Raj approves; SLA ledger shows the first credit | A2, 3, 21 |
| 10 | Inbox | Open as Alice | One recall notice: S-04 export restated 118.2 → 131.4 minutes with reason; her earlier narrative sentence is amber | 1, 19 |
| 11 | Ask | Priya: "Should I request mutual aid for substation S-04 tonight?" | Decision pack matched; thresholds with margins; "Two more outages over four hours this month and NORTH breaches its SAIDI target" | 18 |
| 12 | Builds | Raj publishes `dp.outage_reliability` v2 (rename `saidi` → `saidi_minutes`) | Three consumers get proposed rewrites with zero divergence; Accept-all; Alice's saved question still works | 17 |

Close on the Marketplace, where the decision pack's certificate now lists the healed node, the
agent-built product and the human approvals, all signed and verifiable with one click.

## Talk track (one line per step, shown in the conductor)

0. "Your people shop for decisions, not tables."
1. "Agents earn their place with evidence, not ratings."
2. "Subscribing gives the agent exactly the access it needs, and nothing it doesn't."
3. "Every number carries proof. Every sentence has a receipt."
4. "When the platform can't answer, it remembers the question."
5. "Demand becomes a contract. A human approves, agents build."
6. "The build is tested against its own contract and signed."
7. "A new agent version proves itself in the shadows before it goes live."
8. "Tracer rows prove the pipelines are still telling the truth, every run."
9. "A vendor changed a field overnight. Watch the platform heal itself, and refuse the wrong fix."
10. "The numbers Alice exported last week were wrong. She's the first to know, not the last."
11. "Ask about the decision and you get the number and the margin."
12. "Breaking changes migrate their own consumers."

## Adapting to a different client

Everything sector-specific lives in `tessera/seed/` (tables, products, decision packs, thresholds,
seed questions, sentinel definitions) and `tessera/llm/fixtures/`. To demo to a non-utility client,
replace the seed module and regenerate fixtures; the agents, marketplace and governance spine are
sector-neutral.

## Pre-demo checklist

- [ ] `make reset && make test` green in mock mode
- [ ] Conductor opened in a second window, projector window on `/market?user=priya`
- [ ] Run steps 0–12 once end to end the morning of the demo; reset
- [ ] Have the certificate verify endpoint ready to show on request (`/certs/{id}/verify`)
- [ ] Repository private; no screenshots in the leave-behind deck until counsel clears the filings
