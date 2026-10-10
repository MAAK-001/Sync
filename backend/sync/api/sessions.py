"""Conversations: start (manual, sample or auto-listen), feed utterances, choose the project, end, recap,
follow-ups and private questions.

A conversation belongs to the person who had it: only they control it while it's live and choose its project.
Once it is filed under a project, every member of that project can see it (and confirm or correct what it filed).
The project is always chosen by the user. Sync never guesses it.
"""
from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .. import access, autolisten, bus
from ..auth import current_user
from ..brain import brief as brief_mod
from ..config import settings
from ..db import get_db, session_scope
from ..events import as_dict, record
from ..live.engine import get_live, start_live
from ..llm import llm
from ..models import Event, Item, Nudge, Project, User, Utterance
from ..models import Session as SessionRow
from .projects import create_project, item_out, item_sources, session_out

router = APIRouter(tags=["sessions"])
APP_LABEL = {"phone link": "Phone Link call", "zoom": "Zoom meeting", "teams": "Teams meeting", "meet": "Google Meet",
             "webex": "Webex meeting", "whatsapp": "WhatsApp call", "slack": "Slack huddle", "discord": "Discord call"}


class SessionIn(BaseModel):
    mode: str = "online"  # online | in_person | script
    project_id: int | None = None
    title: str = ""
    app: str = ""
    caller: str = ""
    objective: str = ""
    speed: float = 1.0  # >1 replays scripted conversations faster (evals)
    audio: bool = False  # attach local speech recognition to this session's WebSocket audio


def _names(db: Session) -> dict[int, str]:
    return {u.id: u.name for u in db.scalars(select(User))}


def _create_row(db: Session, user: User, body: SessionIn, status: str) -> SessionRow:
    if body.project_id is not None:
        access.require_project(db, user, body.project_id)
    row = SessionRow(user_id=user.id, mode=body.mode, project_id=body.project_id, title=body.title, app=body.app,
                     caller=body.caller, objective=body.objective, status=status)
    db.add(row)
    db.flush()
    record(db, f"session.{status if status != 'live' else 'started'}", actor="system", user_id=user.id,
           entity="session", entity_id=row.id, project_id=row.project_id, session_id=row.id,
           after={"mode": row.mode, "app": row.app, "title": row.title, "caller": row.caller})
    return row


async def _go_live(sid: int, speed: float, audio: bool) -> dict:
    live = await start_live(sid, speed=speed)
    if audio and getattr(live, "audio", None) is None:
        from ..stt.engine import AudioPipeline

        live.audio = AudioPipeline(live)
        await live.audio.start()
    with session_scope() as db:
        audience = access.audience(db, db.get(SessionRow, sid))
    # The owner's desktop agent shows "Sync is on" and, if no project is set yet, asks which project this is.
    bus.publish({"type": "session.live", "session_id": sid, "owner_id": live.user_id, "project_id": live.project_id,
                 "app": live.app, "title": live.title, "caller": live.caller}, audience)
    return {"id": sid, **live.snapshot()}


@router.post("/sessions")
async def create_session(body: SessionIn, user: User = Depends(current_user)):
    with session_scope() as db:
        sid = _create_row(db, db.get(User, user.id), body, "live").id
    return await _go_live(sid, body.speed, body.audio)


# ---------------------------------------------------------------- auto-listen (desktop agent)


class CallIn(BaseModel):
    app: str
    title: str = ""
    caller: str = ""


async def _prewarm() -> None:
    """Load what a call needs while the skip countdown runs, so listening starts the moment it ends."""
    from ..stt.engine import recognizer

    jobs = [asyncio.get_running_loop().run_in_executor(None, recognizer)]
    if settings.llm_enabled:
        jobs.append(llm.warm(settings.live_model, [{"role": "user", "content": "ok"}]))
    await asyncio.gather(*jobs, return_exceptions=True)


