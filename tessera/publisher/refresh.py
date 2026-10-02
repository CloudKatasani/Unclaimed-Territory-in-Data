"""Refreshing published products from their registered model SQL."""

from __future__ import annotations

from typing import Any

from tessera import clock
from tessera.governance import contracts as contracts_mod
from tessera.governance.contract_tests import TestResult, quality_score, run_contract_tests
from tessera.jsonutil import dumps
from tessera.publisher.promote import materialize
from tessera.warehouse.base import Warehouse


def model(wh: Warehouse, fqn: str) -> dict[str, Any]:
    rows = wh.rows("SELECT * FROM meta.models WHERE fqn = ?", [fqn])
    if not rows:
        raise KeyError(f"no model registered for {fqn}")
    return rows[0]


def register_model(wh: Warehouse, fqn: str, kind: str, select_sql: str, artifact_id: str | None) -> None:
    cur = wh.scalar("SELECT version FROM meta.models WHERE fqn = ?", [fqn])
    wh.execute("DELETE FROM meta.models WHERE fqn = ?", [fqn])
    wh.execute(
        "INSERT INTO meta.models VALUES (?, ?, ?, ?, ?, ?)",
        [fqn, kind, select_sql, (cur or 0) + 1, artifact_id, clock.naive_utc(clock.now())],
    )


def run_product_tests(wh: Warehouse, fqn: str) -> list[TestResult]:
    found = contracts_mod.latest_for(wh, fqn)
    if not found:
        return []
    contract, _ = found
    return run_contract_tests(wh, contract, fqn)


def record_quality(wh: Warehouse, fqn: str, results: list[TestResult], refreshed: bool = True) -> None:
    failing = [r.name for r in results if not r.passed]
    if refreshed:
        wh.execute(
            "UPDATE meta.products SET last_refreshed_at = ?, quality_score = ?, failing_tests = ?, "
            "refresh_status = 'ok', held_reason = NULL WHERE fqn = ?",
            [clock.naive_utc(clock.now()), quality_score(results), dumps(failing), fqn],
        )
    else:
        wh.execute(
            "UPDATE meta.products SET quality_score = ?, failing_tests = ? WHERE fqn = ?",
            [quality_score(results), dumps(failing), fqn],
        )


def refresh_product(wh: Warehouse, fqn: str) -> list[TestResult]:
    m = model(wh, fqn)
    materialize(wh, fqn, str(m["select_sql"]), str(m["kind"]))
    results = run_product_tests(wh, fqn)
    record_quality(wh, fqn, results)
    return results


def hold_product(wh: Warehouse, fqn: str, reason: str) -> None:
    wh.execute(
        "UPDATE meta.products SET refresh_status = 'held', held_reason = ? WHERE fqn = ?", [reason, fqn]
    )
