"""Who is asking. Every browser and desktop agent signs in with a token, a random key of which only a hash is
stored. Tokens travel in the ``X-Sync-Token`` header (or ``?token=`` for WebSockets)."""
from __future__ import annotations

import hashlib
import json
import secrets

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .db import get_db, session_scope
from .models import ApiToken, User


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue_token(db: Session, user_id: int, label: str = "browser") -> str:
    token = secrets.token_urlsafe(24)
    db.add(ApiToken(user_id=user_id, token_hash=_hash(token), label=label))
    db.flush()
    return token


def user_for_token(db: Session, token: str | None) -> User | None:
    if not token:
        return None
    row = db.scalar(select(ApiToken).where(ApiToken.token_hash == _hash(token)))
    return db.get(User, row.user_id) if row else None


def request_token(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.headers.get("x-sync-token") or request.query_params.get("token")


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """FastAPI dependency: the signed-in user (401 otherwise)."""
    user = user_for_token(db, request_token(request))
    if user is None:
        raise HTTPException(401, "Not signed in to Sync")
    return user


def optional_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    return user_for_token(db, request_token(request))


def ws_user_id(token: str | None) -> int | None:
    with session_scope() as db:
        user = user_for_token(db, token)
        return user.id if user else None


# ---------------------------------------------------------------- the desktop agent's sign-in on this laptop


def agent_config_path():
    return settings.data_dir / "agent.json"


def load_agent_config() -> dict:
    """{"server": ..., "token": ...} saved for the desktop agent (and the demo player) on this laptop."""
    try:
        return json.loads(agent_config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_agent_config(server: str, token: str) -> None:
    path = agent_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"server": server, "token": token}, indent=2), encoding="utf-8")