@router.post("/calls/detected")
def call_detected(body: CallIn, background: BackgroundTasks, user: User = Depends(current_user),
                  db: Session = Depends(get_db)):
    """The user's desktop agent saw a call/meeting start. Decide auto | ask | never from their rules."""
    cfg = autolisten.merged(user.autolisten)
    decision, reason = autolisten.decide(cfg, body.app, body.title, body.caller)
    if decision == "never":
        if cfg["log_skipped"]:
            record(db, "session.skipped", actor="system", user_id=user.id, entity="session",
                   after={"app": body.app}, reason=reason)
        return {"decision": decision, "reason": reason}
    background.add_task(_prewarm)
    row = _create_row(db, user, SessionIn(mode="online", app=body.app, title=body.title, caller=body.caller), "detected")
    return {"decision": decision, "reason": reason, "session_id": row.id, "countdown": cfg["countdown"]}


class StartIn(BaseModel):
    audio: bool = True


@router.post("/sessions/{sid}/start")
async def start_detected(sid: int, body: StartIn | None = None, user: User = Depends(current_user)):
    with session_scope() as db:
        row = access.require_session(db, user, sid, owner=True)
        if row.status not in ("detected", "live"):
            raise HTTPException(409, "this conversation can't be started")
    return await _go_live(sid, 1.0, (body or StartIn()).audio)


