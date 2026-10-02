"""Shadow-mode trials and promotion of agent versions (idea 15)."""

from __future__ import annotations

from typing import Any

import numpy as np

from tessera import clock, ids
from tessera.agents.answer import AnswerAgent
from tessera.agents.recall import diff_results
from tessera.governance.provenance import Artifact, ProvenanceLedger
from tessera.jsonutil import dumps, loads
from tessera.market.agents import (
    AgentContract,
    agent_scope,
    answer_quietly,
    credits,
    evaluate_agent,
    latest_evaluation,
    replay_set,
)
from tessera.market.listings import Marketplace, agent_listing_id
from tessera.warehouse.base import Warehouse


class PromotionRejected(RuntimeError):
    pass


def trial_traffic(wh: Warehouse) -> list[dict[str, Any]]:
    """Questions the agent would have served: the replay set plus the live question log."""
    seen: set[tuple[str, str]] = set()
    out = []
    live = wh.rows(
        "SELECT q.question_id, q.asked_by, q.text FROM meta.questions q WHERE NOT EXISTS (SELECT 1 FROM "
        "meta.answer_snapshots s WHERE s.question_id = q.question_id AND s.kind = 'export') "
        "ORDER BY q.asked_at, q.question_id"
    )
    for r in [*replay_set(wh), *live]:
        k = (str(r["asked_by"]), str(r["text"]))
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out


def question_divergence(inc: dict[str, Any], cand: dict[str, Any]) -> float:
    if inc["hash"] == cand["hash"]:
        return 0.0
    if inc["outcome"] != "answered" or cand["outcome"] != "answered":
        return 1.0
    plan = inc["plan"]
    if inc["plan"].dimensions != cand["plan"].dimensions or inc["plan"].metrics != cand["plan"].metrics:
        return 1.0
    n_i, n_c = len(inc["rows"]), len(cand["rows"])
    ratio = abs(1 - n_c / n_i) if n_i else 1.0
    d = diff_results(inc["rows"], cand["rows"], plan.dimensions, plan.metrics)
    return float(max(ratio, d["max_pct"] / 100))


