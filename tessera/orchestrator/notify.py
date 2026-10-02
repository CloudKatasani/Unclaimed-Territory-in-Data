"""User notifications and disclosure metrics."""

from __future__ import annotations

from typing import Any

from tessera import clock, ids
from tessera.jsonutil import dumps
from tessera.warehouse.base import Warehouse


def notify(wh: Warehouse, user_id: str, kind: str, message: str, ref: str | None = None) -> str:
    nid = ids.new_id(wh, "notification")
    wh.execute(
        "INSERT INTO meta.notifications VALUES (?, ?, ?, ?, ?, ?, NULL)",
        [nid, user_id, kind, message, ref, clock.naive_utc(clock.now())],
    )
    return nid


def record_metric(wh: Warehouse, name: str, value: float, context: dict[str, Any] | None = None) -> None:
    """Measures captured for the invention disclosures (docs/05)."""
    wh.execute(
        "INSERT INTO meta.metrics VALUES (?, ?, ?, ?, ?)",
        [ids.new_id(wh, "metric"), name, float(value), dumps(context or {}), clock.naive_utc(clock.now())],
    )
