"""People using Sync: first-run setup, joining a shared project by invite link, profile, desktop-agent keys,
personal settings (auto-listen, what Sync learned), search and health."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import access, autolisten, bus
from ..agent.search import search
from ..auth import current_user, issue_token, load_agent_config, optional_user, save_agent_config, user_for_token
from ..config import settings
from ..db import get_db
from ..events import as_dict, changed, record
from ..llm import llm
from ..models import Event, GapStat, PlaybookEntry, Project, User

router = APIRouter(tags=["me"])
LOCAL = {"127.0.0.1", "::1", "localhost"}


def _profile(user: User) -> dict:
    out = as_dict(user)
    out.pop("autolisten", None)
    return out


@router.get("/health")
async def health(user: User | None = Depends(optional_user)):
    out = {"ok": True, "llm_enabled": settings.llm_enabled, "live_model": settings.live_model,
           "deep_model": settings.deep_model, "chat_model": settings.chat_model, "live_sessions": llm.live_sessions,
           "desktop_agent": bus.agents.get(user.id, 0) > 0 if user else None}
    try:
        models = await llm.models()
        out["ollama"] = "ok"
        out["models_ready"] = {m: m in models for m in {settings.live_model, settings.deep_model, settings.chat_model}}
    except Exception as e:  # noqa: BLE001
        out["ollama"] = f"unreachable: {e}"
    try:
        from ..stt.engine import available

        out["stt"] = available()
    except Exception as e:  # noqa: BLE001
        out["stt"] = f"unavailable: {e}"
    return out


# ---------------------------------------------------------------- getting in


@router.get("/setup")
def setup(db: Session = Depends(get_db)):
    """Has anyone set Sync up yet? (The first visit creates the owner's profile.)"""
    return {"has_users": (db.scalar(select(func.count()).select_from(User)) or 0) > 0}


class NewUserIn(BaseModel):
    name: str


@router.post("/users")
def create_first_user(body: NewUserIn, request: Request, db: Session = Depends(get_db)):
    """First run only: the person who installed Sync creates their profile. Everyone else joins with an invite link."""
    if (db.scalar(select(func.count()).select_from(User)) or 0) > 0:
        raise HTTPException(403, "Sync is already set up here. Ask a teammate for an invite link to join a project.")
    if not body.name.strip():
        raise HTTPException(400, "name is required")
    user = User(name=body.name.strip())
    db.add(user)
    db.flush()
    record(db, "user.created", actor="user", entity="user", entity_id=user.id, user_id=user.id,
           after={"name": user.name})
    token = issue_token(db, user.id, "browser")
    if request.client and request.client.host in LOCAL:  # the desktop agent on this laptop signs in by itself
        save_agent_config(f"http://127.0.0.1:{settings.port}", issue_token(db, user.id, "desktop agent"))
    return {"user": _profile(user), "token": token}


@router.post("/signin/this-computer")
def signin_this_computer(request: Request, db: Session = Depends(get_db)):
    """A new browser on the laptop where Sync runs: sign in as that laptop's owner (whose desktop-agent key is
    saved here). Only allowed from the laptop itself."""
    if not request.client or request.client.host not in LOCAL:
        raise HTTPException(403, "only possible on the computer where Sync runs")
    owner = user_for_token(db, load_agent_config().get("token"))
    if owner is None:
        raise HTTPException(404, "no profile is set up on this computer yet")
    return {"user": _profile(owner), "token": issue_token(db, owner.id, "browser")}


@router.get("/join/{code}")
def join_preview(code: str, db: Session = Depends(get_db)):
    p = db.scalar(select(Project).where(Project.invite_code == code))
    if p is None:
        raise HTTPException(404, "This invite link is no longer valid")
    members = access.user_names(db, access.member_ids(db, p.id))
    return {"project": p.name, "members": sorted(members.values())}


class JoinIn(BaseModel):
    code: str
    name: str = ""


@router.post("/join")
def join(body: JoinIn, user: User | None = Depends(optional_user), db: Session = Depends(get_db)):
    """Join a shared project with an invite link. New people create their profile here."""
    p = db.scalar(select(Project).where(Project.invite_code == body.code))
    if p is None:
        raise HTTPException(404, "This invite link is no longer valid")
    token = None
    if user is None:
        if not body.name.strip():
            raise HTTPException(400, "name is required")
        user = User(name=body.name.strip())
        db.add(user)
        db.flush()
        record(db, "user.created", actor="user", entity="user", entity_id=user.id, user_id=user.id,
               after={"name": user.name}, reason=f"joined through an invite to {p.name}")
        token = issue_token(db, user.id, "browser")
    access.add_member(db, p.id, user.id, added_by=user.id)
    return {"user": _profile(user), "token": token, "project_id": p.id}


# ---------------------------------------------------------------- me


class ProfileIn(BaseModel):
    name: str | None = None
    nudge_mode: str | None = None
    languages: list[str] | None = None


@router.get("/me")
def get_me(user: User = Depends(current_user)):
    return _profile(user)


@router.put("/me")
def put_me(body: ProfileIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    before = _profile(user)
    data = body.model_dump(exclude_none=True)
    if "nudge_mode" in data and data["nudge_mode"] not in ("quiet", "normal", "active"):
        raise HTTPException(400, "nudge_mode must be quiet, normal or active")
    if "name" in data:
        data["name"] = data["name"].strip()
        if not data["name"]:
            raise HTTPException(400, "name can't be empty")
    for k, v in data.items():
        setattr(user, k, v)
    b, a = changed(before, _profile(user))
    if a:
        record(db, "profile.updated", actor="user", entity="user", entity_id=user.id, user_id=user.id, before=b, after=a)
    return _profile(user)


@router.post("/me/agent-key")
def agent_key(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """A key for the desktop agent on another laptop (shown once)."""
    return {"token": issue_token(db, user.id, "desktop agent")}


@router.get("/me/playbook")
def playbook(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return [as_dict(p) for p in db.scalars(select(PlaybookEntry).where(PlaybookEntry.user_id == user.id)
                                           .order_by(PlaybookEntry.id.desc()))]


class PlaybookIn(BaseModel):
    text: str | None = None
    active: bool | None = None


class NewPlaybookIn(BaseModel):
    kind: str = "style"  # habit | style | effective_question | avoid
    text: str


@router.post("/me/playbook")
def playbook_add(body: NewPlaybookIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Tell Sync something about how you like to work (it is used in your calls' prompts and follow-ups)."""
    if body.kind not in ("habit", "style", "effective_question", "avoid") or not body.text.strip():
        raise HTTPException(400, "give a kind (habit, style, effective_question, avoid) and some text")
    e = PlaybookEntry(user_id=user.id, kind=body.kind, text=body.text.strip(), evidence=[])
    db.add(e)
    db.flush()
    record(db, "playbook.added", actor="user", entity="playbook", entity_id=e.id, user_id=user.id, after=as_dict(e))
    return as_dict(e)


@router.post("/me/playbook/reset")
def playbook_reset(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Forget everything Sync learned about you (project memory is not touched)."""
    entries = list(db.scalars(select(PlaybookEntry).where(PlaybookEntry.user_id == user.id)))
    for e in entries:
        db.delete(e)
    for g in db.scalars(select(GapStat).where(GapStat.user_id == user.id)):
        db.delete(g)
    record(db, "playbook.reset", actor="user", entity="playbook", user_id=user.id, before={"entries": len(entries)})
    return {"deleted": len(entries)}


@router.get("/me/playbook/history")
def playbook_history(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return [as_dict(e) for e in db.scalars(select(Event).where(Event.entity == "playbook", Event.user_id == user.id)
                                           .order_by(Event.id.desc()).limit(200))]


def _entry(db: Session, user: User, entry_id: int) -> PlaybookEntry:
    e = db.get(PlaybookEntry, entry_id)
    if e is None or e.user_id != user.id:
        raise HTTPException(404)
    return e


@router.patch("/me/playbook/{entry_id}")
def playbook_edit(entry_id: int, body: PlaybookIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    e = _entry(db, user, entry_id)
    before = as_dict(e)
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(e, k, v)
    b, a = changed(before, as_dict(e))
    record(db, "playbook.updated", actor="user", entity="playbook", entity_id=e.id, user_id=user.id, before=b, after=a)
    return as_dict(e)


@router.delete("/me/playbook/{entry_id}")
def playbook_delete(entry_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    e = _entry(db, user, entry_id)
    record(db, "playbook.deleted", actor="user", entity="playbook", entity_id=e.id, user_id=user.id, before=as_dict(e))
    db.delete(e)
    return {"deleted": entry_id}


@router.get("/me/autolisten")
def get_autolisten(user: User = Depends(current_user)):
    return autolisten.merged(user.autolisten)


@router.put("/me/autolisten")
def put_autolisten(body: dict, user: User = Depends(current_user), db: Session = Depends(get_db)):
    before = autolisten.merged(user.autolisten)
    allowed = set(autolisten.DEFAULTS)
    cfg = {**(user.autolisten or {}), **{k: v for k, v in body.items() if k in allowed}}
    if cfg.get("mode", "auto") not in ("auto", "ask", "off"):
        raise HTTPException(400, "mode must be auto, ask or off")
    user.autolisten = cfg
    after = autolisten.merged(cfg)
    b, a = changed(before, after)
    record(db, "autolisten.updated", actor="user", entity="user", entity_id=user.id, user_id=user.id, before=b, after=a)
    return after


@router.get("/search")
def search_all(q: str, project_id: int | None = None, k: int = 8, user: User = Depends(current_user),
               db: Session = Depends(get_db)):
    if project_id is not None:
        access.require_project(db, user, project_id)
    return search(db, q, user.id, project_id, k=k)
