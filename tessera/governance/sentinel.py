"""Sentinel records (idea 3): synthetic tracer rows that verify every transformation, continuously.

* ``generate`` injects the sector's sentinel rows (reserved ``SNTL-`` keys) into the sources.
* ``expect`` evaluates each product's model SQL over the sentinel rows only, in isolation, to get the
  rows the product must contain for those keys (the symbolic step).
* ``verify`` compares a product (published or shadow) with its expectations.
The Policy Engine excludes sentinel keys from every served query.
"""

from __future__ import annotations

import math
from datetime import UTC
from pathlib import Path
from typing import Any

import yaml

from tessera import clock, ids
from tessera.governance import contracts as contracts_mod
from tessera.governance.policy import SENTINEL_PREFIX
from tessera.jsonutil import canonical, dumps, loads
from tessera.publisher.promote import rewrite_tables
from tessera.warehouse.base import Warehouse

DEFINITIONS = Path(__file__).resolve().parents[1] / "seed" / "sentinels.yaml"
RAW_SOURCES = ["raw.feeders", "raw.meters", "raw.outage_events", "raw.head_end_vendor_feed"]


def _norm(v: Any) -> Any:
    if isinstance(v, float):
        return None if math.isnan(v) else round(v, 9)
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return v


def _row(r: dict[str, Any]) -> dict[str, Any]:
    return {k: _norm(v) for k, v in r.items()}


