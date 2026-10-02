"""Tests derived deterministically from a contract (the A1 mechanism)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tessera.governance.contracts import Contract
from tessera.warehouse.base import Warehouse

TYPE_FAMILIES: dict[str, set[str]] = {
    "varchar": {"VARCHAR"},
    "integer": {"INTEGER", "BIGINT", "SMALLINT", "TINYINT", "HUGEINT", "UBIGINT", "UINTEGER"},
    "double": {"DOUBLE", "FLOAT", "REAL"},
    "boolean": {"BOOLEAN"},
    "date": {"DATE"},
    "timestamp": {"TIMESTAMP", "TIMESTAMP WITH TIME ZONE", "TIMESTAMP_NS", "TIMESTAMP_MS"},
}


def type_matches(contract_type: str, actual: str) -> bool:
    fam = TYPE_FAMILIES.get(contract_type.lower())
    if fam is None:
        return contract_type.upper() == actual.upper()
    return actual.upper() in fam


@dataclass
class TestResult:
    name: str
    kind: str
    passed: bool
    detail: str = ""
    violations: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "passed": self.passed,
            "detail": self.detail,
            "violations": self.violations,
        }


def derive_test_names(contract: Contract) -> list[tuple[str, str]]:
    """(name, kind) for every test derived from the contract, before running anything."""
    out: list[tuple[str, str]] = [("schema.columns", "schema"), ("schema.types", "schema")]
    out += [(f"not_null.{c.name}", "schema") for c in contract.columns if not c.nullable]
    out.append((f"unique.{'+'.join(contract.primary_key)}", "key"))
    out += [(f"expect.{e.name}", "expectation") for e in contract.quality.expectations]
    out += [(f"fk.{fk.column}", "referential") for fk in contract.foreign_keys]
    return out


def run_contract_tests(
    wh: Warehouse,
    contract: Contract,
    table_fqn: str,
    *,
    prefix: str = "",
    include_fk: bool = True,
    ref_map: dict[str, str] | None = None,
) -> list[TestResult]:
    results: list[TestResult] = []
    actual = {c.name: c.data_type for c in wh.table_schema(table_fqn)}
    expected = [c.name for c in contract.columns]
    missing = [c for c in expected if c not in actual]
    extra = [c for c in actual if c not in expected]
    results.append(
        TestResult(
            f"{prefix}schema.columns",
            "schema",
            not missing and not extra,
            f"missing={missing} extra={extra}" if missing or extra else "ok",
        )
    )
    wrong = [
        f"{c.name}: expected {c.type}, got {actual[c.name]}"
        for c in contract.columns
        if c.name in actual and not type_matches(c.type, actual[c.name])
    ]
    results.append(TestResult(f"{prefix}schema.types", "schema", not wrong, "; ".join(wrong) or "ok"))

    def count(sql: str) -> int:
        return int(wh.scalar(sql) or 0)

    for c in contract.columns:
        if not c.nullable and c.name in actual:
            n = count(f"SELECT count(*) FROM {table_fqn} WHERE {c.name} IS NULL")
            results.append(TestResult(f"{prefix}not_null.{c.name}", "schema", n == 0, f"{n} nulls", n))
    pk = contract.primary_key
    if all(k in actual for k in pk):
        cols = ", ".join(pk)
        n = count(f"SELECT count(*) - count(DISTINCT ({cols})) FROM {table_fqn}")
        results.append(TestResult(f"{prefix}unique.{'+'.join(pk)}", "key", n == 0, f"{n} duplicate keys", n))
    for e in contract.quality.expectations:
        try:
            n = count(f"SELECT count(*) FROM {table_fqn} WHERE NOT ({e.sql}) OR ({e.sql}) IS NULL")
            results.append(
                TestResult(f"{prefix}expect.{e.name}", "expectation", n == 0, f"{n} rows violate", n)
            )
        except Exception as exc:  # noqa: BLE001 - surface any SQL error as a failing test
            results.append(TestResult(f"{prefix}expect.{e.name}", "expectation", False, f"error: {exc}"))
    if include_fk:
        for fk in contract.foreign_keys:
            parent, _, pcol = fk.references.rpartition(".")
            parent = (ref_map or {}).get(parent, parent)
            try:
                n = count(
                    f"SELECT count(*) FROM {table_fqn} AS c WHERE c.{fk.column} IS NOT NULL AND NOT EXISTS "
                    f"(SELECT 1 FROM {parent} AS p WHERE p.{pcol} = c.{fk.column})"
                )
                results.append(
                    TestResult(f"{prefix}fk.{fk.column}", "referential", n == 0, f"{n} orphans", n)
                )
            except Exception as exc:  # noqa: BLE001
                results.append(TestResult(f"{prefix}fk.{fk.column}", "referential", False, f"error: {exc}"))
    return results


def quality_score(results: list[TestResult]) -> float:
    if not results:
        return 1.0
    return round(sum(1 for r in results if r.passed) / len(results), 4)
