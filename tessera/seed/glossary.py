"""Loader for the domain glossary (sector-specific vocabulary)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

PATH = Path(__file__).resolve().parent / "glossary.yaml"


@dataclass(frozen=True)
class Concept:
    name: str
    label: str
    specificity: int
    phrases: tuple[str, ...]
    derived_from: str | None


@lru_cache(maxsize=1)
def load() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(PATH.read_text())
    return data


def concepts() -> list[Concept]:
    out = []
    for name, c in load()["concepts"].items():
        out.append(
            Concept(
                name, c["label"], int(c.get("specificity", 1)), tuple(c["phrases"]), c.get("derived_from")
            )
        )
    return out


def find_concepts(text: str) -> list[tuple[str, str]]:
    """Longest-first, non-overlapping glossary matches: [(concept, phrase)] in text order."""
    t = text.lower()
    candidates = sorted(
        ((p, c.name) for c in concepts() for p in c.phrases), key=lambda x: (-len(x[0]), x[0])
    )
    taken: list[tuple[int, int]] = []
    hits: list[tuple[int, str, str]] = []
    for phrase, concept in candidates:
        for m in re.finditer(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", t):
            s, e = m.span()
            if any(s < b and e > a for a, b in taken):
                continue
            taken.append((s, e))
            hits.append((s, concept, phrase))
    return [(c, p) for _, c, p in sorted(hits)]


def covers(concept: str, element_phrases: list[str]) -> bool:
    c = next((x for x in concepts() if x.name == concept), None)
    if c is None:
        return False
    for ep in element_phrases:
        for p in c.phrases:
            if re.search(rf"(?<![a-z0-9]){re.escape(p)}(?![a-z0-9])", ep.lower()):
                return True
    return False


def intent_label(concept_names: set[str]) -> str | None:
    for entry in load().get("intent_labels", []):
        if set(entry["concepts"]) == concept_names:
            return str(entry["label"])
    return None


def contract_template(concept_names: set[str]) -> str | None:
    for entry in load().get("intent_labels", []):
        if set(entry["concepts"]) == concept_names and entry.get("contract_template"):
            name = str(entry["contract_template"])
            return (Path(__file__).resolve().parent / "contract_templates" / name).read_text()
    return None


def entities() -> dict[str, dict[str, Any]]:
    ents: dict[str, dict[str, Any]] = load().get("entities", {})
    return ents