class Trials:
    def __init__(
        self, wh: Warehouse, answer: AnswerAgent, market: Marketplace, ledger: ProvenanceLedger
    ) -> None:
        self.wh = wh
        self.answer = answer
        self.market = market
        self.ledger = ledger

    def start(self, agent: str, candidate_version: int, days: int | None = None) -> dict[str, Any]:
        incumbent = self.market.agent_spec(agent)
        candidate = self.market.agent_spec(agent, candidate_version)
        days = days or candidate.evaluation.trial_days
        evaluate_agent(self.wh, self.answer, candidate)  # signed evidence for the candidate first
        trial_id = ids.new_id(self.wh, "trial")
        self.wh.execute(
            "INSERT INTO meta.agent_trials VALUES (?, ?, ?, ?, ?, 'running', ?, NULL, NULL)",
            [trial_id, agent, incumbent.version, candidate.version, days, clock.naive_utc(clock.now())],
        )
        inc_scope = agent_scope(self.wh, incumbent)
        cand_scope = agent_scope(self.wh, candidate)
        results = []
        for q in trial_traffic(self.wh):
            text, user = str(q["text"]), str(q["asked_by"])
            inc = answer_quietly(
                self.answer, text, user, inc_scope, incumbent.runtime.retrieval_top_k
            )  # served
            cand = answer_quietly(
                self.answer, text, user, cand_scope, candidate.runtime.retrieval_top_k
            )  # stored
            div = question_divergence(inc, cand)
            results.append({"q": q, "inc": inc, "cand": cand, "div": div})
            self.wh.execute(
                "INSERT INTO meta.agent_trial_results VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    trial_id,
                    q["question_id"],
                    text,
                    inc["hash"],
                    cand["hash"],
                    div,
                    credits(int(inc["tokens"])),
                    credits(int(cand["tokens"])),
                    inc["outcome"],
                    cand["outcome"],
                ],
            )
        summary = self.summarise(results, days, candidate)
        self.wh.execute(
            "UPDATE meta.agent_trials SET status = 'completed', summary_json = ? WHERE trial_id = ?",
            [dumps(summary), trial_id],
        )
        return {"trial_id": trial_id, **summary}

    @staticmethod
    def summarise(results: list[dict[str, Any]], days: int, candidate: AgentContract) -> dict[str, Any]:
        n = len(results)
        divergent = [r for r in results if r["div"] > 0]
        new_capability = [
            r for r in divergent if r["inc"]["outcome"] != "answered" and r["cand"]["outcome"] == "answered"
        ]
        both = [r for r in results if r["inc"]["outcome"] == "answered"]
        regressions = [r for r in both if r["div"] > 0]
        inc_cost = float(np.mean([credits(int(r["inc"]["tokens"])) for r in results])) if results else 0.0
        cand_cost = float(np.mean([credits(int(r["cand"]["tokens"])) for r in results])) if results else 0.0
        cost_change = (cand_cost - inc_cost) / inc_cost * 100 if inc_cost else 0.0
        gate_cfg = candidate.evaluation.promotion_gate
        reg_share = len(regressions) / len(both) if both else 0.0
        reasons = []
        if reg_share > gate_cfg.max_divergence:
            reasons.append(
                f"divergence on previously answered questions {reg_share:.2%} > {gate_cfg.max_divergence:.0%}"
            )
        if cost_change > gate_cfg.max_cost_increase_pct:
            reasons.append(f"cost +{cost_change:.1f}% > +{gate_cfg.max_cost_increase_pct:.0f}%")
        return {
            "days": days,
            "questions": n,
            "divergent": len(divergent),
            "divergent_pct": round(len(divergent) / n * 100, 2) if n else 0.0,
            "new_capability": len(new_capability),
            "divergent_other": len(regressions),
            "regression_share": round(reg_share, 6),
            "new_capability_examples": sorted({str(r["q"]["text"]) for r in new_capability})[:5],
            "incumbent_cost": round(inc_cost, 6),
            "candidate_cost": round(cand_cost, 6),
            "cost_change_pct": round(cost_change, 2),
            "gate": {
                "passed": not reasons,
                "reasons": reasons,
                "max_divergence": gate_cfg.max_divergence,
                "max_cost_increase_pct": gate_cfg.max_cost_increase_pct,
            },
        }

    def latest(self, agent: str) -> dict[str, Any] | None:
        r = self.wh.rows(
            "SELECT * FROM meta.agent_trials WHERE agent = ? ORDER BY started_at DESC LIMIT 1", [agent]
        )
        if not r:
            return None
        return {**r[0], "summary_json": loads(r[0]["summary_json"])}

    def promote(self, agent: str, approver: str) -> dict[str, Any]:
        trial = self.latest(agent)
        if trial is None or trial["status"] != "completed":
            raise PromotionRejected("no completed shadow trial for this agent")
        summary = trial["summary_json"]
        candidate = self.market.agent_spec(agent, int(trial["candidate_version"]))
        ev = latest_evaluation(self.wh, agent, candidate.version)
        reasons = list(summary["gate"]["reasons"])
        if ev is None or float(ev["accuracy"]) < candidate.evaluation.min_verified_accuracy:
            reasons.append("candidate below minimum verified accuracy")
        if reasons:
            raise PromotionRejected("; ".join(reasons))
        inc_id = agent_listing_id(agent, int(trial["incumbent_version"]))
        cand_id = agent_listing_id(agent, candidate.version)
        self.wh.execute("UPDATE meta.listings SET status = 'superseded' WHERE listing_id = ?", [inc_id])
        self.wh.execute(
            "UPDATE meta.listings SET status = 'published', published_at = ? WHERE listing_id = ?",
            [clock.naive_utc(clock.now()), cand_id],
        )
        self.wh.execute(
            "UPDATE meta.agent_trials SET status = 'promoted', decided_at = ? WHERE trial_id = ?",
            [clock.naive_utc(clock.now()), trial["trial_id"]],
        )
        spec = str(self.market.get(cand_id)["spec_yaml"])
        artifact = self.ledger.record(
            Artifact(
                "agent_contract",
                f"agent.{agent}",
                spec,
                agent=f"human:{approver}",
                approved_by=approver,
                input_refs={
                    "trial_id": trial["trial_id"],
                    "evaluation_id": ev["evaluation_id"] if ev else None,
                },
                gate_evidence=summary["gate"],
            )
        )
        return {"promoted": cand_id, "superseded": inc_id, "artifact_id": artifact, "trial": summary}
