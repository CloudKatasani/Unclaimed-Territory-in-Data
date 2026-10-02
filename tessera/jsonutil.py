"""Canonical JSON and hashing helpers."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any


def _default(o: Any) -> Any:
    if isinstance(o, datetime):
        return o.isoformat()
    if isinstance(o, date):
        return o.isoformat()
    if isinstance(o, Decimal):
        return float(o)
    if hasattr(o, "model_dump"):
        return o.model_dump(mode="json")
    if isinstance(o, set | frozenset):
        return sorted(o)
    raise TypeError(f"not JSON serialisable: {type(o)}")


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=_default, ensure_ascii=False)


def dumps(obj: Any) -> str:
    return json.dumps(obj, default=_default)


def loads(text: str | None) -> Any:
    if text is None or text == "":
        return None
    return json.loads(text)


def sha256(text: str | bytes) -> str:
    data = text.encode() if isinstance(text, str) else text
    return hashlib.sha256(data).hexdigest()
