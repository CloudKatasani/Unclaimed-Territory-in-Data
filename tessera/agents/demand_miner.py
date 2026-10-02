"""Demand Miner (B2, core of ID-1): unanswered questions -> demand intents -> draft contracts."""

from __future__ import annotations

from collections import Counter
from typing import Any

import networkx as nx
import numpy as np
import yaml
from pydantic import BaseModel, Field
from sklearn.cluster import AgglomerativeClustering
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from tessera import clock, ids
from tessera.config import Settings
from tessera.governance import contracts as contracts_mod
from tessera.governance.catalog import Catalog
from tessera.governance.provenance import Artifact, ProvenanceLedger
from tessera.governance.semantic import SemanticModel
from tessera.jsonutil import dumps, loads
from tessera.llm.client import LLM
from tessera.orchestrator.bus import Bus
from tessera.orchestrator.notify import notify, record_metric
from tessera.seed import glossary
from tessera.warehouse.base import Warehouse

AGENT = "demand_miner@0.1.0"
UNRESOLVED = ("missing_data", "low_confidence", "abandoned")


class IntentSpec(BaseModel):
    label: str
    entities: list[str] = Field(default_factory=list)
    concepts: list[str] = Field(default_factory=list)
    measures: list[str] = Field(default_factory=list)
    grain: list[str] = Field(default_factory=list)
    time_window: str | None = None


class ContractDraft(BaseModel):
    contract_yaml: str


