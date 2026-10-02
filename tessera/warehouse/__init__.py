"""Warehouse factory."""

from __future__ import annotations

import os
from pathlib import Path

from tessera.warehouse.base import Column, Warehouse
from tessera.warehouse.duckdb_wh import DuckDBWarehouse

__all__ = ["Column", "Warehouse", "DuckDBWarehouse", "open_warehouse"]


def open_warehouse(path: Path | str) -> Warehouse:
    if os.environ.get("WAREHOUSE", "duckdb") == "snowflake":  # pragma: no cover
        from tessera.warehouse.snowflake_wh import SnowflakeWarehouse

        return SnowflakeWarehouse()
    return DuckDBWarehouse(path)
