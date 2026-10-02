"""Column-level lineage built by parsing model SQL with sqlglot; stored in meta.lineage_edges."""

from __future__ import annotations

from collections import deque

import networkx as nx
import sqlglot
from sqlglot import exp
from sqlglot.lineage import lineage as sg_lineage

from tessera import clock
from tessera.warehouse.base import Warehouse

TABLE_COL = "*"  # table-level dependency marker


def _fqn(t: exp.Table) -> str:
    return f"{t.db}.{t.name}" if t.db else t.name


def referenced_tables(select_sql: str) -> set[str]:
    tree = sqlglot.parse_one(select_sql, dialect="duckdb")
    ctes = {c.alias_or_name for c in tree.find_all(exp.CTE)}
    return {_fqn(t) for t in tree.find_all(exp.Table) if _fqn(t) not in ctes and t.name not in ctes}


def output_columns(select_sql: str) -> list[str]:
    tree = sqlglot.parse_one(select_sql, dialect="duckdb")
    if isinstance(tree, exp.Create):
        tree = tree.expression
    assert isinstance(tree, exp.Query)
    return list(tree.named_selects)


def _schema_for(wh: Warehouse, tables: set[str]) -> dict[str, dict[str, dict[str, str]]]:
    schema: dict[str, dict[str, dict[str, str]]] = {}
    for fqn in tables:
        db, _, name = fqn.partition(".")
        cols = wh.table_schema(fqn)
        if cols:
            schema.setdefault(db, {})[name] = {c.name: c.data_type for c in cols}
    return schema


def column_edges(wh: Warehouse, dst_fqn: str, select_sql: str) -> list[tuple[str, str, str, str]]:
    """Return (src_fqn, src_column, dst_fqn, dst_column) edges for a model."""
    tables = referenced_tables(select_sql)
    schema = _schema_for(wh, tables)
    edges: set[tuple[str, str, str, str]] = set()
    for col in output_columns(select_sql):
        node = sg_lineage(col, select_sql, schema=schema, dialect="duckdb")
        for n in node.walk():
            if isinstance(n.source, exp.Table) and not n.downstream:
                src = _fqn(n.source)
                src_col = n.name.split(".")[-1]
                if src in tables:
                    edges.add((src, src_col, dst_fqn, col))
    for t in tables:
        edges.add((t, TABLE_COL, dst_fqn, TABLE_COL))
    return sorted(edges)


class Lineage:
    def __init__(self, wh: Warehouse) -> None:
        self.wh = wh

    def rebuild_for(self, dst_fqn: str, select_sql: str, transform_id: str | None) -> int:
        edges = column_edges(self.wh, dst_fqn, select_sql)
        with self.wh.transaction():
            self.wh.execute("DELETE FROM meta.lineage_edges WHERE dst_fqn = ?", [dst_fqn])
            now = clock.naive_utc(clock.now())
            for s, sc, d, dc in edges:
                self.wh.execute(
                    "INSERT INTO meta.lineage_edges VALUES (?, ?, ?, ?, ?, ?)",
                    [s, sc, d, dc, transform_id, now],
                )
        return len(edges)

    def set_transform(self, dst_fqn: str, transform_id: str) -> None:
        self.wh.execute(
            "UPDATE meta.lineage_edges SET transform_id = ? WHERE dst_fqn = ?", [transform_id, dst_fqn]
        )

    def graph(self) -> nx.DiGraph[str]:
        g: nx.DiGraph[str] = nx.DiGraph()
        for r in self.wh.rows("SELECT src_fqn, src_column, dst_fqn, dst_column FROM meta.lineage_edges"):
            g.add_edge(f"{r['src_fqn']}.{r['src_column']}", f"{r['dst_fqn']}.{r['dst_column']}")
        return g

    def upstream_columns(self, fqn: str, columns: list[str]) -> list[str]:
        """All fqn.column nodes upstream of the given columns (inclusive), plus table-level deps."""
        g = self.graph()
        start = [f"{fqn}.{c}" for c in columns] + [f"{fqn}.{TABLE_COL}"]
        seen: set[str] = set()
        queue = deque(n for n in start if n in g or n.endswith(TABLE_COL))
        for n in start:
            seen.add(n)
        while queue:
            n = queue.popleft()
            if n not in g:
                continue
            for p in g.predecessors(n):
                if p not in seen:
                    seen.add(p)
                    queue.append(p)
        return sorted(n for n in seen if not n.endswith("." + TABLE_COL))

    def upstream_tables(self, fqn: str) -> list[str]:
        g = self.graph()
        node = f"{fqn}.{TABLE_COL}"
        if node not in g:
            return []
        return sorted({a.rsplit(".", 1)[0] for a in nx.ancestors(g, node)})

    def downstream_columns(self, fqn: str, column: str) -> list[str]:
        g = self.graph()
        node = f"{fqn}.{column}"
        if node not in g:
            return []
        return sorted(n for n in nx.descendants(g, node) if not n.endswith("." + TABLE_COL))

    def downstream_tables(self, fqn: str) -> list[str]:
        g = self.graph()
        node = f"{fqn}.{TABLE_COL}"
        if node not in g:
            return []
        return sorted({n.rsplit(".", 1)[0] for n in nx.descendants(g, node)})

    def table_graph(self) -> list[dict[str, str]]:
        rows = self.wh.rows(
            "SELECT DISTINCT src_fqn, dst_fqn FROM meta.lineage_edges WHERE src_column = ? ORDER BY 1, 2",
            [TABLE_COL],
        )
        return [{"src": str(r["src_fqn"]), "dst": str(r["dst_fqn"])} for r in rows]
