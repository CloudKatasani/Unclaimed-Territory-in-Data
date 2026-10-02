"""Ed25519 signing for certificates and provenance. Keys live in meta.keys."""

from __future__ import annotations

import hashlib
from functools import lru_cache

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from tessera import clock
from tessera.warehouse.base import Warehouse

KEY_ID = "tessera-signing-1"


def generate_key(wh: Warehouse, seed: str) -> str:
    """Create the signing key. Derived from a seed so demo certificates are reproducible."""
    raw = hashlib.sha256(seed.encode()).digest()
    priv = Ed25519PrivateKey.from_private_bytes(raw)
    pub = priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    wh.execute("DELETE FROM meta.keys WHERE key_id = ?", [KEY_ID])
    wh.execute(
        "INSERT INTO meta.keys VALUES (?, ?, ?, ?)",
        [KEY_ID, pub.hex(), raw.hex(), clock.naive_utc(clock.now())],
    )
    return pub.hex()


@lru_cache(maxsize=8)
def _private(hex_key: str) -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(bytes.fromhex(hex_key))


class Signer:
    def __init__(self, wh: Warehouse) -> None:
        row = wh.rows("SELECT public_key_hex, private_key_hex FROM meta.keys WHERE key_id = ?", [KEY_ID])
        if not row:
            raise RuntimeError("signing key missing; run `make setup`")
        self.public_key_hex = str(row[0]["public_key_hex"])
        self._priv = _private(str(row[0]["private_key_hex"]))

    def sign(self, payload: str) -> str:
        return self._priv.sign(payload.encode()).hex()

    def verify(self, payload: str, signature: str) -> bool:
        return verify_with(self.public_key_hex, payload, signature)


def verify_with(public_key_hex: str, payload: str, signature: str) -> bool:
    pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex))
    try:
        pub.verify(bytes.fromhex(signature), payload.encode())
    except (InvalidSignature, ValueError):
        return False
    return True
