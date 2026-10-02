"""Consumer migration agent (idea 17): breaking changes migrate their own consumers.

On a breaking publish the agent rewrites every registered consumer of the product with the contract's
rename map (saved questions via the semantic model, SQL via sqlglot, agent prompts via the LLM),
shadow-runs old against new (old artifact on the archived previous version, new artifact on the new
version) and proposes the rewrite with its divergence. Accept swaps the artifact (with provenance);
reject pins the consumer to the archived version with a sunset date.
"""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Any

import sqlglot
from pydantic import BaseModel
from sqlglot import exp

from tessera import clock, ids
from tessera.agents.answer import serialise
from tessera.governance.policy import PolicyEngine, load_principal
from tessera.governance.provenance import Artifact, ProvenanceLedger
from tessera.governance.semantic import Element, Plan, SemanticModel, compile_plan
from tessera.jsonutil import dumps, loads
from tessera.llm.client import LLM
from tessera.publisher.promote import rewrite_tables
from tessera.warehouse.base import Warehouse

AGENT = "migration_agent@0.1.0"
SUNSET_DAYS = 90


class MigratedText(BaseModel):
    text: str


def archive_fqn(fqn: str, version: int) -> str:
    return f"dp_archive.{fqn.split('.', 1)[1]}__v{version}"


def rename_sql(sql: str, renames: dict[str, str]) -> str:
    """Rename column references; keep each projection's output name so the consumer's shape is stable."""
    tree = sqlglot.parse_one(sql, dialect="duckdb")
    for proj in tree.find_all(exp.Select):
        new_exprs = []
        for e in proj.expressions:
            if isinstance(e, exp.Column) and e.name in renames:
                new_exprs.append(exp.alias_(exp.column(renames[e.name], table=e.table or None), e.name))
            else:
                new_exprs.append(e)
        proj.set("expressions", new_exprs)
    for col in tree.find_all(exp.Column):
        if col.name in renames:
            col.set("this", exp.to_identifier(renames[col.name]))
    return str(tree.sql(dialect="duckdb"))


def rename_plan(plan: dict[str, Any], renames: dict[str, str]) -> dict[str, Any]:
    out = dict(plan)
    out["metrics"] = [renames.get(m, m) for m in plan.get("metrics", [])]
    out["dimensions"] = [renames.get(d, d) for d in plan.get("dimensions", [])]
    out["filters"] = [
        {**f, "dimension": renames.get(f["dimension"], f["dimension"])} for f in plan.get("filters", [])
    ]
    return out


