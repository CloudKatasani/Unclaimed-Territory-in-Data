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
    tokens: int = 0
    narrative: list[dict[str, Any]] = field(default_factory=list)
    decision: dict[str, Any] | None = None

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
        self.sentinels: Any = None
        self.narrator: Any = None  # NarrativeWriter (M10)
        self.decisions: Any = None  # DecisionEvaluator (M10)
        self.pack_verdict: Any = None  # callable(pack_spec) -> aggregated certificate verdict
        self._last_tokens = 0

    # -- planning ------------------------------------------------------------------------------
    def plan(
        self, question: str, scope: list[str] | None = None, k: int = 15
    ) -> tuple[Plan, float, list[str]]:
        candidates, retrieval = self.semantic.retrieve(question, k=k, products=scope)
        variables = {
            "question": question,
            "candidates": [c.brief() for c in candidates],
            "today": clock.naive_utc(clock.now()).date().isoformat(),
        }
        res = self.llm.complete_json("answer.plan", variables, Plan)
        self._last_tokens = res.input_tokens + res.output_tokens
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
    def ask(
        self,
        question: str,
        user_id: str,
        *,
        question_id: str | None = None,
        log: bool = True,
        scope: list[str] | None = None,
        snapshot: bool = True,
    ) -> Answer:
        principal = load_principal(self.wh, user_id)
        qid = question_id or ids.new_id(self.wh, "question")
        self._last_tokens = 0
        if self.decisions is not None:
            match, pack = self.decisions.match(question)
            if pack is not None:
                return self.decide(qid, question, principal, match.scope, pack, log=log)
        plan, retrieval, _ = self.plan(question, scope)
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
        ans.tokens = self._last_tokens
        if self.narrator is not None and ans.certificate and not ans.withheld:
            sentences, bound = self.narrator.write(
                question, plan, ans.rows, lambda p: self.run_sub(p, principal)
            )
            ans.narrative = self.narrator.bind(ans.certificate["cert_id"], user_id, sentences, bound)
        if log:
            self._log(ans, existing=question_id is not None)
        if snapshot:
            self.snapshot(ans, "answer")
        return ans

    def run_sub(self, plan: Plan, principal: Principal) -> list[dict[str, Any]]:
        """Execute a drill-down sub-query under the same policies (no certificate of its own)."""
        try:
            sql = compile_plan(self.semantic, plan)
        except CompileError:
            return []
        rewritten, _ = self.policy.rewrite(sql, principal, sentinel_keys=self.sentinel_keys)
        return serialise(self.wh.rows(rewritten))

    def decide(
        self,
        qid: str,
        question: str,
        principal: Principal,
        scope: dict[str, str],
        pack: dict[str, Any],
        *,
        log: bool = True,
    ) -> Answer:
        """Decision-first answer: thresholds, margins and sensitivity, under one certificate."""
        decisions: list[Any] = []

        def rewrite(sql: str) -> str:
            out, dec = self.policy.rewrite(sql, principal, sentinel_keys=self.sentinel_keys)
            for d in dec:
                if d.as_dict() not in [x.as_dict() for x in decisions]:
                    decisions.append(d)
            return str(out)

        thresholds = self.decisions.evaluate(pack, scope, principal, rewrite=rewrite)
        rows = [
            {
                "threshold": t["meaning"],
                "scope": f"{t['scope']} {t['scope_value'] or 'all'}",
                "current": round(t["current"], 2),
                "limit": f"{t['comparator']} {t['threshold']:g}",
                "margin": round(t["margin"], 2),
                "status": "breached" if t["breached"] else "within",
            }
            for t in thresholds
        ]
        metrics: list[dict[str, Any]] = []
        products: list[str] = []
        cols: set[str] = set()
        for t in thresholds:
            el = self.semantic.get(t["product"], t["measure"])
            if el is None:
                continue
            if t["product"] not in products:
                products.append(t["product"])
            if el.name not in [m["name"] for m in metrics]:
                metrics.append(
                    {
                        "name": el.name,
                        "certified": el.certified,
                        "certified_by": el.certified_by,
                        "expression": el.expression,
                        "product": t["product"],
                    }
                )
            cols |= {c.name for c in sqlglot.parse_one(el.expression, dialect="duckdb").find_all(exp.Column)}
            cols |= {"region", "substation_id", "month"}
        nodes = sorted({n for p in products for n in self.lineage.upstream_columns(p, sorted(cols))})
        cert = self.certs.issue(
            question_id=qid,
            asked_by=principal.user_id,
            metrics=metrics,
            products=products,
            policy_decisions=[d.as_dict() for d in decisions],
            lineage_nodes=nodes,
            result_hash=result_hash(rows),
            sentinel_status=self._sentinel_status(nodes),
        )
        sens = [t for t in thresholds if t["sensitivity"]]
        narrative = [
            {
                "sentence": t["sensitivity"],
                "query_ref": f"threshold:{t['metric']}",
                "sentence_no": i,
                "status": "current",
            }
            for i, t in enumerate(sens, start=1)
        ]
        for n in narrative:
            self.wh.execute(
                "INSERT INTO meta.narrative_bindings VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'current', NULL, NULL)",
                [
                    cert["cert_id"],
                    n["sentence_no"],
                    n["sentence"],
                    n["query_ref"],
                    dumps({"decision": pack["decision"], "scope": scope}),
                    cert["result_hash"],
                    principal.user_id,
                    clock.naive_utc(clock.now()),
                ],
            )
        verdict = self.pack_verdict(pack) if self.pack_verdict else None
        withheld = cert["verdict"] == "block"
        ans = Answer(
            qid,
            question,
            principal.user_id,
            "answered",
            1.0,
            {
                "decision": pack["decision"],
                "scope": scope,
                "product": products[0] if products else None,
                "metrics": [m["name"] for m in metrics],
                "dimensions": [],
            },
            "\n".join(t["sql"] for t in thresholds),
            list(rows[0].keys()) if rows else [],
            [] if withheld else rows,
            cert,
            "",
            withheld=withheld,
            narrative=[] if withheld else narrative,
            decision={
                "decision": pack["decision"],
                "title": pack.get("title"),
                "scope": scope,
                "thresholds": thresholds,
                "pack_verdict": verdict,
            },
        )
        if log:
            self._log(ans, existing=False)
        return ans

    def run_plan(
        self, plan: Plan, user_id: str, question: str, *, kind: str = "export", log: bool = True
    ) -> Answer:
        """Execute a saved plan (exports, saved questions) without the LLM; certified like any answer."""
        principal = load_principal(self.wh, user_id)
        qid = ids.new_id(self.wh, "question")
        sql = compile_plan(self.semantic, plan)
        ans = self.execute(qid, question, principal, plan, sql, 1.0)
        if log:
            self._log(ans, existing=False)
        self.snapshot(ans, kind)
        return ans

    def snapshot(self, ans: Answer, kind: str) -> None:
        """Answer log for recall: what was served, to whom, from which plan (docs/07 capability 1)."""
        if ans.certificate is None or ans.withheld:
            return
        self.wh.execute(
            "INSERT INTO meta.answer_snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ans.certificate["cert_id"],
                ans.question_id,
                ans.asked_by,
                ans.question,
                kind,
                dumps(ans.plan),
                dumps(ans.rows),
                ans.certificate["result_hash"],
                clock.naive_utc(clock.now()),
            ],
        )

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
            sentinel_status=self._sentinel_status(nodes),
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

    def _sentinel_status(self, nodes: list[str]) -> dict[str, Any]:
        if self.sentinels is None:
            return {}
        tables = {n.rsplit(".", 1)[0] for n in nodes}
        return {k: v for k, v in self.sentinels.status().items() if k in tables}

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