@router.post("/sessions/{sid}/skip")
def skip_session(sid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """The user skipped the call during the countdown: nothing is kept."""
    row = access.require_session(db, user, sid, owner=True)
    if row.status != "detected":
        raise HTTPException(409, "only a call that hasn't started can be skipped")
    app = row.app
    db.delete(row)
    if autolisten.merged(user.autolisten)["log_skipped"]:
        record(db, "session.skipped", actor="user", user_id=user.id, entity="session", after={"app": app},
               reason="skipped by user")
    return {"skipped": sid}


# ---------------------------------------------------------------- live interaction (the owner only)


def _live_owned(sid: int, user: User):
    live = get_live(sid)
    if live is None:
        raise HTTPException(404, "this conversation is not live")
    if live.user_id != user.id:
        raise HTTPException(403, "only the person on the call can do that")
    return live


class UtteranceIn(BaseModel):
    channel: str  # you | them
    text: str
    speaker: str = ""
    t_start: float | None = None
    t_end: float | None = None


@router.post("/sessions/{sid}/utterances")
async def add_utterance(sid: int, body: UtteranceIn, user: User = Depends(current_user)):
    u = await _live_owned(sid, user).add_utterance(body.channel, body.text, speaker=body.speaker,
                                                   t_start=body.t_start, t_end=body.t_end)
    return {"id": u.id, "seq": u.seq} if u else {"ignored": True}


class EndIn(BaseModel):
    delete: bool = False


@router.post("/sessions/{sid}/end")
async def end_session(sid: int, body: EndIn | None = None, user: User = Depends(current_user)):
    delete = bool(body and body.delete)
    with session_scope() as db:
        access.require_session(db, user, sid, owner=True)
    live = get_live(sid)
    if live is not None:
        await live.end(delete=delete)
    elif delete:
        with session_scope() as db:
            row = db.get(SessionRow, sid)
            pid = row.project_id
            db.delete(row)
            record(db, "session.deleted", actor="user", user_id=user.id, entity="session", entity_id=sid,
                   project_id=pid, reason="deleted by its owner")
    return {"ended": sid, "deleted": delete}


class AskIn(BaseModel):
    question: str


@router.post("/sessions/{sid}/ask")
async def ask(sid: int, body: AskIn, user: User = Depends(current_user)):
    return await _live_owned(sid, user).ask(body.question)


@router.post("/sessions/{sid}/catchup")
async def catchup(sid: int, user: User = Depends(current_user)):
    return {"lines": await _live_owned(sid, user).catchup()}


@router.post("/sessions/{sid}/wrapup")
def wrapup(sid: int, user: User = Depends(current_user)):
    """Start the wrap-up now (the card also appears automatically when closing language is heard)."""
    return _live_owned(sid, user).start_wrapup()


class LinkIn(BaseModel):
    project_id: int | None = None
    new_project: bool = False  # create a new project for this conversation
    new_project_name: str = ""  # optional; otherwise named after the call (rename it any time)


def _auto_name(row: SessionRow) -> str:
    what = row.caller or (row.title if row.title and row.title != "Phone call" else "") \
        or APP_LABEL.get(row.app, f"{row.app.title()} call" if row.app else "Conversation")
    return f"{what} · {datetime.now():%d %b, %H:%M}"


@router.post("/sessions/{sid}/link")
async def link(sid: int, body: LinkIn, user: User = Depends(current_user)):
    """File this conversation under a project (the user's choice), or under a new project."""
    with session_scope() as db:
        row = access.require_session(db, user, sid, owner=True)
        if body.new_project or body.new_project_name.strip():
            p = create_project(db, db.get(User, user.id), body.new_project_name.strip() or _auto_name(row),
                               reason="created for a conversation")
            pid = p.id
        elif body.project_id is not None:
            pid = access.require_project(db, db.get(User, user.id), body.project_id).id
        else:
            raise HTTPException(400, "choose a project or a new project")
        db.get(Project, pid).updated_at = datetime.now(UTC)
    live = get_live(sid)
    if live is not None:
        await live.link_project(pid, actor="user", reason="chosen by the user")
    else:
        from ..brain.consolidate import _merge_into_project

        with session_scope() as db:
            row = db.get(SessionRow, sid)
            row.project_id = pid
            for it in db.scalars(select(Item).where(Item.session_id == sid)):
                it.project_id = pid
            db.flush()
            _merge_into_project(db, row)  # check what it said against what the project already knows
            record(db, "session.linked", actor="user", user_id=user.id, entity="session", entity_id=sid,
                   project_id=pid, session_id=sid, after={"project_id": pid})
            audience = access.audience(db, row)
    with session_scope() as db:
        name = db.get(Project, pid).name
    if live is None:
        bus.publish({"type": "project.linked", "session_id": sid, "owner_id": user.id, "project_id": pid, "name": name},
                    audience)
    return {"session_id": sid, "project_id": pid, "name": name}


class FeedbackIn(BaseModel):
    action: str  # used | useful | dismissed


@router.post("/nudges/{nid}/feedback")
async def nudge_feedback(nid: int, body: FeedbackIn, user: User = Depends(current_user)):
    with session_scope() as db:
        n = db.get(Nudge, nid)
        if n is None:
            raise HTTPException(404)
        sid = n.session_id
    await _live_owned(sid, user).feedback(nid, body.action)
    return {"id": nid, "outcome": body.action}


# ---------------------------------------------------------------- after the call (everyone who can see it)


@router.get("/sessions")
def list_sessions(limit: int = 50, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Your conversations plus the ones filed under projects you're a member of."""
    ids = access.project_ids(db, user.id)
    q = select(SessionRow).where(or_(SessionRow.user_id == user.id, SessionRow.project_id.in_(ids)))
    names = _names(db)
    return [session_out(s, names) for s in db.scalars(q.order_by(SessionRow.id.desc()).limit(limit))]


@router.get("/sessions/{sid}")
def get_session(sid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    row = access.require_session(db, user, sid)
    live = get_live(sid)
    nudges = [as_dict(n) for n in db.scalars(select(Nudge).where(Nudge.session_id == sid).order_by(Nudge.id))]
    return {**session_out(row, _names(db)), "live": live.snapshot() if live else None, "nudges": nudges,
            "is_owner": row.user_id == user.id}


@router.get("/sessions/{sid}/transcript")
def transcript(sid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    row = access.require_session(db, user, sid)
    names = (row.stats or {}).get("speakers", {})
    return [{"seq": u.seq, "channel": u.channel, "speaker": names.get(u.speaker, u.speaker), "text": u.text,
             "t": round(u.t_start or 0, 1)}
            for u in db.scalars(select(Utterance).where(Utterance.session_id == sid).order_by(Utterance.seq))]


@router.get("/sessions/{sid}/recap")
def recap(sid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    row = access.require_session(db, user, sid)
    names = _names(db)
    rows = list(db.scalars(select(Item).where(Item.session_id == sid, Item.status != "dropped")))
    sources = item_sources(db, rows, user.id, names)
    items = [item_out(i, user.id, names, sources) for i in rows]
    return {"status": row.recap_status, "recap": row.recap, "items": items}


class ConfirmIn(BaseModel):
    item_ids: list[int] | None = None  # None = confirm all
    reject_ids: list[int] = []


@router.post("/sessions/{sid}/recap/confirm")
def confirm_recap(sid: int, body: ConfirmIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    access.require_session(db, user, sid)
    items = db.scalars(select(Item).where(Item.session_id == sid, Item.status != "dropped")).all()
    confirmed, rejected = [], []
    for it in items:
        if it.id in body.reject_ids:
            before = it.status
            it.status = "dropped"
            rejected.append(it.id)
            record(db, "item.rejected", actor="user", user_id=user.id, entity="item", entity_id=it.id,
                   project_id=it.project_id, session_id=sid, before={"status": before, "text": it.text},
                   after={"status": "dropped"})
        elif (body.item_ids is None or it.id in body.item_ids) and not it.confirmed:
            it.confirmed = True
            confirmed.append(it.id)
            record(db, "item.confirmed", actor="user", user_id=user.id, entity="item", entity_id=it.id,
                   project_id=it.project_id, session_id=sid, after={"confirmed": True})
    return {"confirmed": confirmed, "rejected": rejected}


@router.post("/sessions/{sid}/followup")
async def followup(sid: int, user: User = Depends(current_user)):
    with session_scope() as db:
        access.require_session(db, user, sid)
    text = await brief_mod.followup(sid, user.id)
    with session_scope() as db:
        row = db.get(SessionRow, sid)
        record(db, "followup.drafted", actor="t2", user_id=user.id, entity="session", entity_id=sid,
               project_id=row.project_id, session_id=sid, after={"text": text})
    return {"text": text}


@router.get("/sessions/{sid}/events")
def session_events(sid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    access.require_session(db, user, sid)
    return [as_dict(e) for e in db.scalars(select(Event).where(Event.session_id == sid).order_by(Event.id))]


class PasteIn(BaseModel):
    text: str
    project_id: int | None = None
    title: str = ""


_LINE = re.compile(r"^\s*([^:\n]{1,40}):\s*(.+)$")


@router.post("/sessions/paste")
async def paste(body: PasteIn, user: User = Depends(current_user)):
    """Paste a chat, email thread or notes ("Name: text" lines). Runs the same pipeline, then the post-call pass."""
    with session_scope() as db:
        me = db.get(User, user.id)
        sid = _create_row(db, me, SessionIn(mode="paste", project_id=body.project_id, title=body.title), "live").id
        user_names = {"me", "you", me.name.lower()}
    live = await start_live(sid, speed=1000.0)
    live.show_nudges = False
    labels: dict[str, str] = {}
    for raw in body.text.splitlines():
        m = _LINE.match(raw)
        name, text = (m.group(1).strip(), m.group(2).strip()) if m else ("", raw.strip())
        if not text:
            continue
        if name.lower() in user_names:
            await live.add_utterance("you", text)
        else:
            label = labels.setdefault(name or "?", chr(ord("A") + len(labels)))
            if name:
                live.signals.names[label] = name
            await live.add_utterance("them", text, speaker=label)
    await live.end()
    return {"id": sid, "status": "consolidating"}
