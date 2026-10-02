"""Property tests: synthetic edge-case source rows derived from a contract's join path.

Small sources are copied as-is; large sources are replaced by synthetic rows placed exactly on the
boundaries of every range condition in the join path (inclusive lower bound, exclusive upper bound),
with zero, NULL and max-precision values. The model is run over this isolated copy and must still
satisfy every contract expectation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import sqlglot
from sqlglot import exp

from tessera.governance.contracts import Contract
from tessera.warehouse.base import Warehouse

LARGE = 50_000
NUMERIC = {"DOUBLE", "FLOAT", "REAL", "INTEGER", "BIGINT", "DECIMAL", "SMALLINT", "HUGEINT"}


@dataclass
class Cond:
    left: tuple[str, str]
    op: str
    right: tuple[str, str]


def _col(c: exp.Expression) -> tuple[str, str]:
    assert isinstance(c, exp.Column)
    return f"{c.args['db'].name}.{c.table}" if c.args.get("db") else c.table, c.name


def parse_join_path(join_path: list[str]) -> list[Cond]:
    out = []
    ops: dict[type[Any], str] = {
        exp.EQ: "=",
        exp.GTE: ">=",
        exp.GT: ">",
        exp.LT: "<",
        exp.LTE: "<=",
    }
    for text in join_path:
        e = sqlglot.parse_one(text, dialect="duckdb")
        op = ops.get(type(e))
        if op is None or not isinstance(e.this, exp.Column) or not isinstance(e.expression, exp.Column):
            continue
        out.append(Cond(_col(e.this), op, _col(e.expression)))
    return out


def build_property_schema(wh: Warehouse, contract: Contract, schema: str) -> dict[str, Any]:
    """Create `schema` with copies of the sources plus synthetic edge rows. Returns a description."""
    wh.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
    wh.execute(f"CREATE SCHEMA {schema}")
    conds = parse_join_path(contract.join_path)
    large = [s for s in contract.sources if int(wh.scalar(f"SELECT count(*) FROM {s}") or 0) > LARGE]
    cases: list[dict[str, Any]] = []
    for src in contract.sources:
        name = src.split(".", 1)[1]
        if src not in large:
            wh.execute(f"CREATE TABLE {schema}.{name} AS SELECT * FROM {src}")
            continue
        wh.execute(f"CREATE TABLE {schema}.{name} AS SELECT * FROM {src} LIMIT 0")
        cases += _synthesise(wh, contract, src, f"{schema}.{name}", conds, schema)
    return {"schema": schema, "synthetic_sources": large, "cases": cases}


def _synthesise(
    wh: Warehouse, contract: Contract, src: str, dst: str, conds: list[Cond], schema: str
) -> list[dict[str, Any]]:
    mine = [c for c in conds if src in (c.left[0], c.right[0])]
    others = [c for c in conds if src not in (c.left[0], c.right[0])]
    tables = sorted(
        {t for c in others for t in (c.left[0], c.right[0])}
        | {c.right[0] if c.left[0] == src else c.left[0] for c in mine}
    )
    if not tables:
        return []
    alias = {t: f"t{i}" for i, t in enumerate(tables)}
    on = " AND ".join(
        f"{alias[c.left[0]]}.{c.left[1]} {c.op} {alias[c.right[0]]}.{c.right[1]}" for c in others
    )
    joined = f"{schema}.{tables[0].split('.', 1)[1]} AS {alias[tables[0]]}" + "".join(
        f" CROSS JOIN {schema}.{t.split('.', 1)[1]} AS {alias[t]}" for t in tables[1:]
    )
    cols = wh.table_schema(src)
    exprs: dict[str, str] = {}
    lower: dict[str, str] = {}
    upper: dict[str, str] = {}
    for c in mine:
        (st, sc), op, (ot, oc) = (
            (c.left, c.op, c.right) if c.left[0] == src else (c.right, _flip(c.op), c.left)
        )
        ref = f"{alias[ot]}.{oc}"
        if op == "=":
            exprs[sc] = ref
        elif op in (">=", ">"):
            lower[sc] = ref
        else:
            upper[sc] = ref
    refs = sorted(set(exprs.values()) | set(lower.values()) | set(upper.values()))
    ref_alias = {r: f"r{i}" for i, r in enumerate(refs)}
    order = ", ".join(refs)
    proj = ", ".join(f"{r} AS {ref_alias[r]}" for r in refs)
    base = f"SELECT {proj} FROM {joined}" + (f" WHERE {on}" if on else "") + f" ORDER BY {order} LIMIT 3"
    template = wh.rows(f"SELECT * FROM {src} LIMIT 1")
    variants: list[tuple[str, str, float | None]] = [
        ("lower_bound_zero", "lower", 0.0),
        ("lower_bound_null", "lower", None),
        ("max_precision", "lower", 0.123456789),
        ("upper_bound_excluded", "upper", 1.5),
    ]
    out = []
    for label, edge, value in variants:
        select = []
        for col in cols:
            if col.name in exprs:
                select.append(f"b.{ref_alias[exprs[col.name]]} AS {col.name}")
            elif col.name in upper and edge == "upper":
                select.append(f"b.{ref_alias[upper[col.name]]} AS {col.name}")
            elif col.name in lower:
                select.append(f"b.{ref_alias[lower[col.name]]} AS {col.name}")
            elif col.data_type.upper().split("(")[0] in NUMERIC:
                lit = "NULL" if value is None else repr(value)
                select.append(f"CAST({lit} AS {col.data_type}) AS {col.name}")
            else:
                v = template[0][col.name] if template else None
                lit = "NULL" if v is None else "'" + str(v).replace("'", "''") + "'"
                select.append(f"CAST({lit} AS {col.data_type}) AS {col.name}")
        wh.execute(f"INSERT INTO {dst} SELECT {', '.join(select)} FROM ({base}) AS b")
        out.append(
            {"case": label, "source": src, "rows": int(wh.scalar(f"SELECT count(*) FROM ({base})") or 0)}
        )
    return out


def _flip(op: str) -> str:
    return {">=": "<=", "<=": ">=", ">": "<", "<": ">", "=": "="}[op]
