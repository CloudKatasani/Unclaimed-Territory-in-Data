"""Materialising models and promoting draft/shadow tables into `dp`."""

from __future__ import annotations

import sqlglot
from sqlglot import exp

from tessera.warehouse.base import Warehouse


def rewrite_tables(select_sql: str, mapping: dict[str, str]) -> str:
    """Point table references at other schemas, e.g. {'raw.meters': 'shadow_x.meters'}."""
    tree = sqlglot.parse_one(select_sql, dialect="duckdb")
    for t in tree.find_all(exp.Table):
        fqn = f"{t.db}.{t.name}" if t.db else t.name
        if fqn in mapping:
            db, _, name = mapping[fqn].partition(".")
            alias = t.args.get("alias")
            new = exp.table_(name, db=db)
            if alias is not None:
                new.set("alias", alias)
            elif t.name != name:
                new.set("alias", exp.TableAlias(this=exp.to_identifier(t.name)))
            t.replace(new)
    return tree.sql(dialect="duckdb")


def materialize(wh: Warehouse, fqn: str, select_sql: str, kind: str = "table") -> None:
    schema = fqn.split(".", 1)[0]
    wh.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
    if kind == "view":
        wh.execute(f"CREATE OR REPLACE VIEW {fqn} AS {select_sql}")
    else:
        wh.execute(f"CREATE OR REPLACE TABLE {fqn} AS {select_sql}")


def swap_in(wh: Warehouse, src_fqn: str, dst_fqn: str) -> None:
    """Atomically replace a published table with a built one."""
    with wh.transaction():
        wh.execute(f"CREATE OR REPLACE TABLE {dst_fqn} AS SELECT * FROM {src_fqn}")
