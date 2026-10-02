"""Decision-first self-serve with sensitivity (idea 18).

A question that matches a decision pack is answered with the pack's registered thresholds evaluated at
the stated scope, the current margin, and the smallest counterfactual change in the driving measure that
would flip each threshold, phrased with the threshold's sensitivity template.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from tessera.governance.policy import SENTINEL_PREFIX, Principal
from tessera.governance.semantic import SemanticModel, resolve_window
from tessera.llm.client import LLM
from tessera.warehouse.base import Warehouse

CONFIG = Path(__file__).resolve().parents[1] / "seed" / "decision_metrics.yaml"
NUMBER_WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"]
OPS: dict[str, Callable[[float, float], bool]] = {
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
}


class DecisionMatch(BaseModel):
    decision: str | None = None
    scope: dict[str, str] = Field(default_factory=dict)
    confidence: float = 0.0


@lru_cache(maxsize=1)
def config() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(CONFIG.read_text())
    return data


def number_words(n: int) -> str:
    return NUMBER_WORDS[n] if 0 <= n < len(NUMBER_WORDS) else f"{n:,}"


def _lit(v: str) -> str:
    return "'" + v.replace("'", "''") + "'"


class DecisionEvaluator:
    def __init__(self, wh: Warehouse, llm: LLM, semantic: SemanticModel) -> None:
        self.wh = wh
        self.llm = llm
        self.semantic = semantic
        self._rewrite: Callable[[str], str] = lambda sql: sql

    # -- matching -------------------------------------------------------------------------------------
    def packs(self) -> list[dict[str, Any]]:
        rows = self.wh.rows(
            "SELECT spec_yaml FROM meta.listings WHERE kind = 'decision_pack' AND status = 'published' "
            "ORDER BY listing_id"
        )
        return [yaml.safe_load(str(r["spec_yaml"])) for r in rows]

    def candidates(self, question: str) -> list[dict[str, Any]]:
        """Cheap pre-filter: a decision cue plus textual similarity to a pack's example questions."""
        q = question.lower()
        if not any(cue in q for cue in config()["decision_cues"]):
            return []
        out = []
        for p in self.packs():
            docs = [e.lower() for e in p.get("question_examples", [])]
            if not docs:
                continue
            vec = TfidfVectorizer(ngram_range=(1, 2)).fit(docs + [q])
            sim = float(cosine_similarity(vec.transform([q]), vec.transform(docs)).max())
            if sim >= 0.15:
                out.append(
                    {
                        "decision": p["decision"],
                        "title": p.get("title"),
                        "question_examples": p.get("question_examples", []),
                        "similarity": round(sim, 4),
                    }
                )
        return out

    def match(self, question: str) -> tuple[DecisionMatch, dict[str, Any] | None]:
        cands = self.candidates(question)
        if not cands:
            return DecisionMatch(), None
        res = self.llm.complete_json("decision.match", {"question": question, "packs": cands}, DecisionMatch)
        m = res.value
        pack = next((p for p in self.packs() if p["decision"] == m.decision), None)
        return m, pack

    # -- evaluation -------------------------------------------------------------------------------------
    def _scope_value(self, scope: str, hint: dict[str, str], principal: Principal | None) -> str | None:
        if scope in hint:
            return hint[scope]
        if scope == "region":
            for finer, col in (("substation", "substation_id"), ("feeder", "feeder_id")):
                if finer in hint:
                    return str(
                        self.wh.scalar(f"SELECT max(region) FROM raw.feeders WHERE {col} = ?", [hint[finer]])
                    )
            if principal and principal.region:
                return principal.region
        return None

    def _base(self, metric: str, scope: str, value: str | None) -> tuple[str, str, str, str]:
        m = config()["metrics"][metric]
        product = str(m["product"])
        dim = str(config()["scopes"][scope]["dimension"])
        el = self.semantic.get(product, str(m["measure"]))
        if el is None:
            raise KeyError(f"{product} has no measure {m['measure']}")
        td = self.semantic.time_dimension(product)
        assert td is not None
        lo, hi = resolve_window(str(m["time_window"])) or (None, None)
        where = (
            [f"{td.expression} >= DATE '{lo.date()}' AND {td.expression} < DATE '{hi.date()}'"]
            if lo and hi
            else []
        )
        if value is not None:
            where.append(f"{dim} = {_lit(value)}")
        where.append(f"feeder_id NOT LIKE '{SENTINEL_PREFIX}%'")
        return product, el.expression, " AND ".join(where), dim

    def current(self, metric: str, scope: str, value: str | None) -> tuple[float, str]:
        product, expr, where, _ = self._base(metric, scope, value)
        sql = self._rewrite(f"SELECT {expr} AS v FROM {product} WHERE {where}")
        return float(self.wh.scalar(sql) or 0.0), sql

    def counterfactual(self, metric: str, scope: str, value: str | None, driver: str, delta: int) -> float:
        product, expr, where, dim = self._base(metric, scope, value)
        unit = config()["drivers"][driver]["unit_row"]
        avg = float(self.wh.scalar(f"SELECT avg(customers_served) FROM {product} WHERE {where}") or 0.0)
        td = self.semantic.time_dimension(product)
        assert td is not None
        lo, _ = resolve_window(str(config()["metrics"][metric]["time_window"])) or (None, None)
        cols = []
        for c in self.wh.table_schema(product):
            if c.name in unit:
                e = str(unit[c.name]).replace("avg_feeder_customers", repr(avg))
                cols.append(f"CAST(({e}) * {delta} AS {c.data_type}) AS {c.name}")
            elif c.name == td.expression and lo is not None:
                cols.append(f"DATE '{lo.date()}' AS {c.name}")
            elif c.name == dim and value is not None:
                cols.append(f"{_lit(value)} AS {c.name}")
            elif c.name == "feeder_id":
                cols.append(f"'__counterfactual__' AS {c.name}")
            else:
                cols.append(f"CAST(NULL AS {c.data_type}) AS {c.name}")
        synthetic = f"SELECT {', '.join(cols)}"
        base = self._rewrite(f"SELECT * FROM {product} WHERE {where}")
        sql = f"SELECT {expr} AS v FROM ({base} UNION ALL {synthetic}) AS cf"
        return float(self.wh.scalar(sql) or 0.0)

    def sensitivity(
        self,
        metric: str,
        scope: str,
        value: str | None,
        driver: str,
        comparator: str,
        threshold: float,
        limit: int = 1_000_000,
    ) -> int | None:
        """Smallest increment of the driver that flips the comparator (exponential then binary search)."""
        cmp = OPS[comparator]
        if cmp(self.counterfactual(metric, scope, value, driver, 0), threshold):
            return 0
        hi = 1
        while not cmp(self.counterfactual(metric, scope, value, driver, hi), threshold):
            hi *= 2
            if hi > limit:
                return None
        lo = hi // 2
        while lo + 1 < hi:
            mid = (lo + hi) // 2
            if cmp(self.counterfactual(metric, scope, value, driver, mid), threshold):
                hi = mid
            else:
                lo = mid
        return hi

    def evaluate(
        self,
        pack: dict[str, Any],
        hint: dict[str, str],
        principal: Principal | None,
        rewrite: Callable[[str], str] | None = None,
    ) -> list[dict[str, Any]]:
        self._rewrite = rewrite or (lambda sql: sql)
        out = []
        for t in pack.get("thresholds", []):
            scope_value = self._scope_value(str(t["scope"]), hint, principal)
            cur, sql = self.current(str(t["metric"]), str(t["scope"]), scope_value)
            thr = float(t["value"])
            breached = OPS[str(t["comparator"])](cur, thr)
            margin = thr - cur if str(t["comparator"]).startswith(">") else cur - thr
            delta = None
            sentence = None
            if t.get("driver") and not breached:
                delta = self.sensitivity(
                    str(t["metric"]),
                    str(t["scope"]),
                    scope_value,
                    str(t["driver"]),
                    str(t["comparator"]),
                    thr,
                )
            if delta is not None and t.get("sensitivity_template"):
                text = str(t["sensitivity_template"]).format(
                    delta=number_words(delta) if delta < 11 else f"{delta:,}",
                    scope_value=scope_value or "the system",
                    value=f"{thr:g}",
                )
                sentence = text[0].upper() + text[1:]
            m = config()["metrics"][t["metric"]]
            out.append(
                {
                    "metric": t["metric"],
                    "measure": m["measure"],
                    "product": m["product"],
                    "scope": t["scope"],
                    "scope_value": scope_value,
                    "comparator": t["comparator"],
                    "threshold": thr,
                    "current": round(cur, 4),
                    "margin": round(margin, 4),
                    "breached": breached,
                    "meaning": t.get("meaning"),
                    "driver": t.get("driver"),
                    "flip_delta": delta,
                    "sensitivity": sentence,
                    "unit": m.get("unit"),
                    "sql": sql,
                }
            )
        return out

    def margin_summary(self, pack: dict[str, Any], t: dict[str, Any]) -> dict[str, Any]:
        """Tightest current margin across all scope values (marketplace card)."""
        metric, scope = str(t["metric"]), str(t["scope"])
        product, expr, where, dim = self._base(metric, scope, None)
        rows = self.wh.rows(f"SELECT {dim} AS k, {expr} AS v FROM {product} WHERE {where} GROUP BY {dim}")
        if not rows:
            return {}
        thr = float(t["value"])
        greater = str(t["comparator"]).startswith(">")
        best = min(rows, key=lambda r: (thr - float(r["v"] or 0)) if greater else (float(r["v"] or 0) - thr))
        cur = float(best["v"] or 0)
        return {
            "scope_value": best["k"],
            "current": round(cur, 2),
            "margin": round(thr - cur if greater else cur - thr, 2),
            "breached": OPS[str(t["comparator"])](cur, thr),
        }


def extract_scope(question: str) -> dict[str, str]:
    scope: dict[str, str] = {}
    if m := re.search(r"\b(S-\d{2})\b", question):
        scope["substation"] = m.group(1)
    if m := re.search(r"\b(F-\d{3})\b", question):
        scope["feeder"] = m.group(1)
    if m := re.search(r"\b(north|central|south)\b", question, re.I):
        scope["region"] = m.group(1).upper()
    return scope
