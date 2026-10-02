"""Optional Snowflake adapter (M7). Not required for tests; imported lazily."""

from __future__ import annotations

import os
from typing import Any

import pyarrow as pa

from tessera.warehouse.base import Column


class SnowflakeWarehouse:  # pragma: no cover - requires credentials
    """Snowflake implementation using zero-copy CLONE for shadow schemas."""

    def __init__(self) -> None:
        try:
            import snowflake.connector
        except ImportError as exc:
            raise RuntimeError("pip install tessera[snowflake] to use WAREHOUSE=snowflake") from exc
        self.con = snowflake.connector.connect(
            account=os.environ["SNOWFLAKE_ACCOUNT"],
            user=os.environ["SNOWFLAKE_USER"],
            password=os.environ.get("SNOWFLAKE_PASSWORD"),
            warehouse=os.environ.get("SNOWFLAKE_WAREHOUSE"),
            database=os.environ.get("SNOWFLAKE_DATABASE", "TESSERA"),
        )

    def query(self, sql: str, params: dict[str, Any] | list[Any] | None = None) -> pa.Table:
        cur = self.con.cursor()
        cur.execute(sql, params)
        return cur.fetch_arrow_all()

    def rows(self, sql: str, params: dict[str, Any] | list[Any] | None = None) -> list[dict[str, Any]]:
        return list(self.query(sql, params).to_pylist())

    def scalar(self, sql: str, params: dict[str, Any] | list[Any] | None = None) -> Any:
        r = self.rows(sql, params)
        return next(iter(r[0].values())) if r else None

    def execute(self, sql: str, params: dict[str, Any] | list[Any] | None = None) -> None:
        self.con.cursor().execute(sql, params)

    def load_rows(self, fqn: str, columns: list[str], rows: list[tuple[Any, ...]]) -> None:
        cols = ", ".join(columns)
        marks = ", ".join(["%s"] * len(columns))
        self.con.cursor().executemany(f"INSERT INTO {fqn} ({cols}) VALUES ({marks})", rows)

    def clone_schema(self, src: str, dst: str) -> None:
        self.execute(f"CREATE OR REPLACE SCHEMA {dst} CLONE {src}")

    def clone_table(self, src_fqn: str, dst_fqn: str, where: str | None = None) -> None:
        if where:
            self.execute(f"CREATE OR REPLACE TABLE {dst_fqn} AS SELECT * FROM {src_fqn} WHERE {where}")
        else:
            self.execute(f"CREATE OR REPLACE TABLE {dst_fqn} CLONE {src_fqn}")

    def table_schema(self, fqn: str) -> list[Column]:
        rows = self.rows(f"DESCRIBE TABLE {fqn}")
        return [Column(r["name"].lower(), r["type"], r["null?"] == "Y") for r in rows]

    def table_exists(self, fqn: str) -> bool:
        schema, _, name = fqn.partition(".")
        n = self.scalar(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema = %s AND table_name = %s",
            [schema.upper(), name.upper()],
        )
        return bool(n)

    def list_tables(self, schema: str) -> list[str]:
        rows = self.rows(f"SHOW TABLES IN SCHEMA {schema}")
        return [r["name"].lower() for r in rows]

    def drop_schema(self, name: str) -> None:
        self.execute(f"DROP SCHEMA IF EXISTS {name} CASCADE")

    def transaction(self) -> Any:
        from contextlib import nullcontext

        return nullcontext()

    def close(self) -> None:
        self.con.close()
