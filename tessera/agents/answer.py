"""Answer Agent (B1): question -> governed plan -> policy-rewritten SQL -> result + signed certificate."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import sqlglot
from sqlglot import exp

from tessera import clock, ids
from tessera.agents.certificate import CertificateService
from tessera.config import Settings
from tessera.governance.lineage import Lineage
from tessera.governance.policy import PolicyEngine, Principal, load_principal
from tessera.governance.semantic import CompileError, Plan, SemanticModel, compile_plan
from tessera.jsonutil import canonical, dumps, sha256
from tessera.llm.client import LLM
from tessera.orchestrator.bus import Bus
from tessera.warehouse.base import Warehouse

AGENT = "answer_agent@0.1.0"


def json_value(v: Any) -> Any:
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, Decimal):
        v = float(v)
    if isinstance(v, float):
        return None if math.isnan(v) else round(v, 4)
    return v


def serialise(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{k: json_value(v) for k, v in r.items()} for r in rows]


def result_hash(rows: list[dict[str, Any]]) -> str:
    return sha256(canonical(rows))


@dataclass
class Answer:
    question_id: str
    question: str
    asked_by: str
    outcome: str
    confidence: float
    plan: dict[str, Any]
    sql: str | None = None
    columns: list[str] = field(default_factory=list)
    rows: list[dict[str, Any]] = field(default_factory=list)
    certificate: dict[str, Any] | None = None
    message: str = ""
    unmatched: list[str] = field(default_factory=list)
    withheld: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id,
            "question": self.question,
            "asked_by": self.asked_by,
            "outcome": self.outcome,
            "confidence": self.confidence,
            "plan": self.plan,
            "sql": self.sql,
            "columns": self.columns,
            "rows": self.rows,
            "certificate": self.certificate,
            "message": self.message,
            "unmatched": self.unmatched,
            "withheld": self.withheld,
        }


class AnswerAgent:
    def __init__(
        self,
        wh: Warehouse,
        llm: LLM,
        semantic: SemanticModel,
        policy: PolicyEngine,
        lineage: Lineage,
        certs: CertificateService,
        bus: Bus,
        settings: Settings,
    ) -> None:
        self.wh = wh
        self.llm = llm
        self.semantic = semantic
        self.policy = policy
        self.lineage = lineage
        self.certs = certs
        self.bus = bus
        self.settings = settings
        self.sentinel_keys: dict[str, str] = {}

    # -- planning ------------------------------------------------------------------------------
    def plan(self, question: str) -> tuple[Plan, float, list[str]]:
        candidates, retrieval = self.semantic.retrieve(question)
        variables = {
            "question": question,
            "candidates": [c.brief() for c in candidates],
            "today": clock.naive_utc(clock.now()).date().isoformat(),
        }
        res = self.llm.complete_json("answer.plan", variables, Plan)
        plan = res.value
        allowed = {(c.product_fqn, c.name) for c in candidates}
        unknown = []
        if plan.product:
            refs = plan.metrics + plan.dimensions + [f.dimension for f in plan.filters]
            unknown = [r for r in refs if (plan.product, r) not in allowed]
        if unknown:
            plan.unmatched = list(dict.fromkeys(plan.unmatched + unknown))
        return plan, retrieval, unknown

    def confidence(self, plan: Plan, retrieval: float) -> float:
        requested = max(1, len(set(plan.requested_terms)))
        matched = (
            len(set(plan.requested_terms) & set(plan.matched_terms + plan.metrics))
            if plan.requested_terms
            else 0
        )
        coverage = min(1.0, matched / requested) if plan.requested_terms else (1.0 if plan.metrics else 0.0)
        return round(coverage * min(1.0, 0.7 + retrieval), 4)

    # -- main entry ----------------------------------------------------------------------------
    def ask(self, question: str, user_id: str, *, question_id: str | None = None, log: bool = True) -> Answer:
        principal = load_principal(self.wh, user_id)
        qid = question_id or ids.new_id(self.wh, "question")
        plan, retrieval, _ = self.plan(question)
        conf = self.confidence(plan, retrieval)
        plan_json = plan.model_dump(mode="json")
        if not plan.product or not plan.metrics or plan.unmatched:
            ans = Answer(
                qid,
                question,
                user_id,
                "missing_data",
                conf,
                plan_json,
                unmatched=plan.unmatched,
                message="I can't answer this yet. We've logged this — you'll be notified when it can "
                "be answered.",
            )
            if log:
                self._log(ans, existing=question_id is not None)
            return ans
        try:
            sql = compile_plan(self.semantic, plan)
        except CompileError as exc:
            ans = Answer(
                qid,
                question,
                user_id,
                "missing_data",
                conf,
                plan_json,
                unmatched=[str(exc)],
                message="I can't answer this yet. We've logged this.",
            )
            if log:
                self._log(ans, existing=question_id is not None)
            return ans
        ans = self.execute(qid, question, principal, plan, sql, conf)
        if log:
            self._log(ans, existing=question_id is not None)
        return ans

    def execute(
        self, qid: str, question: str, principal: Principal, plan: Plan, sql: str, conf: float
    ) -> Answer:
        assert plan.product
        rewritten, decisions = self.policy.rewrite(sql, principal, sentinel_keys=self.sentinel_keys)
        rows = serialise(self.wh.rows(rewritten))
        columns = plan.dimensions + plan.metrics
        used = self._columns_used(plan, decisions)
        nodes = self.lineage.upstream_columns(plan.product, used)
        metrics = []
        for m in plan.metrics:
            el = self.semantic.get(plan.product, m)
            assert el is not None
            metrics.append(
                {
                    "name": m,
                    "certified": el.certified,
                    "certified_by": el.certified_by,
                    "expression": el.expression,
                    "product": plan.product,
                }
            )
        rhash = result_hash(rows)
        cert = self.certs.issue(
            question_id=qid,
            asked_by=principal.user_id,
            metrics=metrics,
            products=[plan.product],
            policy_decisions=[d.as_dict() for d in decisions],
            lineage_nodes=nodes,
            result_hash=rhash,
        )
        outcome = "low_confidence" if conf < self.settings.confidence_floor else "answered"
        withheld = cert["verdict"] == "block"
        message = ""
        if withheld:
            reasons = [t["reason"] for t in cert["rule_trace"] if t["outcome"] == "block"]
            message = "Answer withheld by certificate rules: " + "; ".join(reasons)
        return Answer(
            qid,
            question,
            principal.user_id,
            outcome,
            conf,
            plan.model_dump(mode="json"),
            rewritten,
            columns,
            [] if withheld else rows,
            cert,
            message,
            withheld=withheld,
        )

    def _columns_used(self, plan: Plan, decisions: list[Any]) -> list[str]:
        assert plan.product
        cols: list[str] = []
        names = plan.dimensions + [f.dimension for f in plan.filters] + plan.metrics
        td = self.semantic.time_dimension(plan.product) if plan.time_window not in (None, "all") else None
        if td:
            names.append(td.name)
        for n in names:
            el = self.semantic.get(plan.product, n)
            if el is None:
                continue
            for c in sqlglot.parse_one(el.expression, dialect="duckdb").find_all(exp.Column):
                cols.append(c.name)
        for d in decisions:
            if d.applied and d.predicate:
                for c in sqlglot.parse_one(d.predicate, dialect="duckdb").find_all(exp.Column):
                    cols.append(c.name)
        return sorted(set(cols))

    def _log(self, ans: Answer, existing: bool) -> None:
        cert_id = ans.certificate["cert_id"] if ans.certificate else None
        if existing:
            return
        self.wh.execute(
            "INSERT INTO meta.questions VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?, NULL)",
            [
                ans.question_id,
                ans.asked_by,
                ans.question,
                clock.naive_utc(clock.now()),
                ans.outcome,
                dumps(ans.plan),
                ans.confidence,
                cert_id,
            ],
        )
        self.bus.emit("question.logged", {"question_id": ans.question_id, "outcome": ans.outcome})
