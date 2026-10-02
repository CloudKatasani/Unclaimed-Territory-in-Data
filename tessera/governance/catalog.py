"""Catalog: table/column descriptions, classifications and declared foreign keys."""

from __future__ import annotations

from dataclasses import dataclass

from tessera.warehouse.base import Warehouse


@dataclass(frozen=True)
class CatalogColumn:
    fqn: str
    column: str
    data_type: str
    description: str
    classification: str | None
    searchable: bool


class Catalog:
    def __init__(self, wh: Warehouse) -> None:
        self.wh = wh

    def columns(self, fqn: str | None = None) -> list[CatalogColumn]:
        sql = "SELECT * FROM meta.catalog_columns"
        params: list[str] = []
        if fqn:
            sql += " WHERE fqn = ?"
            params.append(fqn)
        rows = self.wh.rows(sql + " ORDER BY fqn, column_name", params)
        return [
            CatalogColumn(
                str(r["fqn"]),
                str(r["column_name"]),
                str(r["data_type"]),
                str(r["description"] or ""),
                r["classification"],
                bool(r["searchable"]),
            )
            for r in rows
        ]

    def pii_columns(self) -> set[tuple[str, str]]:
        return {(c.fqn, c.column) for c in self.columns() if c.classification == "PII"}

    def foreign_keys(self) -> list[tuple[str, str, str, str]]:
        rows = self.wh.rows("SELECT * FROM meta.foreign_keys ORDER BY src_fqn, src_column")
        return [
            (str(r["src_fqn"]), str(r["src_column"]), str(r["dst_fqn"]), str(r["dst_column"])) for r in rows
        ]

    def register_columns(
        self, fqn: str, columns: list[tuple[str, str, str, str | None]], searchable: bool
    ) -> None:
        self.wh.execute("DELETE FROM meta.catalog_columns WHERE fqn = ?", [fqn])
        for name, dtype, desc, classification in columns:
            self.wh.execute(
                "INSERT INTO meta.catalog_columns VALUES (?, ?, ?, ?, ?, ?)",
                [fqn, name, dtype, desc, classification, searchable],
            )
