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


def resume_after(latest: datetime | None) -> None:
    """Frozen-clock processes resume after the latest stored event so ordering is preserved."""
    global _offset
    if latest is None:
        return
    latest = latest.replace(tzinfo=UTC) if latest.tzinfo is None else latest
    base = demo_now()
    if latest + timedelta(milliseconds=1) > base + _offset:
        _offset = latest - base + timedelta(milliseconds=1)


class at:  # noqa: N801 - used as a context manager: `with clock.at(dt): ...`
    """Temporarily pin the clock (used to seed history, e.g. an export served on 28 Sep)."""

    def __init__(self, when: datetime) -> None:
        self.when = when.replace(tzinfo=UTC) if when.tzinfo is None else when

    def __enter__(self) -> None:
        global _offset
        self._saved = _offset
        _offset = self.when - demo_now()

    def __exit__(self, *exc: object) -> None:
        global _offset
        _offset = self._saved