def _values(rows: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
    return sorted((tuple(r.values()) for r in rows), key=lambda t: tuple(str(x) for x in t))


def value_divergence(old: list[dict[str, Any]], new: list[dict[str, Any]]) -> float:
    """Shape-insensitive comparison (column names may differ after a rename)."""
    a, b = _values(old), _values(new)
    if len(a) != len(b):
        return 1.0
    worst = 0.0
    for ra, rb in zip(a, b, strict=True):
        if len(ra) != len(rb):
            return 1.0
        for x, y in zip(ra, rb, strict=True):
            if isinstance(x, int | float) and isinstance(y, int | float):
                worst = max(worst, abs(x - y) / max(abs(x), 1e-9) if abs(x - y) > 1e-9 else 0.0)
            elif x != y:
                return 1.0
    return worst


class FrozenSemantics(SemanticModel):
    """A snapshot of semantic elements (e.g. the previous version's), detached from the warehouse."""

    def __init__(self, elements: list[Element]) -> None:
        self.elements = list(elements)

    def refresh(self) -> None:
        return None


class MigrationAgent:
    def __init__(
        self, wh: Warehouse, llm: LLM, semantic: SemanticModel, policy: PolicyEngine, ledger: ProvenanceLedger
    ) -> None:
        self.wh = wh
        self.llm = llm
        self.semantic = semantic
        self.policy = policy
        self.ledger = ledger
        self.sentinel_keys: dict[str, str] = {}

    def consumers(self, fqn: str) -> list[dict[str, Any]]:
        rows = self.wh.rows("SELECT * FROM meta.consumers ORDER BY consumer_id")
        return [r for r in rows if fqn in (loads(r["fqn_refs"]) or [])]

    # -- per-kind rewrite + shadow run --------------------------------------------------------------
    def _run(
        self, sem: SemanticModel, plan_data: dict[str, Any], owner: str, table_map: dict[str, str]
    ) -> list[dict[str, Any]]:
        plan = Plan.model_validate(plan_data)
        # policies are attached to the product, so apply them first, then point at the archived version
        sql, _ = self.policy.rewrite(
            compile_plan(sem, plan), load_principal(self.wh, owner), sentinel_keys=self.sentinel_keys
        )
        return serialise(self.wh.rows(rewrite_tables(sql, table_map)))

    def propose(
        self,
        fqn: str,
        from_version: int,
        to_version: int,
        renames: dict[str, str],
        old_elements: list[Element],
    ) -> list[str]:
        archive = archive_fqn(fqn, from_version)
        old_sem = FrozenSemantics(old_elements)
        out = []
        for c in self.consumers(fqn):
            kind = str(c["kind"])
            old_text = str(c["artifact_text"])
            if kind == "saved_question":
                old_plan = loads(old_text)
                new_plan = rename_plan(old_plan, renames)
                new_text = dumps(new_plan)
                old_rows = self._run(old_sem, old_plan, str(c["owner"]), {fqn: archive})
                new_rows = self._run(self.semantic, new_plan, str(c["owner"]), {})
            elif kind == "dashboard":
                new_text = rename_sql(old_text, renames)
                old_rows = serialise(self.wh.rows(rewrite_tables(old_text, {fqn: archive})))
                new_rows = serialise(self.wh.rows(new_text))
            else:  # agent prompt
                res = self.llm.complete_json(
                    "migrate.prompt", {"prompt": old_text, "renames": renames, "product": fqn}, MigratedText
                )
                new_text = res.value.text
                old_rows, new_rows = self._agent_shadow(fqn, archive, renames, old_sem)
            div = value_divergence(old_rows, new_rows)
            shadow = {
                "old_rows": len(old_rows),
                "new_rows": len(new_rows),
                "sample_old": old_rows[:3],
                "sample_new": new_rows[:3],
            }
            mid = ids.new_id(self.wh, "migration")
            self.wh.execute(
                "INSERT INTO meta.migrations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'proposed', ?, NULL, NULL)",
                [
                    mid,
                    c["consumer_id"],
                    fqn,
                    from_version,
                    to_version,
                    old_text,
                    new_text,
                    div,
                    dumps(shadow),
                    clock.naive_utc(clock.now()),
                ],
            )
            out.append(mid)
        return out

    def _agent_shadow(
        self, fqn: str, archive: str, renames: dict[str, str], old_sem: SemanticModel
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Replay benchmark questions that use renamed elements: old names on v1 vs new names on v2."""
        old_all: list[dict[str, Any]] = []
        new_all: list[dict[str, Any]] = []
        for r in self.wh.rows(
            "SELECT asked_by, gold_plan FROM meta.benchmark_questions ORDER BY question_id"
        ):
            plan = loads(r["gold_plan"])
            if plan.get("product") != fqn or not set(plan.get("metrics", [])) & set(renames):
                continue
            old_all += self._run(old_sem, plan, str(r["asked_by"]), {fqn: archive})
            new_all += self._run(self.semantic, rename_plan(plan, renames), str(r["asked_by"]), {})
        return old_all, new_all

    # -- decisions -------------------------------------------------------------------------------------
    def accept(self, migration_id: str, user: str) -> None:
        m = self.wh.rows("SELECT * FROM meta.migrations WHERE migration_id = ?", [migration_id])[0]
        if m["status"] != "proposed":
            return
        self.wh.execute(
            "UPDATE meta.consumers SET artifact_text = ?, pinned_version = NULL, sunset_at = NULL "
            "WHERE consumer_id = ?",
            [m["new_text"], m["consumer_id"]],
        )
        self.wh.execute(
            "UPDATE meta.migrations SET status = 'accepted', decided_at = ?, decided_by = ? "
            "WHERE migration_id = ?",
            [clock.naive_utc(clock.now()), user, migration_id],
        )
        self.ledger.record(
            Artifact(
                "migration",
                str(m["consumer_id"]),
                str(m["new_text"]),
                agent=AGENT,
                model_name="deterministic"
                if not str(m["consumer_id"]).startswith("agent:")
                else self.llm.mode,
                prompt_id="migrate.prompt" if str(m["consumer_id"]).startswith("agent:") else None,
                input_refs={
                    "migration_id": migration_id,
                    "product": m["product_fqn"],
                    "from_version": m["from_version"],
                    "to_version": m["to_version"],
                },
                tests_run={
                    "shadow_divergence": {"passed": float(m["divergence"]) == 0.0, "value": m["divergence"]}
                },
                approved_by=user,
            )
        )

    def reject(self, migration_id: str, user: str) -> None:
        m = self.wh.rows("SELECT * FROM meta.migrations WHERE migration_id = ?", [migration_id])[0]
        sunset = clock.naive_utc(clock.now() + timedelta(days=SUNSET_DAYS))
        self.wh.execute(
            "UPDATE meta.consumers SET pinned_version = ?, sunset_at = ? WHERE consumer_id = ?",
            [m["from_version"], sunset, m["consumer_id"]],
        )
        self.wh.execute(
            "UPDATE meta.migrations SET status = 'held_on_previous', decided_at = ?, decided_by = ? "
            "WHERE migration_id = ?",
            [clock.naive_utc(clock.now()), user, migration_id],
        )

    def accept_all(self, fqn: str, user: str) -> int:
        rows = self.wh.rows(
            "SELECT migration_id FROM meta.migrations WHERE product_fqn = ? AND status = 'proposed' "
            "ORDER BY migration_id",
            [fqn],
        )
        for r in rows:
            self.accept(str(r["migration_id"]), user)
        return len(rows)

    def run_saved(self, consumer_id: str, user: str, answer: Any) -> Any:
        c = self.wh.rows("SELECT * FROM meta.consumers WHERE consumer_id = ?", [consumer_id])[0]
        plan = Plan.model_validate(loads(c["artifact_text"]))
        return answer.run_plan(plan, user, str(c["title"]), kind="saved")


def mock_migrate_prompt(text: str, renames: dict[str, str]) -> str:
    for old, new in renames.items():
        text = re.sub(rf"(?<![A-Za-z0-9_]){re.escape(old)}(?![A-Za-z0-9_])", new, text)
    return text
