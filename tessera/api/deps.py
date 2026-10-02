"""Request helpers."""

from __future__ import annotations

from fastapi import Header, HTTPException, Query

from tessera.seed.domain import USERS

USER_IDS = {u["user_id"] for u in USERS}


def current_user(user: str | None = Query(default=None), x_user: str | None = Header(default=None)) -> str:
    uid = user or x_user or "alice"
    if uid not in USER_IDS:
        raise HTTPException(404, f"unknown user {uid}")
    return uid
