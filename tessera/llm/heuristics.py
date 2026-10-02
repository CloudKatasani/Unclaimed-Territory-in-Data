"""Deterministic rule-based responders used in mock mode when no recorded fixture exists.

They stand in for the LLM so the demo runs offline. Each takes the exact ``variables`` dict the
agent sends and returns JSON matching the prompt's output schema.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from tessera.seed import glossary

Responder = Callable[[dict[str, Any]], dict[str, Any]]
RESPONDERS: dict[str, Responder] = {}


def responder(prompt_id: str) -> Callable[[Responder], Responder]:
    def wrap(fn: Responder) -> Responder:
        RESPONDERS[prompt_id] = fn
        return fn

    return wrap


def _has(text: str, phrase: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(phrase.lower())}(?![a-z0-9])", text) is not None


def _phrases(el: dict[str, Any]) -> list[str]:
    return [str(el["name"]).replace("_", " ").lower(), *[str(s).lower() for s in el.get("synonyms", [])]]


TIME_PATTERNS = [
    (r"\b(last|past|previous) (3|three) months\b", "last_3_months"),
    (r"\b(last|past|previous) month\b", "last_month"),
    (r"\b(last|past) 7 days\b", "last_7_days"),
    (r"\b(last|past|previous) week\b", "last_week"),
    (r"\b(this month|month to date|mtd|so far this month)\b", "this_month"),
    (r"\byesterday\b", "yesterday"),
]
FILTER_PATTERNS = [
    (r"\b(F-\d{3})\b", "feeder_id"),
    (r"\b(S-\d{2})\b", "substation_id"),
    (r"\b(M-\d{5})\b", "meter_id"),
]
REGION_RE = r"\b(?:in )?(?:the )?(north|central|south)(?: region)?\b"
GROUP_RE = r"\b(?:by|per|for each|each|which|list|across)\s+(?:the\s+)?([a-z_]+)"


@responder("answer.plan")
def answer_plan(v: dict[str, Any]) -> dict[str, Any]:
    question = str(v["question"])
    q = question.lower()
    candidates: list[dict[str, Any]] = list(v["candidates"])
    products = sorted({c["product"] for c in candidates})

    time_window = None
    for pat, label in TIME_PATTERNS:
        if re.search(pat, q):
            time_window = label
            q = re.sub(pat, " ", q)
            break
    filters: list[dict[str, Any]] = []
    for pat, dim in FILTER_PATTERNS:
        for m in re.finditer(pat, question):
            filters.append({"dimension": dim, "op": "=", "value": m.group(1)})
            q = q.replace(m.group(1).lower(), " ")
    if "my region" not in q:
        rm = re.search(REGION_RE, q)
        if rm and ("region" in q or "in the" in q):
            filters.append({"dimension": "region", "op": "=", "value": rm.group(1).upper()})
    q = q.replace("my region", " ")

    concept_hits = glossary.find_concepts(q)
    specificity = {c.name: c.specificity for c in glossary.concepts()}
    measures = [c for c in candidates if c["type"] in ("metric", "measure")]
    direct = [c for c in measures if any(_has(q, p) for p in _phrases(c))]
    # a metric hit whose phrase is fully inside a glossary concept phrase is not a direct hit
    concept_phrases = [p for _, p in concept_hits]
    direct = [
        c
        for c in direct
        if not all(any(_has(cp, p) for cp in concept_phrases) for p in _phrases(c) if _has(q, p))
    ]

    def covered(product: str) -> set[str]:
        els = [c for c in candidates if c["product"] == product]
        return {name for name, _ in concept_hits if any(glossary.covers(name, _phrases(e)) for e in els)}

    def score(product: str) -> tuple[float, str]:
        s = 2.0 * len(covered(product)) + 3.0 * sum(1 for c in direct if c["product"] == product)
        return (-s, product)

    requested = [p for _, p in concept_hits] + [c["name"] for c in direct]
    if not products or (not concept_hits and not direct):
        return {
            "product": None,
            "metrics": [],
            "dimensions": [],
            "filters": [],
            "time_window": time_window,
            "unmatched": [w for w in re.findall(r"[a-z]{4,}", q)][:4],
            "requested_terms": requested,
            "matched_terms": [],
        }
    product = sorted(products, key=score)[0]
    cov = covered(product)
    metrics = [c["name"] for c in direct if c["product"] == product]
    if not metrics:
        ranked = sorted(
            (c for c in measures if c["product"] == product),
            key=lambda c: (-sum(specificity[n] for n in cov if glossary.covers(n, _phrases(c))), c["name"]),
        )
        if ranked and any(glossary.covers(n, _phrases(ranked[0])) for n in cov):
            metrics = [ranked[0]["name"]]
    unmatched = [p for n, p in concept_hits if n not in cov]
    dims_available = [c for c in candidates if c["product"] == product and c["type"] == "dimension"]
    dimensions: list[str] = []
    for m in re.finditer(GROUP_RE, q):
        word = m.group(1)
        for d in dims_available:
            if any(word == p or word.rstrip("s") == p for p in _phrases(d)) and d["name"] not in dimensions:
                dimensions.append(d["name"])
    for d in dims_available:
        wants_time = (d.get("grain") == "day" and _has(q, "daily")) or (
            d.get("grain") == "month" and _has(q, "monthly")
        )
        if wants_time and d["name"] not in dimensions:
            dimensions.append(d["name"])
    filters = [f for f in filters if any(d["name"] == f["dimension"] for d in dims_available)]
    matched = list(dict.fromkeys([p for n, p in concept_hits if n in cov] + metrics))
    return {
        "product": product if metrics else None,
        "metrics": metrics,
        "dimensions": dimensions,
        "filters": filters,
        "time_window": time_window,
        "unmatched": unmatched if metrics else (unmatched or [p for _, p in concept_hits]),
        "requested_terms": requested,
        "matched_terms": matched,
    }
