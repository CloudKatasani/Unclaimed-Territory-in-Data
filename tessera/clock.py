"""Clock. The demo runs on a frozen clock (DEMO_NOW) so every run is reproducible."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tessera.config import get_settings

_offset = timedelta(0)


def demo_now() -> datetime:
    return datetime.fromisoformat(get_settings().demo_now).astimezone(UTC)


def now() -> datetime:
    """Current UTC time (naive-free, tz-aware)."""
    if get_settings().frozen_clock:
        return demo_now() + _offset
    return datetime.now(UTC)


def advance(delta: timedelta) -> None:
    """Move the frozen clock forward (used to order events within a demo run)."""
    global _offset
    _offset += delta


def reset() -> None:
    global _offset
    _offset = timedelta(0)


def naive_utc(dt: datetime) -> datetime:
    """Timestamps are stored in DuckDB as naive UTC."""
    return dt.astimezone(UTC).replace(tzinfo=None)


def iso(dt: datetime) -> str:
    return naive_utc(dt).isoformat(timespec="seconds") + "Z"
