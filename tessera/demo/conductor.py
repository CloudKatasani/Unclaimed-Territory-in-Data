"""Demo conductor (docs/08): the 12-step client demo as code — run, replay, reset.

Every step performs its backend actions and returns where the projector window should navigate.
Steps are idempotent (a step already run just returns its navigation), so the conductor can replay
"steps 0..N" after a reset and land in exactly the same state every time.
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tessera import clock
from tessera.config import get_settings
from tessera.demo.storyline import seed_questions
from tessera.jsonutil import dumps, loads
from tessera.llm.client import FIXTURES
from tessera.platform import Tessera
from tessera.seed import domain as D
from tessera.seed.generate import ensure_template

COPILOT_V1 = "agent:outage_copilot@1"
COPILOT_V2 = "agent:outage_copilot@2"
MUTUAL_AID = "Should I request mutual aid for substation S-04 tonight?"


@dataclass(frozen=True)
class Step:
    n: int
    screen: str
    title: str
    user: str
    idea: str
    talk: str
    action: Callable[[Tessera], dict[str, Any]]


def _q(n: int) -> dict[str, str]:
    return seed_questions()[n]


def s0(app: Tessera) -> dict[str, Any]:
    return {"navigate": "/market?user=priya"}


def s1(app: Tessera) -> dict[str, Any]:
    return {"navigate": f"/agents?user=priya&open={COPILOT_V1}"}


def s2(app: Tessera) -> dict[str, Any]:
    b = app.market.subscribe(COPILOT_V1, "alice")
    return {"bundle_id": b["bundle_id"], "leases": len(b["leases"]), "navigate": "/access?user=alice"}


def s3(app: Tessera) -> dict[str, Any]:
    q = _q(0)
    a = app.answer.ask(q["text"], q["asked_by"])
    return {"question_id": a.question_id, "navigate": f"/?user=alice&question_id={a.question_id}"}


def s4(app: Tessera) -> dict[str, Any]:
    ids = [
        app.answer.ask(q["text"], q["asked_by"]).question_id
        for q in seed_questions()
        if q["expected_outcome"] == "missing_data"
    ]
    return {"question_ids": ids, "navigate": f"/?user=alice&question_id={ids[0]}"}


def s5(app: Tessera) -> dict[str, Any]:
    app.demand.mine()
    rows = app.wh.rows("SELECT intent_id, contract_id FROM meta.demand_intents ORDER BY created_at LIMIT 1")
    cid = rows[0]["contract_id"] or app.demand.draft_contract(str(rows[0]["intent_id"]))
    return {"intent_id": rows[0]["intent_id"], "contract_id": cid, "navigate": "/demand?user=raj"}


def s6(app: Tessera) -> dict[str, Any]:
    cid = str(
        app.wh.rows("SELECT contract_id FROM meta.demand_intents ORDER BY created_at LIMIT 1")[0][
            "contract_id"
        ]
    )
    app.approvals.approve_contract(cid, "raj")
    app.bus.drain()
    job = app.wh.rows("SELECT job_id FROM meta.build_jobs WHERE contract_id = ?", [cid])[0]["job_id"]
    return {"job_id": job, "navigate": f"/builds?user=raj&job={job}"}


def s7(app: Tessera) -> dict[str, Any]:
    trial = app.trials.start("outage_copilot", 2)
    promoted = app.trials.promote("outage_copilot", "raj")
    return {
        "trial_id": trial["trial_id"],
        "promoted": promoted["promoted"],
        "navigate": f"/agents?user=raj&open={COPILOT_V2}",
    }


def s8(app: Tessera) -> dict[str, Any]:
    res = app.sentinels.verify_all()
    return {"passed": all(r["passed"] for r in res.values()), "navigate": "/truth?user=raj"}


def s9(app: Tessera) -> dict[str, Any]:
    out: dict[str, Any] = dict(app.simulate_drift())
    return {
        "events": out["events"],
        "navigate": f"/drift?user=raj&event={out['events'][0]}" if out["events"] else "/drift?user=raj",
    }


def s10(app: Tessera) -> dict[str, Any]:
    pending = app.wh.rows("SELECT job_id FROM meta.patches WHERE status = 'awaiting_approval'")
    for p in pending:  # the presenter may already have clicked Approve on the Drift screen
        app.approve_patch(str(p["job_id"]), "raj")
    return {"notices": len(app.recall.inbox("alice")), "navigate": "/inbox?user=alice"}


def s11(app: Tessera) -> dict[str, Any]:
    a = app.answer.ask(MUTUAL_AID, "priya")
    return {"question_id": a.question_id, "navigate": f"/?user=priya&question_id={a.question_id}"}


def s12(app: Tessera) -> dict[str, Any]:
    out = app.publish_version(D.RELIABILITY_V2_CONTRACT, D.RELIABILITY_V2_SQL, "raj")
    accepted = app.migration.accept_all("dp.outage_reliability", "raj")
    saved = app.migration.run_saved("saved:alice:saidi_by_substation", "alice", app.answer)
    return {
        "migrations": out["migrations"],
        "accepted": accepted,
        "saved_question_outcome": saved.outcome,
        "navigate": "/builds?user=raj&tab=migrations",
    }


STEPS: list[Step] = [
    Step(
        0,
        "Marketplace",
        "Open the marketplace as Priya",
        "priya",
        "16, 13",
        "Your people shop for decisions, not tables.",
        s0,
    ),
    Step(
        1,
        "Agent store",
        "Open outage_copilot",
        "priya",
        "13",
        "Agents earn their place with evidence, not ratings.",
        s1,
    ),
    Step(
        2,
        "Agent store",
        "Subscribe Alice to outage_copilot",
        "alice",
        "14",
        "Subscribing gives the agent exactly the access it needs, and nothing it doesn't.",
        s2,
    ),
    Step(
        3,
        "Ask",
        "Alice asks for SAIDI by substation",
        "alice",
        "B1, 19",
        "Every number carries proof. Every sentence has a receipt.",
        s3,
    ),
    Step(
        4,
        "Ask",
        "Alice, Maria and Deshawn ask about zero usage during outages",
        "alice",
        "B2",
        "When the platform can't answer, it remembers the question.",
        s4,
    ),
    Step(
        5,
        "Demand board",
        "Raj opens the demand board; contract drafted",
        "raj",
        "B2, ID-1",
        "Demand becomes a contract. A human approves, agents build.",
        s5,
    ),
    Step(
        6,
        "Builds",
        "Raj approves; the build runs and publishes",
        "raj",
        "A1, A6",
        "The build is tested against its own contract and signed.",
        s6,
    ),
    Step(
        7,
        "Agent store",
        "Shadow trial of outage_copilot v2, then promote",
        "raj",
        "15",
        "A new agent version proves itself in the shadows before it goes live.",
        s7,
    ),
    Step(
        8,
        "Pipeline truth",
        "Sentinel records across products",
        "raj",
        "3",
        "Tracer rows prove the pipelines are still telling the truth, every run.",
        s8,
    ),
    Step(
        9,
        "Drift",
        "The vendor changes the feed overnight",
        "raj",
        "A2, 3, 21",
        "A vendor changed a field overnight. Watch the platform heal itself, and refuse the wrong fix.",
        s9,
    ),
    Step(
        10,
        "Inbox",
        "Alice's inbox after Raj approves the patch",
        "alice",
        "1, 19",
        "The numbers Alice exported last week were wrong. She's the first to know, not the last.",
        s10,
    ),
    Step(
        11,
        "Ask",
        "Priya asks whether to request mutual aid for S-04",
        "priya",
        "18",
        "Ask about the decision and you get the number and the margin.",
        s11,
    ),
    Step(
        12,
        "Builds",
        "Raj publishes dp.outage_reliability v2 (saidi -> saidi_minutes)",
        "raj",
        "17",
        "Breaking changes migrate their own consumers.",
        s12,
    ),
]


def steps_view(app: Tessera) -> list[dict[str, Any]]:
    done = {int(r["step"]): r for r in app.wh.rows("SELECT * FROM meta.demo_progress")}
    return [
        {
            "n": s.n,
            "screen": s.screen,
            "title": s.title,
            "user": s.user,
            "idea": s.idea,
            "talk": s.talk,
            "done": s.n in done,
            "result": loads(done[s.n]["result_json"]) if s.n in done else None,
        }
        for s in STEPS
    ]


def run_step(app: Tessera, n: int) -> dict[str, Any]:
    prev = app.wh.rows("SELECT result_json FROM meta.demo_progress WHERE step = ?", [n])
    if prev:
        return dict(loads(prev[0]["result_json"]))
    for s in STEPS[:n]:  # earlier steps first, so any step can be run directly
        if not app.wh.scalar("SELECT count(*) FROM meta.demo_progress WHERE step = ?", [s.n]):
            run_step(app, s.n)
    result = STEPS[n].action(app)
    app.bus.drain()
    result = {"step": n, **result}
    app.wh.execute(
        "INSERT INTO meta.demo_progress VALUES (?, ?, ?)", [n, clock.naive_utc(clock.now()), dumps(result)]
    )
    return result


def run_through(app: Tessera, last: int) -> list[dict[str, Any]]:
    return [run_step(app, n) for n in range(0, last + 1)]


def fingerprint(app: Tessera) -> dict[str, Any]:
    """What must be identical across runs from reset: certificates, hashes, notices, signatures."""
    return {
        "certificates": app.wh.rows(
            "SELECT cert_id, question_id, verdict, result_hash, signature FROM "
            "meta.certificates ORDER BY cert_id"
        ),
        "notices": app.wh.rows(
            "SELECT notice_id, consumer, message, old_hash, new_hash FROM meta.recall_notices "
            "ORDER BY notice_id"
        ),
        "provenance": app.wh.rows(
            "SELECT artifact_id, artifact_kind, target_fqn, content_sha256, signature FROM "
            "meta.provenance ORDER BY artifact_id"
        ),
        "migrations": app.wh.rows(
            "SELECT migration_id, consumer_id, divergence, status FROM meta.migrations ORDER BY migration_id"
        ),
    }


def record_fixtures() -> dict[str, Any]:
    """Replay the full client demo on a scratch copy in LLM_MODE=record and write fixture files."""
    settings = get_settings()
    tpl = ensure_template(settings)
    existing = {str(p.relative_to(FIXTURES)) for p in FIXTURES.rglob("*.json")}
    from tessera.demo import storyline

    calls: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory() as tmp:
        # both demo paths: the 7-step storyline (docs/00) and the 12-step client demo (docs/08)
        runners: tuple[tuple[str, Callable[[Tessera], list[dict[str, Any]]]], ...] = (
            ("storyline", lambda a: [storyline.run_all(a)]),
            ("conductor", lambda a: run_through(a, len(STEPS) - 1)),
        )
        for name, run in runners:
            path = Path(tmp) / f"{name}.duckdb"
            shutil.copyfile(tpl, path)
            clock.reset()
            rec = Tessera(path)
            rec.llm.mode = "record"
            try:
                results += run(rec)
                calls += rec.wh.rows(
                    "SELECT prompt_id, variables_hash FROM meta.llm_calls ORDER BY created_at"
                )
            finally:
                rec.close()
    files = sorted({f"{c['prompt_id']}/{c['variables_hash']}.json" for c in calls})
    stale = sorted(existing - set(files))
    for rel in stale:  # fixtures the demo no longer reaches
        (FIXTURES / rel).unlink()
    return {
        "steps": len(results),
        "fixtures": files,
        "new": sorted(set(files) - existing),
        "removed": stale,
        "total_files": len(files),
    }
