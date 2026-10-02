"""DuckDB warehouse (default)."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa

from tessera.warehouse.base import Column


def _split(fqn: str) -> tuple[str, str]:
    schema, _, name = fqn.partition(".")
    if not name:
        return "main", schema
    return schema, name


class DuckDBWarehouse:
    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.con = duckdb.connect(self.path)
        self.con.execute("SET TimeZone = 'UTC'")
        self.lock = threading.RLock()
        self._tx_depth = 0

    # -- queries -----------------------------------------------------------------------------
    def query(self, sql: str, params: dict[str, Any] | list[Any] | None = None) -> pa.Table:
        with self.lock:
            return self.con.execute(sql, params).fetch_arrow_table()

    def rows(self, sql: str, params: dict[str, Any] | list[Any] | None = None) -> list[dict[str, Any]]:
        with self.lock:
            cur = self.con.execute(sql, params)
            cols = [d[0] for d in cur.description or []]
            return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]

    def scalar(self, sql: str, params: dict[str, Any] | list[Any] | None = None) -> Any:
        with self.lock:
            row = self.con.execute(sql, params).fetchone()
            return None if row is None else row[0]

    def execute(self, sql: str, params: dict[str, Any] | list[Any] | None = None) -> None:
        with self.lock:
            self.con.execute(sql, params)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        with self.lock:
            if self._tx_depth:
                self._tx_depth += 1
                try:
                    yield
                finally:
                    self._tx_depth -= 1
                return
            self.con.execute("BEGIN TRANSACTION")
            self._tx_depth = 1
            try:
                yield
            except BaseException:
                self.con.execute("ROLLBACK")
                raise
            else:
                self.con.execute("COMMIT")
            finally:
                self._tx_depth = 0

    def load_rows(self, fqn: str, columns: list[str], rows: list[tuple[Any, ...]]) -> None:
        """Bulk-insert rows into an existing table via Arrow (much faster than executemany)."""
        data = {c: [r[i] for r in rows] for i, c in enumerate(columns)}
        tbl = pa.table(data)
        with self.lock:
            self.con.register("_tessera_load", tbl)
            try:
                self.con.execute(
                    f"INSERT INTO {fqn} ({', '.join(columns)}) SELECT {', '.join(columns)} FROM _tessera_load"
                )
            finally:
                self.con.unregister("_tessera_load")

    # -- schemas -----------------------------------------------------------------------------
    def list_tables(self, schema: str) -> list[str]:
        rows = self.rows(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = ? ORDER BY table_name",
            [schema],
        )
        return [str(r["table_name"]) for r in rows]

    def table_exists(self, fqn: str) -> bool:
        schema, name = _split(fqn)
        n = self.scalar(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema = ? AND table_name = ?",
            [schema, name],
        )
        return bool(n)

    def table_schema(self, fqn: str) -> list[Column]:
        schema, name = _split(fqn)
        rows = self.rows(
            "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
            "WHERE table_schema = ? AND table_name = ? ORDER BY ordinal_position",
            [schema, name],
        )
        return [Column(str(r["column_name"]), str(r["data_type"]), r["is_nullable"] == "YES") for r in rows]

    def clone_schema(self, src: str, dst: str) -> None:
        """DuckDB has no zero-copy clone, so this is a CTAS copy of every table in the schema."""
        with self.lock:
            self.execute(f"CREATE SCHEMA IF NOT EXISTS {dst}")
            for t in self.list_tables(src):
                self.execute(f"CREATE OR REPLACE TABLE {dst}.{t} AS SELECT * FROM {src}.{t}")

    def clone_table(self, src_fqn: str, dst_fqn: str, where: str | None = None) -> None:
        schema, _ = _split(dst_fqn)
        with self.lock:
            self.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
            clause = f" WHERE {where}" if where else ""
            self.execute(f"CREATE OR REPLACE TABLE {dst_fqn} AS SELECT * FROM {src_fqn}{clause}")

    def drop_schema(self, name: str) -> None:
        self.execute(f"DROP SCHEMA IF EXISTS {name} CASCADE")

    def close(self) -> None:
        with self.lock:
            self.con.close()
