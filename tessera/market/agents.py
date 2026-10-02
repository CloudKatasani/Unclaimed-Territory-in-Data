"""Agent product contracts, the seeded benchmark replay set and evidence-based evaluation (idea 13)."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import numpy as np
import yaml
from pydantic import BaseModel, ConfigDict, Field

from tessera import clock, ids
from tessera.agents.answer import AnswerAgent, result_hash, serialise
from tessera.governance.policy import load_principal
from tessera.governance.semantic import CompileError, Plan, compile_plan
from tessera.governance.signing import Signer
from tessera.jsonutil import canonical, dumps, loads
from tessera.warehouse.base import Warehouse

SEED_DIR = Path(__file__).resolve().parents[1] / "seed" / "market"
CREDITS_PER_1K_TOKENS = 0.01


class Requires(BaseModel):
    model_config = ConfigDict(extra="allow")
    data_products: list[str] = Field(default_factory=list)
    policies: list[str] = Field(default_factory=list)
    purpose: str = "operational_reporting"


class Budget(BaseModel):
    max_credits_per_run: float = 0.05
    max_runs_per_day: int = 500


class PromotionGate(BaseModel):
    max_divergence: float = 0.01
    max_cost_increase_pct: float = 10


class Evaluation(BaseModel):
    model_config = ConfigDict(extra="allow")
    replay_set: str = "ground_truth"
    min_verified_accuracy: float = 0.95
    trial_days: int = 3
    promotion_gate: PromotionGate = Field(default_factory=PromotionGate)


class AgentContract(BaseModel):
    model_config = ConfigDict(extra="allow")
    agent: str
    version: int
    title: str
    purpose: str
    owner: str
    inputs: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    requires: Requires = Field(default_factory=Requires)
    budget: Budget = Field(default_factory=Budget)
    evaluation: Evaluation = Field(default_factory=Evaluation)


def load_agent_spec(text: str) -> AgentContract:
    return AgentContract.model_validate(yaml.safe_load(text))


def seed_agent_specs() -> list[str]:
    return [p.read_text() for p in sorted(SEED_DIR.glob("agent_*.yaml"))]


def credits(tokens: int) -> float:
    return round(tokens / 1000 * CREDITS_PER_1K_TOKENS, 6)


# -- benchmark replay set ------------------------------------------------------------------------------
METRICS = {"SAIDI": "saidi", "SAIFI": "saifi", "CAIDI": "caidi"}
DIMS = {"substation": "substation_id", "feeder": "feeder_id"}


def benchmark_cases(wh: Warehouse) -> list[dict[str, Any]]:
    """Deterministic benchmark questions with gold plans (seeded ground truth)."""
    feeders = wh.rows("SELECT feeder_id, substation_id, region FROM raw.feeders ORDER BY feeder_id")
    users = [("alice", "NORTH"), ("maria", "NORTH"), ("deshawn", "NORTH"), ("priya", None)]
    out: list[dict[str, Any]] = []
    rel = "dp.outage_reliability"
    for user, region in users:
        mine = [f for f in feeders if region is None or f["region"] == region]
        subs = sorted({f["substation_id"] for f in mine})
        scope = "in my region " if region else ""
        for m, metric in METRICS.items():
            for d, dim in DIMS.items():
                out.append(
                    {
                        "user": user,
                        "text": f"What was {m} by {d} {scope}last month?",
                        "plan": Plan(
                            product=rel, metrics=[metric], dimensions=[dim], time_window="last_month"
                        ),
                    }
                )
                out.append(
                    {
                        "user": user,
                        "text": f"{m} by {d} for the last 3 months",
                        "plan": Plan(
                            product=rel, metrics=[metric], dimensions=[dim], time_window="last_3_months"
                        ),
                    }
                )
            for s in subs:
                out.append(
                    {
                        "user": user,
                        "text": f"What was {m} for substation {s} last month?",
                        "plan": Plan(
                            product=rel,
                            metrics=[metric],
                            dimensions=[],
                            time_window="last_month",
                            filters=[{"dimension": "substation_id", "value": s}],
                        ),
                    }
                )
        for d, dim in [*DIMS.items(), ("region", "region")]:
            out.append(
                {
                    "user": user,
                    "text": f"How many outages by {d} last month?",
                    "plan": Plan(
                        product=rel, metrics=["outage_count"], dimensions=[dim], time_window="last_month"
                    ),
                }
            )
        for f in mine:
            out.append(
                {
                    "user": user,
                    "text": f"Daily kWh for feeder {f['feeder_id']} last week",
                    "plan": Plan(
                        product="dp.meter_consumption_daily",
                        metrics=["daily_kwh"],
                        dimensions=["read_date"],
                        time_window="last_week",
                        filters=[{"dimension": "feeder_id", "value": f["feeder_id"]}],
                    ),
                }
            )
    return out


def execute_plan(agent: AnswerAgent, plan: Plan, user: str) -> tuple[str, list[dict[str, Any]]]:
    sql = compile_plan(agent.semantic, plan)
    rewritten, _ = agent.policy.rewrite(
        sql, load_principal(agent.wh, user), sentinel_keys=agent.sentinel_keys
    )
    rows = serialise(agent.wh.rows(rewritten))
    return result_hash(rows), rows


def seed_benchmark(wh: Warehouse, agent: AnswerAgent) -> int:
    n = 0
    for case in benchmark_cases(wh):
        h, _ = execute_plan(agent, case["plan"], case["user"])
        wh.execute(
            "INSERT INTO meta.benchmark_questions VALUES (?, ?, ?, ?, ?, ?)",
            [
                ids.new_id(wh, "benchmark"),
                case["user"],
                case["text"],
                dumps(case["plan"].model_dump(mode="json")),
                h,
                dumps([case["plan"].product]),
            ],
        )
        n += 1
    return n


def replay_set(wh: Warehouse) -> list[dict[str, Any]]:
    """Seeded benchmark plus questions a human marked correct (ground_truth_hash set)."""
    rows = wh.rows(
        "SELECT question_id, asked_by, text, ground_truth_hash FROM meta.benchmark_questions "
        "ORDER BY question_id"
    )
    rows += wh.rows(
        "SELECT question_id, asked_by, text, ground_truth_hash FROM meta.questions "
        "WHERE outcome = 'answered' AND ground_truth_hash IS NOT NULL ORDER BY question_id"
    )
    return rows


def answer_quietly(agent: AnswerAgent, text: str, user: str, scope: list[str]) -> dict[str, Any]:
    """Answer without logging, certificates or snapshots (evaluation and shadow trials)."""
    llm = agent.llm
    saved = llm.log_calls
    llm.log_calls = False
    t0 = time.perf_counter()
    try:
        plan, _, _ = agent.plan(text, scope)
        tokens = agent._last_tokens
        if not plan.product or not plan.metrics or plan.unmatched:
            return {
                "outcome": "missing_data",
                "hash": None,
                "rows": [],
                "tokens": tokens,
                "plan": plan,
                "latency": time.perf_counter() - t0,
            }
        try:
            h, rows = execute_plan(agent, plan, user)
        except CompileError:
            return {
                "outcome": "missing_data",
                "hash": None,
                "rows": [],
                "tokens": tokens,
                "plan": plan,
                "latency": time.perf_counter() - t0,
            }
        return {
            "outcome": "answered",
            "hash": h,
            "rows": rows,
            "tokens": tokens,
            "plan": plan,
            "latency": time.perf_counter() - t0,
        }
    finally:
        llm.log_calls = saved


def agent_scope(wh: Warehouse, spec: AgentContract) -> list[str]:
    existing = {str(r["fqn"]) for r in wh.rows("SELECT fqn FROM meta.products")}
    touched = set(spec.requires.data_products)
    # one level up: the products the agent's own replayed queries touched (recorded at evaluation time)
    ev = latest_evaluation(wh, spec.agent, spec.version)
    if ev:
        touched |= set((loads(ev["details_json"]) or {}).get("touched_products", []))
    return sorted(touched & existing)


def evaluate_agent(wh: Warehouse, agent: AnswerAgent, spec: AgentContract) -> dict[str, Any]:
    """Replay the ground-truth set in mock mode, compare result hashes, sign the evaluation."""
    cases = replay_set(wh)
    # Evaluate over every published product; record which ones the agent's correct answers touched.
    scope = sorted(str(r["fqn"]) for r in wh.rows("SELECT fqn FROM meta.products"))
    correct = 0
    costs: list[float] = []
    lat: list[float] = []
    touched: set[str] = set()
    failures: list[dict[str, Any]] = []
    for c in cases:
        r = answer_quietly(agent, str(c["text"]), str(c["asked_by"]), scope)
        costs.append(credits(int(r["tokens"])))
        lat.append(float(r["latency"]))
        if r["hash"] == c["ground_truth_hash"]:
            correct += 1
            if r["plan"].product:
                touched.add(str(r["plan"].product))
        elif len(failures) < 20:
            failures.append({"question_id": c["question_id"], "text": c["text"], "outcome": r["outcome"]})
    n = len(cases)
    row = {
        "evaluation_id": ids.new_id(wh, "evaluation"),
        "agent": spec.agent,
        "version": spec.version,
        "n": n,
        "accuracy": round(correct / n, 6) if n else 0.0,
        "avg_cost": round(float(np.mean(costs)) if costs else 0, 6),
        "p95_latency": round(float(np.percentile(lat, 95)) if lat else 0, 4),
        "evaluated_at": clock.naive_utc(clock.now()),
        "details_json": dumps(
            {"correct": correct, "touched_products": sorted(touched), "failures": failures, "scope": scope}
        ),
    }
    payload = canonical({k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in row.items()})
    row["signature"] = Signer(wh).sign(payload)
    wh.execute("INSERT INTO meta.agent_evaluations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", list(row.values()))
    return row


def latest_evaluation(wh: Warehouse, agent: str, version: int) -> dict[str, Any] | None:
    rows = wh.rows(
        "SELECT * FROM meta.agent_evaluations WHERE agent = ? AND version = ? "
        "ORDER BY evaluated_at DESC LIMIT 1",
        [agent, version],
    )
    return rows[0] if rows else None


def evidence_sentence(ev: dict[str, Any]) -> str:
    when = ev["evaluated_at"]
    return (
        f"Verified accuracy {ev['accuracy'] * 100:.1f}% on {int(ev['n']):,} replayed questions "
        f"(evaluated {when.day} {when.strftime('%b %Y')})"
    )
