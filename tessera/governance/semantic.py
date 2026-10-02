"""Semantic model: elements, retrieval, and deterministic plan -> SQL compilation."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from tessera import clock, ids
from tessera.jsonutil import dumps, loads
from tessera.warehouse.base import Warehouse

TIME_GRAINS = {"month", "day", "timestamp"}
TimeWindow = Literal[
    "last_month", "last_3_months", "this_month", "last_week", "last_7_days", "yesterday", "all"
]


@dataclass(frozen=True)
class Element:
    element_id: str
    element_type: str
    name: str
    product_fqn: str
    expression: str
    grain: str | None
    certified: bool
    certified_by: str | None
    synonyms: tuple[str, ...] = field(default_factory=tuple)
    description: str = ""

    @property
    def phrases(self) -> list[str]:
        return [self.name.replace("_", " ").lower(), *[s.lower() for s in self.synonyms]]

    def brief(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.element_type,
            "product": self.product_fqn,
            "synonyms": list(self.synonyms),
            "grain": self.grain,
        }


class Filter(BaseModel):
    dimension: str
    op: Literal["=", "in"] = "="
    value: str | list[str]


class Plan(BaseModel):
    product: str | None = None
    metrics: list[str] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)
    filters: list[Filter] = Field(default_factory=list)
    time_window: TimeWindow | None = None
    unmatched: list[str] = Field(default_factory=list)
    requested_terms: list[str] = Field(default_factory=list)
    matched_terms: list[str] = Field(default_factory=list)
    decision: str | None = None


class SemanticModel:
    def __init__(self, wh: Warehouse) -> None:
        self.wh = wh
        self.refresh()

    def refresh(self) -> None:
        rows = self.wh.rows("SELECT * FROM meta.semantic_model ORDER BY product_fqn, element_type, name")
        self.elements = [
            Element(
                str(r["element_id"]),
                str(r["element_type"]),
                str(r["name"]),
                str(r["product_fqn"]),
                str(r["expression"] or ""),
                r["grain"],
                bool(r["certified"]),
                r["certified_by"],
                tuple(loads(r["synonyms"]) or []),
                str(r["description"] or ""),
            )
            for r in rows
        ]

    def products(self) -> list[str]:
        return sorted({e.product_fqn for e in self.elements})

    def for_product(self, product: str) -> list[Element]:
        return [e for e in self.elements if e.product_fqn == product]

    def get(self, product: str, name: str) -> Element | None:
        return next((e for e in self.elements if e.product_fqn == product and e.name == name), None)

    def time_dimension(self, product: str) -> Element | None:
        return next(
            (
                e
                for e in self.for_product(product)
                if e.element_type == "dimension" and e.grain in TIME_GRAINS
            ),
            None,
        )

    def add_element(
        self,
        element_type: str,
        name: str,
        product: str,
        expression: str,
        *,
        grain: str | None = None,
        certified: bool = False,
        certified_by: str | None = None,
        synonyms: list[str] | None = None,
        description: str = "",
    ) -> str:
        eid = ids.new_id(self.wh, "semantic")
        self.wh.execute(
            "INSERT INTO meta.semantic_model VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                eid,
                element_type,
                name,
                product,
                expression,
                grain,
                certified,
                certified_by,
                dumps(synonyms or []),
                description,
            ],
        )
        return eid

    # -- retrieval ---------------------------------------------------------------------------
    def retrieve(
        self, question: str, k: int = 15, products: list[str] | None = None
    ) -> tuple[list[Element], float]:
        """Synonym hits first, then TF-IDF over names/synonyms/descriptions up to k elements.

        `products` limits retrieval to an agent's scope (its entitled data products).
        """
        q = question.lower()
        pool = [e for e in self.elements if products is None or e.product_fqn in products]
        hits = [e for e in pool if any(_contains_phrase(q, p) for p in e.phrases)]
        hit_products = {e.product_fqn for e in hits if e.element_type in ("metric", "measure")}
        docs = [" ".join([*e.phrases, e.description.lower()]) for e in pool]
        vec = TfidfVectorizer(ngram_range=(1, 2)).fit(docs + [q])
        sims = cosine_similarity(vec.transform([q]), vec.transform(docs))[0]
        ranked = sorted(range(len(pool)), key=lambda i: (-sims[i], pool[i].element_id))
        chosen: list[Element] = list(hits)
        # all dimensions of products that have a measure hit, so the plan can group and filter
        for e in pool:
            if e.product_fqn in hit_products and e.element_type == "dimension" and e not in chosen:
                chosen.append(e)
        for i in ranked:
            if len(chosen) >= max(k, len(hits)):
                break
            if pool[i] not in chosen and sims[i] > 0:
                chosen.append(pool[i])
        score = float(max(sims)) if len(sims) else 0.0
        chosen.sort(key=lambda e: (e.product_fqn, e.element_type, e.name))
        return chosen, round(score, 4)


def _contains_phrase(text: str, phrase: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", text) is not None


# -- time windows ---------------------------------------------------------------------------------
def _month_start(d: date, back: int = 0) -> date:
    y, m = d.year, d.month - back
    while m <= 0:
        m += 12
        y -= 1
    return date(y, m, 1)


def resolve_window(window: str | None, now: datetime | None = None) -> tuple[datetime, datetime] | None:
    if window in (None, "all"):
        return None
    today = clock.naive_utc(now or clock.now()).date()
    start: date
    end: date
    if window == "last_month":
        start, end = _month_start(today, 1), _month_start(today)
    elif window == "last_3_months":
        start, end = _month_start(today, 3), _month_start(today)
    elif window == "this_month":
        start, end = _month_start(today), today + timedelta(days=1)
    elif window in ("last_week", "last_7_days"):
        start, end = today - timedelta(days=7), today
    elif window == "yesterday":
        start, end = today - timedelta(days=1), today
    else:
        raise ValueError(f"unknown time window {window}")
    return datetime.combine(start, datetime.min.time()), datetime.combine(end, datetime.min.time())


def _lit(v: str) -> str:
    return "'" + v.replace("'", "''") + "'"


class CompileError(ValueError):
    pass


def compile_plan(model: SemanticModel, plan: Plan, now: datetime | None = None) -> str:
    """Plan -> SQL, deterministically, using only semantic-model expressions."""
    if not plan.product or not plan.metrics:
        raise CompileError("plan has no product or metrics")
    product = plan.product
    table = product
    select: list[str] = []
    group: list[str] = []
    for d in plan.dimensions:
        el = model.get(product, d)
        if el is None or el.element_type != "dimension":
            raise CompileError(f"unknown dimension {d}")
        select.append(f"{el.expression} AS {el.name}")
        group.append(el.expression)
    for m in plan.metrics:
        el = model.get(product, m)
        if el is None or el.element_type not in ("metric", "measure"):
            raise CompileError(f"unknown metric {m}")
        select.append(f"{el.expression} AS {el.name}")
    where: list[str] = []
    for f in plan.filters:
        el = model.get(product, f.dimension)
        if el is None or el.element_type != "dimension":
            raise CompileError(f"unknown filter dimension {f.dimension}")
        if isinstance(f.value, list):
            where.append(f"{el.expression} IN ({', '.join(_lit(v) for v in f.value)})")
        else:
            where.append(f"{el.expression} = {_lit(f.value)}")
    window = resolve_window(plan.time_window, now)
    if window:
        td = model.time_dimension(product)
        if td is None:
            raise CompileError(f"{product} has no time dimension")
        lo, hi = window
        if td.grain in ("month", "day"):
            where.append(f"{td.expression} >= DATE '{lo.date()}' AND {td.expression} < DATE '{hi.date()}'")
        else:
            where.append(f"{td.expression} >= TIMESTAMP '{lo}' AND {td.expression} < TIMESTAMP '{hi}'")
    sql = f"SELECT {', '.join(select)} FROM {table}"
    if where:
        sql += " WHERE " + " AND ".join(where)
    if group:
        sql += " GROUP BY " + ", ".join(group) + " ORDER BY " + ", ".join(group)
    return sql
