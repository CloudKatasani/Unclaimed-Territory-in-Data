"""Deterministic ULID-style identifiers.

IDs must be identical across demo resets (docs/08), so randomness is replaced by a hash of a
persisted per-kind counter. The timestamp part comes from the (frozen) clock.
"""

from __future__ import annotations

import hashlib

from tessera import clock
from tessera.warehouse.base import Warehouse

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _b32(value: int, length: int) -> str:
    out = []
    for _ in range(length):
        out.append(_ALPHABET[value & 31])
        value >>= 5
    return "".join(reversed(out))


def next_seq(wh: Warehouse, name: str) -> int:
    with wh.transaction():
        cur = wh.scalar("SELECT value FROM meta.sequences WHERE name = ?", [name])
        if cur is None:
            wh.execute("INSERT INTO meta.sequences VALUES (?, 1)", [name])
            return 1
        wh.execute("UPDATE meta.sequences SET value = value + 1 WHERE name = ?", [name])
        return int(cur) + 1


def new_id(wh: Warehouse, kind: str) -> str:
    seq = next_seq(wh, kind)
    ms = int(clock.now().timestamp() * 1000)
    rnd = int.from_bytes(hashlib.sha256(f"{kind}:{seq}".encode()).digest()[:10], "big")
    return _b32(ms, 10) + _b32(rnd, 16)
