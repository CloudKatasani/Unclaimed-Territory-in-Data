# Tessera client demo walkthrough (12 steps, docs/08)

Runs entirely in `LLM_MODE=mock`: deterministic, offline, resettable in seconds. Every number on
screen comes from the seeded warehouse; the conductor replays to identical certificates, hashes and
notices every time (`tests/test_m11_conductor.py`).

## Setup

```
make setup        # once
make reset        # fresh warehouse (copies the seeded template)
make demo         # API :8000, UI :5173
```

* Projector window: http://localhost:5173/market?user=priya
* Conductor window: http://localhost:5173/conduct?conductor=1 — **n** runs the next step, **r** resets.
  "Reset to before step N" reseeds and replays steps 0..N-1 (`POST /demo/reset?step=N-1`).
* Clock is frozen at `2026-10-02T09:00:00-04:00` (`DEMO_NOW`).

## Script

| # | Screen | Conductor does | Point at | Say |
|---|--------|----------------|----------|-----|
| 0 | Marketplace (Priya) | — | Decision pack first, its worst-of-bundle certificate and the two thresholds with live margins; products and agents below with evidence strips | "Your people shop for decisions, not tables." |
| 1 | Agent store | Opens outage_copilot | "Verified accuracy … on N replayed questions (evaluated 2 Oct 2026)", cost per run, required products and policies, signed evaluation history | "Agents earn their place with evidence, not ratings." |
| 2 | Access (Alice) | Subscribes Alice | Exactly two leases (each scoped `region = 'NORTH'`) and one budget, grouped under the bundle | "Subscribing gives the agent exactly the access it needs, and nothing it doesn't." |
| 3 | Ask (Alice) | "What was SAIDI by substation in my region last month?" | Chart, certificate *Release*, "Filtered to region NORTH for alice", Built-by strip all human, three bound sentences (hover for the query); **Verify** | "Every number carries proof. Every sentence has a receipt." |
| 4 | Ask (Alice) | Alice, Maria and Deshawn ask zero-usage variants | "I can't answer this yet", the uncovered terms, logged | "When the platform can't answer, it remembers the question." |
| 5 | Demand board (Raj) | Mines and drafts | One intent, demand score, join path meters → feeders → outage_events → ami_interval_reads; **View contract** | "Demand becomes a contract. A human approves, agents build." |
| 6 | Builds (Raj) | Approves | First SQL draft failed its own contract tests and was repaired; schema/key/expectation/FK/property tests pass; provenance `approved_by: raj`; closure 3/3, askers notified | "The build is tested against its own contract and signed." |
| 7 | Agent store (Raj) | Shadow trial of v2, then Promote | Trial strip: days, questions, % divergent (all of it new capability on zero-usage questions, 0 on the rest), cost change; gate pass | "A new agent version proves itself in the shadows before it goes live." |
| 8 | Pipeline truth (Raj) | Re-verifies sentinels | 14 sentinel records × products, all green; hover for expected vs actual | "Tracer rows prove the pipelines are still telling the truth, every run." |
| 9 | Drift (Raj) | Vendor renames `kwh_delivered` and sends it as text | Rename + type change; blast radius by criticality; rename-only fails to build, the INTEGER cast goes red on sentinels first; the exact cast auto-merges for two products and waits for Raj on `dp.outage_reliability` — click **Approve for criticality-4 products** (or let step 10 do it); Marketplace shows the first SLA credit | "A vendor changed a field overnight. Watch the platform heal itself, and refuse the wrong fix." |
| 10 | Inbox (Alice) | Approves the held patch if still pending | One recall notice: S-04 export of 28 Sep restated with old → new values and reason; **Open original answer**; her step-3 sentence comparing S-04 with August is amber | "The numbers Alice exported last week were wrong. She's the first to know, not the last." |
| 11 | Ask (Priya) | "Should I request mutual aid for substation S-04 tonight?" | Decision pack matched; thresholds with margins; "Two more outages over four hours this month and NORTH breaches its SAIDI target of 120 minutes." | "Ask about the decision and you get the number and the margin." |
| 12 | Builds → Consumer migrations (Raj) | Publishes `dp.outage_reliability` v2 (`saidi` → `saidi_minutes`, adds `cause_category`), Accept-all | Three consumers (Alice's saved question, restoration dashboard, outage_copilot v2 prompt) with rewrites and shadow divergence 0; **Run saved question** still works | "Breaking changes migrate their own consumers." |

Close on the Marketplace: the decision pack's certificate now covers the agent-built product, the healed
node and the human approvals — signed and verifiable with one click (`/certs/{id}/verify`).

## Pre-demo checklist

- [ ] `make reset && make test` green in mock mode
- [ ] Conductor in a second window, projector on `/market?user=priya`
- [ ] Run steps 0–12 once end to end the morning of the demo; reset
- [ ] Certificate verify endpoint ready (`/certs/{id}/verify`, public key at `/certs/public-key`)