class Sentinels:
    def __init__(self, wh: Warehouse) -> None:
        self.wh = wh

    # -- generate ---------------------------------------------------------------------------------------
    def generate(self) -> int:
        spec = yaml.safe_load(DEFINITIONS.read_text())
        n = 0
        for section in ("reference", "records"):
            for fqn, rows in spec[section].items():
                cols = [c.name for c in self.wh.table_schema(fqn)]
                for r in rows:
                    vals = [r.get(c) for c in cols]
                    self.wh.execute(f"INSERT INTO {fqn} VALUES ({', '.join('?' * len(cols))})", vals)
                    if section == "records":
                        key_col = next(c for c in cols if str(r.get(c, "")).startswith(SENTINEL_PREFIX))
                        self.wh.execute(
                            "INSERT INTO meta.sentinel_records VALUES (?, ?, ?, ?, ?)",
                            [f"SNTL-{n + 1:02d}", fqn, key_col, str(r[key_col]), dumps(r)],
                        )
                        n += 1
        return n

    def records(self) -> list[dict[str, Any]]:
        out = []
        for r in self.wh.rows("SELECT * FROM meta.sentinel_records ORDER BY record_id"):
            out.append({**r, "row_json": loads(r["row_json"])})
        return out

    # -- keys used for exclusion ---------------------------------------------------------------------------
    def keys(self) -> dict[str, str]:
        return {str(r["fqn"]): str(r["key_column"]) for r in self.wh.rows("SELECT * FROM meta.sentinel_keys")}

    def register_key(self, fqn: str) -> str | None:
        found = contracts_mod.latest_for(self.wh, fqn)
        pk = found[0].primary_key if found else []
        cols = {c.name: c.data_type for c in self.wh.table_schema(fqn)}
        for c in [*pk, *cols]:
            if cols.get(c) == "VARCHAR" and self.wh.scalar(
                f"SELECT count(*) FROM {fqn} WHERE {c} LIKE '{SENTINEL_PREFIX}%'"
            ):
                self.wh.execute("DELETE FROM meta.sentinel_keys WHERE fqn = ?", [fqn])
                self.wh.execute("INSERT INTO meta.sentinel_keys VALUES (?, ?)", [fqn, c])
                return c
        return None

    # -- expect --------------------------------------------------------------------------------------------
    def isolated_schema(self, schema: str) -> dict[str, str]:
        """Copy only sentinel rows of every raw source into `schema`; build the ingestion mapping."""
        self.wh.drop_schema(schema)
        self.wh.execute(f"CREATE SCHEMA {schema}")
        mapping: dict[str, str] = {}
        for fqn in RAW_SOURCES:
            cols = [c.name for c in self.wh.table_schema(fqn) if c.data_type == "VARCHAR"]
            where = " OR ".join(f"{c} LIKE '{SENTINEL_PREFIX}%'" for c in cols)
            name = fqn.split(".", 1)[1]
            self.wh.clone_table(fqn, f"{schema}.{name}", where=where)
            mapping[fqn] = f"{schema}.{name}"
        for m in self.wh.rows("SELECT fqn, select_sql FROM meta.models WHERE kind = 'view' ORDER BY fqn"):
            name = str(m["fqn"]).split(".", 1)[1]
            self.wh.execute(
                f"CREATE TABLE {schema}.{name} AS {rewrite_tables(str(m['select_sql']), mapping)}"
            )
            mapping[str(m["fqn"])] = f"{schema}.{name}"
        return mapping

    def expect(self, products: list[str] | None = None) -> dict[str, int]:
        schema = "sntl_expect"
        mapping = self.isolated_schema(schema)
        out: dict[str, int] = {}
        try:
            models = self.wh.rows("SELECT fqn, select_sql FROM meta.models WHERE kind = 'table' ORDER BY fqn")
            for m in models:
                fqn = str(m["fqn"])
                if products is not None and fqn not in products:
                    continue
                key = self.register_key(fqn)
                if key is None:
                    continue
                pk = self._pk(fqn)
                rows = self.wh.rows(
                    f"SELECT * FROM ({rewrite_tables(str(m['select_sql']), mapping)}) AS e "
                    f"WHERE {key} LIKE '{SENTINEL_PREFIX}%' ORDER BY {', '.join(pk)}"
                )
                self.wh.execute("DELETE FROM meta.sentinel_expectations WHERE fqn = ?", [fqn])
                now = clock.naive_utc(clock.now())
                for r in rows:
                    nr = _row(r)
                    self.wh.execute(
                        "INSERT INTO meta.sentinel_expectations VALUES (?, ?, ?, ?)",
                        [fqn, canonical({k: nr[k] for k in pk}), canonical(nr), now],
                    )
                out[fqn] = len(rows)
        finally:
            self.wh.drop_schema(schema)
        return out

    def _pk(self, fqn: str) -> list[str]:
        found = contracts_mod.latest_for(self.wh, fqn)
        if found:
            return found[0].primary_key
        return [c.name for c in self.wh.table_schema(fqn)][:1]

    # -- verify --------------------------------------------------------------------------------------------
    def verify(
        self, fqn: str, table: str | None = None, context: str = "published", record: bool = True
    ) -> dict[str, Any]:
        table = table or fqn
        key = self.keys().get(fqn)
        expected = {
            r["key_json"]: loads(r["row_json"])
            for r in self.wh.rows("SELECT * FROM meta.sentinel_expectations WHERE fqn = ?", [fqn])
        }
        if key is None or not expected:
            return {"fqn": fqn, "passed": True, "checked": 0, "mismatches": [], "skipped": True}
        pk = self._pk(fqn)
        actual = {
            canonical({k: _norm(r[k]) for k in pk}): _row(r)
            for r in self.wh.rows(f"SELECT * FROM {table} WHERE {key} LIKE '{SENTINEL_PREFIX}%'")
        }
        mismatches = []
        for k, exp_row in sorted(expected.items()):
            act = actual.get(k)
            if act is None:
                mismatches.append({"key": loads(k), "column": None, "expected": "row", "actual": "missing"})
                continue
            for col, ev in exp_row.items():
                av = act.get(col)
                same = abs(av - ev) <= 1e-9 if isinstance(ev, float) and isinstance(av, float) else av == ev
                if not same:
                    mismatches.append({"key": loads(k), "column": col, "expected": ev, "actual": av})
        result = {
            "fqn": fqn,
            "passed": not mismatches,
            "checked": len(expected),
            "mismatches": mismatches[:50],
        }
        if record:
            self.wh.execute(
                "INSERT INTO meta.sentinel_results VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    ids.new_id(self.wh, "sentinel"),
                    fqn,
                    context,
                    result["passed"],
                    len(expected),
                    dumps(result["mismatches"]),
                    clock.naive_utc(clock.now()),
                ],
            )
        return result

    def verify_all(self) -> dict[str, dict[str, Any]]:
        return {fqn: self.verify(fqn) for fqn in sorted(self.keys())}

    def check_shadow(self, schema: str, products: list[str]) -> dict[str, dict[str, Any]]:
        """Used by the drift gate: sentinel verification of shadow outputs, before divergence."""
        return {
            p: self.verify(p, f"{schema}.{p.split('.', 1)[1]}", context=f"shadow:{schema}") for p in products
        }

    def status(self) -> dict[str, dict[str, Any]]:
        out = {}
        for fqn in sorted(self.keys()):
            r = self.wh.rows(
                "SELECT * FROM meta.sentinel_results WHERE fqn = ? AND context = 'published' "
                "ORDER BY checked_at DESC, result_id DESC LIMIT 1",
                [fqn],
            )
            if r:
                out[fqn] = {
                    "passed": bool(r[0]["passed"]),
                    "checked": int(r[0]["checked"]),
                    "checked_at": clock.iso(r[0]["checked_at"].replace(tzinfo=UTC)),
                }
        return out

    def matrix(self) -> dict[str, Any]:
        """Pipeline-truth matrix: sentinel records x products, with expected vs actual per cell."""
        products = sorted(self.keys())
        recs = self.records()
        meter_feeder = {
            str(r["meter_id"]): str(r["feeder_id"])
            for r in self.wh.rows("SELECT meter_id, feeder_id FROM raw.meters WHERE meter_id LIKE 'SNTL-%'")
        }
        rows = []
        for rec in recs:
            cells: dict[str, dict[str, Any] | None] = {}
            for p in products:
                key = self.keys()[p]
                exp = [
                    loads(r["row_json"])
                    for r in self.wh.rows(
                        "SELECT row_json FROM meta.sentinel_expectations WHERE fqn = ?", [p]
                    )
                ]
                val = str(rec["key_value"])
                link = {val, meter_feeder.get(val, ""), str(rec["row_json"].get("feeder_id", ""))}
                related = [e for e in exp if str(e.get(key)) in link or str(e.get("outage_id")) == val]
                if not related:
                    cells[p] = None
                    continue
                act_rows = {
                    canonical({k: _norm(r[k]) for k in self._pk(p)}): _row(r)
                    for r in self.wh.rows(f"SELECT * FROM {p} WHERE {key} LIKE '{SENTINEL_PREFIX}%'")
                }
                ok = True
                detail = []
                for e in related:
                    k = canonical({c: e[c] for c in self._pk(p)})
                    a = act_rows.get(k)
                    match = a is not None and all(
                        (
                            abs(a[c] - v) <= 1e-9
                            if isinstance(v, float) and isinstance(a.get(c), float)
                            else a.get(c) == v
                        )
                        for c, v in e.items()
                    )
                    ok = ok and match
                    detail.append({"expected": e, "actual": a})
                cells[p] = {"passed": ok, "rows": detail[:4]}
            rows.append(
                {
                    "record_id": rec["record_id"],
                    "source": rec["source_fqn"],
                    "key": rec["key_value"],
                    "row": rec["row_json"],
                    "cells": cells,
                }
            )
        return {"products": products, "records": rows}
