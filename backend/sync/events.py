"""Append-only event log helpers. Call ``record`` in the same transaction as the change it describes."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import inspect
from sqlalchemy.orm import Session

from .models import Event, User

_SKIP = {"vec", "token_hash"}


def as_dict(obj: Any) -> dict:
    """Plain-JSON snapshot of an ORM row (without embeddings)."""
    out = {}
    for col in inspect(obj).mapper.column_attrs:
        if col.key in _SKIP:
            continue
        val = getattr(obj, col.key)
        out[col.key] = val.isoformat() if isinstance(val, datetime) else val
    return out


def record(
    db: Session,
    type: str,
    *,
    actor: str,
    entity: str = "",
    entity_id: int | None = None,
    project_id: int | None = None,
    session_id: int | None = None,
    user_id: int | None,
    before: dict | None = None,
    after: dict | None = None,
    reason: str = "",
) -> Event:
    """Append to the history. ``user_id`` is the person behind the change: whoever edited it, or whose call it
    came from (for live and after-call changes). None only for Sync's own housekeeping."""
    user = db.get(User, user_id) if user_id is not None else None
    ev = Event(
        type=type,
        actor=actor,
        user_id=user_id,
        user_name=user.name if user else "",
        entity=entity,
        entity_id=entity_id,
        project_id=project_id,
        session_id=session_id,
        before=before,
        after=after,
        reason=reason,
    )
    db.add(ev)
    return ev


def changed(before: dict, after: dict) -> tuple[dict, dict]:
    """Keep only the fields that changed (keeps the history readable)."""
    keys = [k for k in after if before.get(k) != after.get(k) and k not in ("updated_at",)]
    return {k: before.get(k) for k in keys}, {k: after.get(k) for k in keys}