class DemandMiner:
    def __init__(
        self,
        wh: Warehouse,
        llm: LLM,
        semantic: SemanticModel,
        catalog: Catalog,
        ledger: ProvenanceLedger,
        bus: Bus,
        settings: Settings,
    ) -> None:
        self.wh = wh
        self.llm = llm
        self.semantic = semantic
        self.catalog = catalog
        self.ledger = ledger
        self.bus = bus
        self.settings = settings

    # -- 1. embed + cluster ----------------------------------------------------------------------
    def pending_questions(self) -> list[dict[str, Any]]:
        return self.wh.rows(
            f"SELECT * FROM meta.questions WHERE outcome IN {UNRESOLVED} AND intent_id IS NULL "
            "AND resolved_by_product IS NULL ORDER BY asked_at, question_id"
        )

    @staticmethod
    def normalise(question: dict[str, Any]) -> str:
        """Mock-mode intent normalisation: glossary concepts + entities + unmatched terms."""
        text = str(question["text"]).lower()
        tokens = [f"concept_{c}" for c, _ in glossary.find_concepts(text)]
        for name, ent in glossary.entities().items():
            if any(f" {p} " in f" {text} " for p in ent["phrases"]):
                tokens.append(f"entity_{name}")
        if not tokens:
            plan = loads(question["plan_json"]) or {}
            tokens = [str(t).replace(" ", "_") for t in plan.get("unmatched", [])] or text.split()
        return " ".join(sorted(set(tokens)))

    def cluster(self, questions: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
        if not questions:
            return []
        texts = [self.normalise(q) for q in questions]
        if len(questions) == 1:
            return [questions]
        vec = TfidfVectorizer(token_pattern=r"[^ ]+").fit_transform(texts)
        dist = 1 - cosine_similarity(vec)
        np.clip(dist, 0, 2, out=dist)
        labels = AgglomerativeClustering(
            n_clusters=None,
            metric="precomputed",
            linkage="average",
            distance_threshold=self.settings.cluster_distance,
        ).fit_predict(dist)
        groups: dict[int, list[dict[str, Any]]] = {}
        for q, lab in zip(questions, labels, strict=True):
            groups.setdefault(int(lab), []).append(q)
        return sorted(groups.values(), key=lambda g: str(g[0]["question_id"]))

    # -- 3. gap classification ---------------------------------------------------------------------
    def covering_products(self, concept: str) -> list[str]:
        out = []
        for p in self.semantic.products():
            if any(glossary.covers(concept, e.phrases) for e in self.semantic.for_product(p)):
                out.append(p)
        return out

    def classify_gap(self, concepts: list[str]) -> tuple[str, dict[str, list[str]]]:
        derived = {c.name: c.derived_from for c in glossary.concepts()}
        coverage = {c: self.covering_products(c) for c in concepts}
        if concepts and set.intersection(*(set(v) for v in coverage.values())):
            return "missing_grain", coverage
        base_cov = {c: coverage[c] or self.covering_products(derived.get(c) or c) for c in concepts}
        if all(base_cov.values()):
            return "missing_join_path", coverage
        if any(self.search_columns(c) for c, v in base_cov.items() if not v):
            return "missing_attribute", coverage
        return "missing_domain", coverage

    # -- 4. source resolution ------------------------------------------------------------------------
    def search_columns(self, concept: str, k: int = 3) -> list[dict[str, Any]]:
        c = next((x for x in glossary.concepts() if x.name == concept), None)
        if c is None:
            return []
        cols = [x for x in self.catalog.columns() if x.searchable and x.classification != "PII"]
        docs = [
            f"{x.fqn.split('.')[-1].replace('_', ' ')} {x.column.replace('_', ' ')} {x.description}".lower()
            for x in cols
        ]
        query = " ".join(c.phrases)
        vec = TfidfVectorizer(ngram_range=(1, 2)).fit(docs + [query])
        sims = cosine_similarity(vec.transform([query]), vec.transform(docs))[0]
        ranked = sorted(range(len(cols)), key=lambda i: (-sims[i], cols[i].fqn, cols[i].column))
        return [
            {
                "concept": concept,
                "column": f"{cols[i].fqn}.{cols[i].column}",
                "score": round(float(sims[i]), 4),
            }
            for i in ranked[:k]
            if sims[i] > 0
        ]

    def fk_graph(self) -> nx.Graph[str]:
        g: nx.Graph[str] = nx.Graph()
        landing = {c.fqn for c in self.catalog.columns() if not c.searchable}
        for s, sc, d, dc in self.catalog.foreign_keys():
            if s in landing or d in landing:
                continue
            g.add_edge(s, d, condition=f"{s}.{sc} = {d}.{dc}")
        return g

    def resolve_sources(
        self, concepts: list[str], entities: list[str]
    ) -> tuple[list[dict[str, Any]], list[str], list[str], float]:
        sources = []
        tables: list[str] = []
        ents = glossary.entities()
        anchor = ents[entities[0]]["table"] if entities and entities[0] in ents else None
        for c in concepts:
            hits = self.search_columns(c)
            if hits:
                sources.append(hits[0])
                tables.append(hits[0]["column"].rsplit(".", 1)[0])
        for e in entities:
            if e in ents and ents[e]["table"] not in tables:
                tables.append(ents[e]["table"])
        g = self.fk_graph()
        anchor = anchor or (tables[0] if tables else None)
        path: list[str] = []
        conditions: list[str] = []
        if anchor:
            path.append(anchor)
            for t in sorted(
                set(tables) - {anchor}, key=lambda t: (-nx.shortest_path_length(g, anchor, t), t)
            ):
                sp = nx.shortest_path(g, anchor, t)
                for a, b in zip(sp, sp[1:], strict=False):
                    cond = str(g.edges[a, b]["condition"])
                    if cond not in conditions:
                        conditions.append(cond)
                for n in sp:
                    if n not in path:
                        path.append(n)
        confidence = round(1.0 / max(1, len(conditions)) ** 0.25, 4) if path else 0.0
        for s in sources:
            s["path"] = path
        return sources, path, conditions, confidence

    # -- scoring -------------------------------------------------------------------------------
    def demand_score(self, question_ids: list[str]) -> tuple[float, dict[str, Any]]:
        qs = self.wh.rows(
            f"SELECT q.asked_by, u.role FROM meta.questions q JOIN meta.users u ON u.user_id = q.asked_by "
            f"WHERE q.question_id IN ({', '.join('?' * len(question_ids))})",
            question_ids,
        )
        askers = {str(r["asked_by"]): str(r["role"]) for r in qs}
        weights = sum(self.settings.role_weights.get(role, 1) for role in askers.values())
        score = len(askers) * 2 + len(qs) + weights
        return float(score), {
            "distinct_askers": len(askers),
            "question_count": len(qs),
            "role_weight_sum": weights,
            "askers": sorted(askers),
        }

    # -- main ------------------------------------------------------------------------------------
    def mine(self) -> list[str]:
        """Cluster pending questions into intents (steps 1-4 + score). Returns new intent ids."""
        created = []
        for group in self.cluster(self.pending_questions()):
            created.append(self._intent_for(group))
        if created:
            record_metric(self.wh, "id1.intents_created", len(created))
        return created

    def _intent_for(self, group: list[dict[str, Any]]) -> str:
        qids = [str(q["question_id"]) for q in group]
        concepts = sorted({c for q in group for c, _ in glossary.find_concepts(str(q["text"]))})
        unmatched = sorted({u for q in group for u in (loads(q["plan_json"]) or {}).get("unmatched", [])})
        variables = {
            "questions": [str(q["text"]) for q in group],
            "concepts": concepts,
            "unmatched": unmatched,
        }
        spec = self.llm.complete_json("demand.intent", variables, IntentSpec).value
        gap_type, coverage = self.classify_gap(spec.concepts or concepts)
        sources, path, conditions, conf = self.resolve_sources(spec.concepts or concepts, spec.entities)
        score, breakdown = self.demand_score(qids)
        intent_id = ids.new_id(self.wh, "intent")
        required = {**spec.model_dump(), "coverage": coverage, "score_breakdown": breakdown}
        join = {"tables": path, "conditions": conditions, "confidence": conf}
        self.wh.execute(
            "INSERT INTO meta.demand_intents VALUES (?, ?, ?, ?, ?, ?, ?, NULL, 'open', ?, ?)",
            [
                intent_id,
                spec.label,
                dumps(qids),
                dumps(required),
                gap_type,
                dumps(sources),
                score,
                dumps(join),
                clock.naive_utc(clock.now()),
            ],
        )
        for q in qids:
            self.wh.execute("UPDATE meta.questions SET intent_id = ? WHERE question_id = ?", [intent_id, q])
        return intent_id

    def intent(self, intent_id: str) -> dict[str, Any]:
        rows = self.wh.rows("SELECT * FROM meta.demand_intents WHERE intent_id = ?", [intent_id])
        if not rows:
            raise KeyError(intent_id)
        r = rows[0]
        out = dict(r)
        for k in ("question_ids", "required_elements", "candidate_sources", "join_path"):
            out[k] = loads(r[k])
        return out

    # -- 5. contract drafting --------------------------------------------------------------------
    def draft_contract(self, intent_id: str) -> str:
        it = self.intent(intent_id)
        req = it["required_elements"]
        pii = self.catalog.pii_columns()
        source_tables = it["join_path"]["tables"]
        source_columns = {
            t: [c.column for c in self.catalog.columns(t) if (t, c.column) not in pii] for t in source_tables
        }
        variables: dict[str, Any] = {
            "intent": {
                "label": it["label"],
                "concepts": req["concepts"],
                "entities": req["entities"],
                "grain": req["grain"],
                "measures": req["measures"],
                "time_window": req["time_window"],
            },
            "gap_type": it["gap_type"],
            "sources": source_columns,
            "join_conditions": it["join_path"]["conditions"],
            "errors": [],
        }
        contract = None
        guard_notes: list[str] = []
        last = None
        for _attempt in range(3):  # first try + max 2 fix-ups
            last = self.llm.complete_json("demand.contract", variables, ContractDraft)
            try:
                contract = contracts_mod.parse(last.value.contract_yaml)
                contract, guard_notes = self.enforce_pii(contract)
                break
            except contracts_mod.ContractError as exc:
                variables = {**variables, "errors": [*variables["errors"], str(exc)]}
        if contract is None or last is None:
            self.wh.execute(
                "UPDATE meta.demand_intents SET status = 'needs_review' WHERE intent_id = ?", [intent_id]
            )
            raise contracts_mod.ContractError(f"could not draft a valid contract: {variables['errors']}")
        contract = contract.model_copy(update={"origin": "demand_miner", "demand_intent_id": intent_id})
        cid = contracts_mod.save(self.wh, contract, "draft", intent_id=intent_id)
        self.ledger.record(
            Artifact(
                "contract",
                contract.product,
                contract.to_yaml(),
                agent=AGENT,
                model_name=last.model_name,
                prompt_id=last.prompt_id,
                prompt_sha256=last.prompt_sha256,
                input_refs={
                    "intent_id": intent_id,
                    "question_ids": it["question_ids"],
                    "sources": source_tables,
                    "guardrail": guard_notes,
                },
            )
        )
        self.wh.execute(
            "UPDATE meta.demand_intents SET contract_id = ?, status = 'drafted' WHERE intent_id = ?",
            [cid, intent_id],
        )
        self.bus.emit(
            "contract.drafted", {"contract_id": cid, "intent_id": intent_id, "product": contract.product}
        )
        return cid

    def enforce_pii(self, contract: contracts_mod.Contract) -> tuple[contracts_mod.Contract, list[str]]:
        """Guardrail: drop PII-sourced columns unless the contract declares a masking policy for them."""
        pii_names = {col for (t, col) in self.catalog.pii_columns() if t in contract.sources}
        masked = {p.column for p in contract.policies if p.kind == "column_mask" and p.column}
        removed = [c.name for c in contract.columns if c.name in pii_names and c.name not in masked]
        if not removed:
            return contract, []
        keys = set(contract.primary_key) | set(contract.grain)
        if keys & set(removed):
            raise contracts_mod.ContractError(f"PII columns {removed} cannot be keys")
        cols = [c for c in contract.columns if c.name not in removed]
        return contract.model_copy(update={"columns": cols}), [
            f"removed unmasked PII column {c}" for c in removed
        ]

    def run(self) -> list[str]:
        """Mine and draft contracts for every new intent that has a resolvable source path."""
        out = []
        for iid in self.mine():
            if self.intent(iid)["join_path"]["tables"]:
                try:
                    out.append(self.draft_contract(iid))
                except contracts_mod.ContractError:
                    continue
        return out

    # -- 7. replay ----------------------------------------------------------------------------------
    def closure(self, intent_id: str) -> dict[str, Any]:
        it = self.intent(intent_id)
        qids = it["question_ids"]
        resolved = self.wh.scalar(
            f"SELECT count(*) FROM meta.questions WHERE question_id IN ({', '.join('?' * len(qids))}) "
            "AND resolved_by_product IS NOT NULL",
            qids,
        )
        return {"resolved": int(resolved or 0), "total": len(qids)}

    def replay(self, intent_id: str, product: str, answer: Any) -> dict[str, Any]:
        """On publish: replay every question of the intent; mark resolved ones and notify askers."""
        it = self.intent(intent_id)
        qids = it["question_ids"]
        qs = self.wh.rows(
            f"SELECT * FROM meta.questions WHERE question_id IN ({', '.join('?' * len(qids))}) "
            "ORDER BY asked_at, question_id",
            qids,
        )
        now = clock.naive_utc(clock.now())
        for q in qs:
            ans = answer.ask(str(q["text"]), str(q["asked_by"]), question_id=str(q["question_id"]), log=False)
            if ans.outcome in ("answered", "low_confidence") and ans.plan.get("product") == product:
                cert_id = ans.certificate["cert_id"] if ans.certificate else None
                self.wh.execute(
                    "UPDATE meta.questions SET resolved_by_product = ?, resolved_at = ?, cert_id = ? "
                    "WHERE question_id = ?",
                    [product, now, cert_id, q["question_id"]],
                )
                notify(
                    self.wh,
                    str(q["asked_by"]),
                    "question_resolved",
                    f'Your question "{q["text"]}" can now be answered from {product}.',
                    str(q["question_id"]),
                )
        result = self.closure(intent_id)
        status = "closed" if result["resolved"] == result["total"] else "partially_closed"
        self.wh.execute("UPDATE meta.demand_intents SET status = ? WHERE intent_id = ?", [status, intent_id])
        first = min(q["asked_at"] for q in qs)
        hours = (now - first).total_seconds() / 3600
        record_metric(
            self.wh,
            "id1.closure_rate",
            result["resolved"] / max(1, result["total"]),
            {"intent_id": intent_id, **result},
        )
        record_metric(self.wh, "id1.time_to_resolution_hours", hours, {"intent_id": intent_id})
        return result

    def labels(self) -> Counter[str]:
        return Counter(str(r["label"]) for r in self.wh.rows("SELECT label FROM meta.demand_intents"))


def contract_from_template(concepts: list[str]) -> str | None:
    tpl = glossary.contract_template(set(concepts))
    if tpl is None:
        return None
    data = yaml.safe_load(tpl)
    data["demand_intent_id"] = None
    return yaml.safe_dump(data, sort_keys=False, width=110)
