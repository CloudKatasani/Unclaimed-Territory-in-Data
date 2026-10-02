"""In-process event bus. Events are logged to meta.events and dispatched deterministically.

Handlers run when `drain()` is called (the API drains after every request), which keeps the demo
reproducible while still decoupling emitters from consumers.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from tessera import clock, ids
from tessera.jsonutil import dumps
from tessera.warehouse.base import Warehouse

Handler = Callable[[dict[str, Any]], None]


@dataclass
class Event:
    event_id: str
    kind: str
    payload: dict[str, Any] = field(default_factory=dict)


class Bus:
    def __init__(self, wh: Warehouse) -> None:
        self.wh = wh
        self.handlers: dict[str, list[Handler]] = {}
        self.queue: deque[Event] = deque()
        self._draining = False

    def subscribe(self, kind: str, handler: Handler) -> None:
        self.handlers.setdefault(kind, []).append(handler)

    def emit(self, kind: str, payload: dict[str, Any] | None = None) -> str:
        payload = payload or {}
        eid = ids.new_id(self.wh, "event")
        self.wh.execute(
            "INSERT INTO meta.events VALUES (?, ?, ?, ?)",
            [eid, kind, dumps(payload), clock.naive_utc(clock.now())],
        )
        self.queue.append(Event(eid, kind, payload))
        return eid

    def drain(self) -> int:
        if self._draining:
            return 0
        self._draining = True
        n = 0
        try:
            while self.queue:
                ev = self.queue.popleft()
                for h in self.handlers.get(ev.kind, []):
                    h(ev.payload)
                n += 1
        finally:
            self._draining = False
        return n

    def history(self, kind: str | None = None) -> list[dict[str, Any]]:
        if kind:
            return self.wh.rows(
                "SELECT * FROM meta.events WHERE kind = ? ORDER BY emitted_at, event_id", [kind]
            )
        return self.wh.rows("SELECT * FROM meta.events ORDER BY emitted_at, event_id")
