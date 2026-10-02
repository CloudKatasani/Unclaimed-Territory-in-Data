"""JSON-safe conversion of warehouse rows."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from tessera.jsonutil import loads


def row(r: dict[str, Any], json_fields: tuple[str, ...] = ()) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in r.items():
        if k in json_fields and isinstance(v, str):
            v = loads(v)
        if isinstance(v, datetime):
            v = (v.replace(tzinfo=UTC) if v.tzinfo is None else v).isoformat().replace("+00:00", "Z")
        elif isinstance(v, date):
            v = v.isoformat()
        out[k] = v
    return out


def rows(rs: list[dict[str, Any]], json_fields: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    return [row(r, json_fields) for r in rs]
