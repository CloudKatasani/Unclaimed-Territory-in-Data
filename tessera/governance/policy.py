"""Policy engine: row filters and column masks applied by rewriting the SQL plan (never by the LLM)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import sqlglot
from sqlglot import exp

from tessera.jsonutil import loads
from tessera.warehouse.base import Warehouse

SENTINEL_PREFIX = "SNTL-"


@dataclass(frozen=True)
class Principal:
    user_id: str
    role: str
    region: str | None = None

    def attr(self, name: str) -> str | None:
        value = getattr(self, name, None)
        return None if value is None else str(value)


def load_principal(wh: Warehouse, user_id: str) -> Principal:
    rows = wh.rows("SELECT * FROM meta.users WHERE user_id = ?", [user_id])
    if not rows:
        raise KeyError(f"unknown user {user_id}")
    r = rows[0]
    return Principal(str(r["user_id"]), str(r["role"]), r["region"])


@dataclass(frozen=True)
class PolicyDecision:
    policy_id: str
    kind: str
    target_fqn: str
    applied: bool
    predicate: str | None
    sentence: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "kind": self.kind,
            "target_fqn": self.target_fqn,
            "applied": self.applied,
            "predicate": self.predicate,
            "sentence": self.sentence,
        }


def _table_fqn(t: exp.Table) -> str:
    return f"{t.db}.{t.name}" if t.db else t.name


class PolicyEngine:
    def __init__(self, wh: Warehouse) -> None:
        self.wh = wh

    def policies_for(self, fqn: str) -> list[dict[str, Any]]:
        return self.wh.rows("SELECT * FROM meta.policies WHERE target_fqn = ? ORDER BY policy_id", [fqn])

    def rewrite(
        self, sql: str, principal: Principal, *, sentinel_keys: dict[str, str] | None = None
    ) -> tuple[str, list[PolicyDecision]]:
        tree = sqlglot.parse_one(sql, dialect="duckdb")
        decisions: list[PolicyDecision] = []
        for select in list(tree.find_all(exp.Select)):
            from_ = select.args.get("from_") or select.args.get("from")
            tables = [t for t in select.find_all(exp.Table) if t.find_ancestor(exp.Select) is select]
            if from_ is None or not tables:
                continue
            for t in tables:
                fqn = _table_fqn(t)
                qual = t.alias_or_name
                for pol in self.policies_for(fqn):
                    exempt = set(loads(pol["exempt_roles"]) or [])
                    if pol["kind"] == "row_filter":
                        decisions.append(self._row_filter(select, pol, principal, exempt, qual))
                    elif pol["kind"] == "column_mask":
                        decisions.append(self._column_mask(select, pol, principal, exempt))
                if sentinel_keys and fqn in sentinel_keys:
                    col = sentinel_keys[fqn]
                    pred = sqlglot.parse_one(f"{qual}.{col} NOT LIKE '{SENTINEL_PREFIX}%'", dialect="duckdb")
                    select.where(pred, copy=False)
        return tree.sql(dialect="duckdb"), decisions

    def _row_filter(
        self, select: exp.Select, pol: dict[str, Any], principal: Principal, exempt: set[str], qual: str
    ) -> PolicyDecision:
        attr = str(pol["principal_attr"])
        target = str(pol["target_fqn"])
        if principal.role in exempt:
            return PolicyDecision(
                str(pol["policy_id"]),
                "row_filter",
                target,
                False,
                None,
                f"No row filter for {principal.user_id} (role {principal.role} sees all {attr}s)",
            )
        value = principal.attr(attr)
        expr_text = str(pol["expression"])
        if value is None:
            predicate = "FALSE"
            sentence = f"No rows for {principal.user_id}: no {attr} assigned"
        else:
            literal = "'" + value.replace("'", "''") + "'"
            predicate = expr_text.replace(f":user.{attr}", literal)
            sentence = f"Filtered to {attr} {value} for {principal.user_id}"
        cond = sqlglot.parse_one(predicate, dialect="duckdb")
        for col in cond.find_all(exp.Column):
            if not col.table:
                col.set("table", exp.to_identifier(qual))
        select.where(cond, copy=False)
        return PolicyDecision(str(pol["policy_id"]), "row_filter", target, True, cond.sql("duckdb"), sentence)

    def _column_mask(
        self, select: exp.Select, pol: dict[str, Any], principal: Principal, exempt: set[str]
    ) -> PolicyDecision:
        column = str(pol["target_column"])
        target = str(pol["target_fqn"])
        if principal.role in exempt:
            return PolicyDecision(
                str(pol["policy_id"]),
                "column_mask",
                target,
                False,
                None,
                f"{column} shown unmasked to {principal.user_id}",
            )
        mask = sqlglot.parse_one(str(pol["expression"]), dialect="duckdb")
        hit = False
        for proj in select.expressions:
            for col in list(proj.find_all(exp.Column)):
                if col.name == column:
                    hit = True
                    if proj is col:
                        new = exp.alias_(mask.copy(), column)
                        proj.replace(new)
                    else:
                        col.replace(mask.copy())
        sentence = f"{column} masked ({pol['classification']}) for {principal.user_id}"
        return PolicyDecision(
            str(pol["policy_id"]), "column_mask", target, hit, str(pol["expression"]), sentence
        )


# -- SQL guard: the trust boundary for agent-generated SQL ---------------------------------------
class SQLRejected(ValueError):
    pass


def guard_generated_sql(sql: str, allowed_write_schema_prefixes: tuple[str, ...]) -> exp.Expression:
    """Only SELECT / CREATE TABLE AS SELECT / CREATE VIEW into draft or shadow schemas are allowed."""
    try:
        statements = sqlglot.parse(sql, dialect="duckdb")
    except sqlglot.errors.ParseError as exc:
        raise SQLRejected(f"unparseable SQL: {exc}") from exc
    statements = [s for s in statements if s is not None]
    if len(statements) != 1:
        raise SQLRejected("exactly one statement is allowed")
    stmt = statements[0]
    if isinstance(stmt, exp.Select | exp.Union):
        return stmt
    if isinstance(stmt, exp.Create):
        kind = str(stmt.args.get("kind", "")).upper()
        if kind not in ("TABLE", "VIEW"):
            raise SQLRejected(f"CREATE {kind} is not allowed")
        target = stmt.this.find(exp.Table) if not isinstance(stmt.this, exp.Table) else stmt.this
        if target is None or not any(str(target.db).startswith(p) for p in allowed_write_schema_prefixes):
            raise SQLRejected(f"writes are only allowed into {allowed_write_schema_prefixes}")
        if not isinstance(stmt.expression, exp.Select | exp.Union):
            raise SQLRejected("CREATE must be AS SELECT")
        return stmt
    raise SQLRejected(f"statement type {type(stmt).__name__} is not allowed")
