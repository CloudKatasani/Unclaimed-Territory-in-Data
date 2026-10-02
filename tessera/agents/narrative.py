"""Verified narratives (idea 19): every sentence is bound to a query the agent actually ran."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field

from tessera import clock, ids
from tessera.agents.answer import result_hash
from tessera.governance.semantic import PREVIOUS_WINDOW, Plan
from tessera.jsonutil import dumps
from tessera.llm.client import LLM
from tessera.warehouse.base import Warehouse

MAX_SENTENCES = 3


class NarrativeSentence(BaseModel):
    sentence: str
    query_ref: str | None = None


class Narrative(BaseModel):
    sentences: list[NarrativeSentence] = Field(default_factory=list)


def drilldowns(plan: Plan) -> dict[str, Plan]:
    """The sub-queries a narrative may cite: the main plan plus declared drill-downs."""
    out = {"main": plan}
    if plan.dimensions:
        out["total"] = plan.model_copy(update={"dimensions": []})
    if plan.time_window in PREVIOUS_WINDOW:
        out["previous_period"] = plan.model_copy(update={"time_window": PREVIOUS_WINDOW[plan.time_window]})
    return out


class NarrativeWriter:
    def __init__(self, wh: Warehouse, llm: LLM) -> None:
        self.wh = wh
        self.llm = llm

    def write(
        self,
        question: str,
        plan: Plan,
        main_rows: list[dict[str, Any]],
        run: Callable[[Plan], list[dict[str, Any]]],
    ) -> tuple[list[dict[str, Any]], dict[str, tuple[Plan, str]]]:
        subs = drilldowns(plan)
        results: dict[str, list[dict[str, Any]]] = {"main": main_rows}
        for ref, sub in subs.items():
            if ref != "main":
                results[ref] = run(sub)
        variables = {
            "question": question,
            "plan": plan.model_dump(mode="json"),
            "results": {k: v[:20] for k, v in results.items()},
        }
        res = self.llm.complete_json("narrative.write", variables, Narrative)
        kept: list[dict[str, Any]] = []
        for s in res.value.sentences[: MAX_SENTENCES + 2]:
            if not s.query_ref or s.query_ref not in results:
                self.wh.execute(
                    "INSERT INTO meta.rejections VALUES (?, ?, ?, ?, ?)",
                    [
                        ids.new_id(self.wh, "rejection"),
                        "narrative.write",
                        f"unbound sentence dropped (query_ref={s.query_ref!r})",
                        s.sentence,
                        clock.naive_utc(clock.now()),
                    ],
                )
                continue
            kept.append({"sentence": s.sentence, "query_ref": s.query_ref})
        bound = {ref: (subs[ref], result_hash(results[ref])) for ref in results}
        return kept[:MAX_SENTENCES], bound

    def bind(
        self, cert_id: str, consumer: str, sentences: list[dict[str, Any]], bound: dict[str, tuple[Plan, str]]
    ) -> list[dict[str, Any]]:
        now = clock.naive_utc(clock.now())
        out = []
        for i, s in enumerate(sentences, start=1):
            sub, h = bound[s["query_ref"]]
            self.wh.execute(
                "INSERT INTO meta.narrative_bindings VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'current', NULL, NULL)",
                [
                    cert_id,
                    i,
                    s["sentence"],
                    s["query_ref"],
                    dumps(sub.model_dump(mode="json")),
                    h,
                    consumer,
                    now,
                ],
            )
            out.append({**s, "sentence_no": i, "status": "current", "result_hash": h})
        return out

    def for_cert(self, cert_id: str) -> list[dict[str, Any]]:
        return self.wh.rows(
            "SELECT * FROM meta.narrative_bindings WHERE cert_id = ? ORDER BY sentence_no", [cert_id]
        )
