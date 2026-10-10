"""Projects: an editable, shared record of everything known about a piece of work.

Members see and edit the same things: details, items (facts, numbers, commitments, questions…), people, documents,
conflicts and every conversation filed under the project. Every change is written to the history with the
member's name and the time. Deleting an item only hides it: the history keeps what it said.
"""
from __future__ import annotations

import secrets
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import access
from ..auth import current_user
from ..brain import brief as brief_mod
from ..brain import docs
from ..brain.memory import owner_label, who
from ..db import get_db
from ..embed import embedder, to_blob
from ..events import as_dict, changed, record
from ..live.signals import slot_value
from ..models import Conflict, DocChunk, DocFact, Document, Event, Item, Person, Project, ProjectMember, User, Utterance
from ..models import Session as SessionRow

router = APIRouter(tags=["projects"])

ITEM_TYPES = ("fact", "requirement", "decision", "commitment", "question", "concern", "proposal", "risk", "note")
ITEM_STATUSES = ("open", "proposed", "conditional", "agreed", "done")


def _touch(p: Project) -> None:
    p.updated_at = datetime.now(UTC)


def _names(db: Session) -> dict[int, str]:
    return {u.id: u.name for u in db.scalars(select(User))}


def item_out(it: Item, viewer_id: int, names: dict[int, str], sources: dict[int, dict] | None = None) -> dict:
    """An item as the viewer sees it: "you" for their own, teammates by name; with where it was said."""
    return {**as_dict(it), "added_by": names.get(it.user_id, ""), "speaker_label": who(it, viewer_id, names),
            "owner_label": owner_label(it, viewer_id, names) if it.owner else "",
            "source": (sources or {}).get(it.id)}


def item_sources(db: Session, items: list[Item], viewer_id: int, names: dict[int, str]) -> dict[int, dict]:
    """Where each item came from: the conversation, who said it and when in the call (for "quote · source" lines)."""
    sids = {i.session_id for i in items if i.session_id}
    if not sids:
        return {}
    sessions = {s.id: s for s in db.scalars(select(SessionRow).where(SessionRow.id.in_(sids)))}
    first = {i.id: (i.evidence or [None])[0] for i in items}
    utts = {u.id: u for u in db.scalars(select(Utterance).where(Utterance.id.in_([x for x in first.values() if x])))}
    out = {}
    for it in items:
        s = sessions.get(it.session_id)
        if s is None:
            continue
        u = utts.get(first[it.id])
        if u is None:
            speaker = ""
        elif u.channel == "you":
            speaker = "you" if s.user_id == viewer_id else names.get(s.user_id, "")
        else:
            speaker = (s.stats or {}).get("speakers", {}).get(u.speaker, "")
        title = s.title or (f"Call with {s.caller}" if s.caller else "") or (f"{s.app.title()} call" if s.app else "Conversation")
        out[it.id] = {"session_id": s.id, "title": title, "date": s.started_at.isoformat(),
                      "t": round(u.t_start, 1) if u else None, "speaker": speaker, "quote": u.text if u else ""}
    return out


def session_out(s: SessionRow, names: dict[int, str]) -> dict:
    return {**as_dict(s), "owner_name": names.get(s.user_id, "")}


# ---------------------------------------------------------------- projects


class PersonIn(BaseModel):
    name: str
    org: str = ""
    role: str = ""


class ProjectIn(BaseModel):
    name: str
    objective: str = ""
    context: str = ""
    people: list[PersonIn] = []


class ProjectPatch(BaseModel):
    name: str | None = None
    objective: str | None = None
    context: str | None = None
    brief: str | None = None  # the project summary ("where things stand")
    status: str | None = None  # active | closed


def create_project(db: Session, user: User, name: str, objective: str = "", context: str = "",
                   reason: str = "") -> Project:
    p = Project(name=name.strip(), objective=objective.strip(), context=context.strip(), created_by=user.id)
    db.add(p)
    db.flush()
    record(db, "project.created", actor="user", user_id=user.id, entity="project", entity_id=p.id, project_id=p.id,
           after={"name": p.name, "objective": p.objective, "context": p.context}, reason=reason)
    access.add_member(db, p.id, user.id, added_by=user.id)
    return p


@router.get("/projects")
def list_projects(user: User = Depends(current_user), db: Session = Depends(get_db)):
    ids = access.project_ids(db, user.id)
    if not ids:
        return []
    counts = dict(db.execute(select(Item.project_id, func.count()).where(
        Item.project_id.in_(ids), Item.status != "dropped", Item.superseded_by.is_(None)).group_by(Item.project_id)).all())
    sessions = dict(db.execute(select(SessionRow.project_id, func.count()).where(SessionRow.project_id.in_(ids))
                               .group_by(SessionRow.project_id)).all())
    names = _names(db)
    members: dict[int, list[str]] = {}
    for pid, uid in db.execute(select(ProjectMember.project_id, ProjectMember.user_id).where(ProjectMember.project_id.in_(ids))):
        members.setdefault(pid, []).append(names.get(uid, ""))
    return [{**as_dict(p), "items": counts.get(p.id, 0), "sessions": sessions.get(p.id, 0), "members": members.get(p.id, [])}
            for p in db.scalars(select(Project).where(Project.id.in_(ids)).order_by(Project.updated_at.desc()))]


@router.post("/projects")
def new_project(body: ProjectIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if not body.name.strip():
        raise HTTPException(400, "name is required")
    p = create_project(db, user, body.name, body.objective, body.context)
    for person in body.people:
        _add_person(db, user, p.id, person)
    return as_dict(p)


@router.get("/projects/{pid}")
def get_project(pid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = access.require_project(db, user, pid)
    return {**as_dict(p), "brief_card": brief_mod.brief(db, pid, user.id), "members": _members(db, pid)}


@router.patch("/projects/{pid}")
def patch_project(pid: int, body: ProjectPatch, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = access.require_project(db, user, pid)
    data = body.model_dump(exclude_none=True)
    if "name" in data and not data["name"].strip():
        raise HTTPException(400, "name can't be empty")
    if "status" in data and data["status"] not in ("active", "closed"):
        raise HTTPException(400, "status must be active or closed")
    before = as_dict(p)
    for k, v in data.items():
        setattr(p, k, v.strip() if isinstance(v, str) else v)
    b, a = changed(before, as_dict(p))
    a.pop("updated_at", None)
    b.pop("updated_at", None)
    if a:
        _touch(p)
        record(db, "project.updated", actor="user", user_id=user.id, entity="project", entity_id=p.id,
               project_id=p.id, before=b, after=a)
    return as_dict(p)


@router.get("/projects/{pid}/brief")
def project_brief(pid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    access.require_project(db, user, pid)
    return brief_mod.brief(db, pid, user.id)


# ---------------------------------------------------------------- members


def _members(db: Session, pid: int) -> list[dict]:
    rows = db.execute(select(User, ProjectMember.added_at).join(ProjectMember, ProjectMember.user_id == User.id)
                      .where(ProjectMember.project_id == pid).order_by(ProjectMember.added_at)).all()
    return [{"id": u.id, "name": u.name, "added_at": added.isoformat()} for u, added in rows]


@router.get("/projects/{pid}/members")
def members(pid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = access.require_project(db, user, pid)
    return {"members": _members(db, pid), "invite_code": p.invite_code, "created_by": p.created_by}


@router.post("/projects/{pid}/invite")
def new_invite(pid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """A new invite link for this project (any earlier link stops working)."""
    p = access.require_project(db, user, pid)
    p.invite_code = secrets.token_urlsafe(12)
    record(db, "invite.created", actor="user", user_id=user.id, entity="project", entity_id=pid, project_id=pid)
    return {"invite_code": p.invite_code}


@router.delete("/projects/{pid}/invite")
def revoke_invite(pid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = access.require_project(db, user, pid)
    p.invite_code = None
    record(db, "invite.revoked", actor="user", user_id=user.id, entity="project", entity_id=pid, project_id=pid)
    return {"invite_code": None}


@router.delete("/projects/{pid}/members/{uid}")
def remove_member(pid: int, uid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Leave a project yourself, or (as its creator) remove someone."""
    p = access.require_project(db, user, pid)
    if uid != user.id and p.created_by != user.id:
        raise HTTPException(403, "only the project's creator can remove other members")
    m = db.get(ProjectMember, (pid, uid))
    if m is None:
        raise HTTPException(404)
    if len(access.member_ids(db, pid)) == 1:
        raise HTTPException(400, "the last member can't leave; close the project instead")
    gone = db.get(User, uid)
    db.delete(m)
    record(db, "member.removed", actor="user", user_id=user.id, entity="user", entity_id=uid, project_id=pid,
           before={"name": gone.name if gone else ""})
    return {"removed": uid}


# ---------------------------------------------------------------- items


class ItemIn(BaseModel):
    type: str = "note"
    text: str
    status: str | None = None
    owner: str = ""
    due: str = ""
    answer: str = ""


class ItemPatch(BaseModel):
    type: str | None = None
    text: str | None = None
    status: str | None = None
    owner: str | None = None
    due: str | None = None
    answer: str | None = None
    condition: str | None = None


def _check_item_fields(data: dict) -> None:
    if "type" in data and data["type"] not in ITEM_TYPES:
        raise HTTPException(400, f"type must be one of {', '.join(ITEM_TYPES)}")
    if "status" in data and data["status"] not in ITEM_STATUSES:
        raise HTTPException(400, f"status must be one of {', '.join(ITEM_STATUSES)}")
    if "text" in data and not data["text"].strip():
        raise HTTPException(400, "text can't be empty")


def _set_slot(it: Item) -> None:
    """Numbers typed by people are tracked like numbers heard on calls (so calls can be checked against them)."""
    hit = slot_value(f"{it.text} {it.answer}")
    it.slot, it.value = (hit[0], hit[1]) if hit else ("", {})


def add_item(db: Session, user: User, pid: int, body: ItemIn, actor: str = "user", reason: str = "") -> Item:
    data = body.model_dump()
    _check_item_fields({k: v for k, v in data.items() if v is not None})
    status = body.status or ("agreed" if body.type in ("commitment", "decision") else "open")
    it = Item(project_id=pid, user_id=user.id, type=body.type, text=body.text.strip(), status=status,
              owner=body.owner.strip(), due=body.due.strip(), answer=body.answer.strip(), speaker="me", confirmed=True,
              created_by=actor, confidence=1.0, vec=to_blob(embedder.encode1(body.text)))
    _set_slot(it)
    db.add(it)
    db.flush()
    _touch(db.get(Project, pid))
    record(db, "item.created", actor=actor, user_id=user.id, entity="item", entity_id=it.id, project_id=pid,
           after=as_dict(it), reason=reason)
    return it


@router.get("/projects/{pid}/items")
def project_items(pid: int, type: str | None = None, status: str | None = None, include_history: bool = False,
                  user: User = Depends(current_user), db: Session = Depends(get_db)):
    access.require_project(db, user, pid)
    q = select(Item).where(Item.project_id == pid).order_by(Item.created_at)
    if not include_history:
        q = q.where(Item.superseded_by.is_(None), Item.status != "dropped")
    if type:
        q = q.where(Item.type == type)
    if status:
        q = q.where(Item.status == status)
    names = _names(db)
    items = list(db.scalars(q))
    sources = item_sources(db, items, user.id, names)
    return [item_out(i, user.id, names, sources) for i in items]


@router.get("/commitments")
def commitments(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Every open promise across your projects and your own conversations: yours, your team's and theirs."""
    ids = access.project_ids(db, user.id)
    own_unfiled = select(SessionRow.id).where(SessionRow.user_id == user.id, SessionRow.project_id.is_(None))
    q = (select(Item).where(Item.type == "commitment", Item.status != "dropped", Item.superseded_by.is_(None))
         .where(Item.project_id.in_(ids) | (Item.project_id.is_(None) & Item.session_id.in_(own_unfiled)))
         .order_by(Item.created_at.desc()))
    items = list(db.scalars(q))
    names = _names(db)
    sources = item_sources(db, items, user.id, names)
    projects = {p.id: p.name for p in db.scalars(select(Project).where(Project.id.in_(ids)))}
    member_names = {names.get(uid, "").lower(): uid for pid in ids for uid in access.member_ids(db, pid)} if ids else {}
    member_names[user.name.lower()] = user.id
    out = []
    for it in items:
        d = item_out(it, user.id, names, sources)
        d["project"] = projects.get(it.project_id, "")
        if it.owner == "me":  # "me" is whoever's call (or edit) recorded it
            d["whose"] = "mine" if it.user_id in (None, user.id) else "team"
        else:  # an owner written by name may still be you or a teammate
            uid = member_names.get(it.owner.strip().lower())
            d["whose"] = "theirs" if uid is None else "mine" if uid == user.id else "team"
        out.append(d)
    return out


@router.post("/projects/{pid}/items")
def new_item(pid: int, body: ItemIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    access.require_project(db, user, pid)
    return item_out(add_item(db, user, pid, body), user.id, _names(db))


@router.patch("/items/{item_id}")
def patch_item(item_id: int, body: ItemPatch, user: User = Depends(current_user), db: Session = Depends(get_db)):
    it = access.require_item(db, user, item_id)
    data = body.model_dump(exclude_none=True)
    _check_item_fields(data)
    before = as_dict(it)
    for k, v in data.items():
        setattr(it, k, v.strip() if isinstance(v, str) else v)
    if "text" in data:
        it.vec = to_blob(embedder.encode1(it.text))
    if "text" in data or "answer" in data:
        _set_slot(it)
    it.confirmed = True
    b, a = changed(before, as_dict(it))
    for k in ("updated_at",):
        a.pop(k, None)
        b.pop(k, None)
    if a:
        if it.project_id:
            _touch(db.get(Project, it.project_id))
        record(db, "item.updated", actor="user", user_id=user.id, entity="item", entity_id=it.id,
               project_id=it.project_id, session_id=it.session_id, before=b, after=a)
    return item_out(it, user.id, _names(db))


@router.delete("/items/{item_id}")
def delete_item(item_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Remove an item from the project. The history keeps what it said."""
    it = access.require_item(db, user, item_id)
    before = {"status": it.status, "text": it.text, "type": it.type}
    it.status = "dropped"
    if it.project_id:
        _touch(db.get(Project, it.project_id))
    record(db, "item.deleted", actor="user", user_id=user.id, entity="item", entity_id=it.id, project_id=it.project_id,
           session_id=it.session_id, before=before, after={"status": "dropped"})
    return {"deleted": item_id}


# ---------------------------------------------------------------- people


class PersonPatch(BaseModel):
    name: str | None = None
    org: str | None = None
    role: str | None = None
    notes: str | None = None


def _add_person(db: Session, user: User, pid: int, body: PersonIn) -> Person:
    if not body.name.strip():
        raise HTTPException(400, "name is required")
    person = db.scalar(select(Person).where(Person.project_id == pid, Person.name.ilike(body.name.strip())))
    if person is not None:
        return person
    person = Person(project_id=pid, name=body.name.strip(), org=body.org.strip(), role=body.role.strip(), concerns=[])
    db.add(person)
    db.flush()
    record(db, "person.added", actor="user", user_id=user.id, entity="person", entity_id=person.id, project_id=pid,
           after={"name": person.name, "org": person.org, "role": person.role})
    return person


def _person(db: Session, user: User, person_id: int) -> Person:
    person = db.get(Person, person_id)
    if person is None or not access.is_member(db, user.id, person.project_id):
        raise HTTPException(404, "person not found")
    return person


@router.get("/projects/{pid}/people")
def project_people(pid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    access.require_project(db, user, pid)
    return [as_dict(x) for x in db.scalars(select(Person).where(Person.project_id == pid).order_by(Person.name))]


@router.post("/projects/{pid}/people")
def add_person(pid: int, body: PersonIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    access.require_project(db, user, pid)
    return as_dict(_add_person(db, user, pid, body))


@router.patch("/people/{person_id}")
def patch_person(person_id: int, body: PersonPatch, user: User = Depends(current_user), db: Session = Depends(get_db)):
    person = _person(db, user, person_id)
    data = body.model_dump(exclude_none=True)
    if "name" in data and not data["name"].strip():
        raise HTTPException(400, "name can't be empty")
    before = as_dict(person)
    for k, v in data.items():
        setattr(person, k, v.strip())
    b, a = changed(before, as_dict(person))
    if a:
        record(db, "person.updated", actor="user", user_id=user.id, entity="person", entity_id=person.id,
               project_id=person.project_id, before=b, after=a)
    return as_dict(person)


@router.delete("/people/{person_id}")
def delete_person(person_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    person = _person(db, user, person_id)
    record(db, "person.removed", actor="user", user_id=user.id, entity="person", entity_id=person.id,
           project_id=person.project_id, before={"name": person.name, "org": person.org, "role": person.role})
    db.delete(person)
    return {"deleted": person_id}


# ---------------------------------------------------------------- conflicts, conversations, history


@router.get("/projects/{pid}/conflicts")
def project_conflicts(pid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    access.require_project(db, user, pid)
    return [as_dict(c) for c in db.scalars(select(Conflict).where(Conflict.project_id == pid).order_by(Conflict.id))]


class ResolveIn(BaseModel):
    resolution: str
    keep_item_id: int | None = None


@router.post("/conflicts/{cid}/resolve")
def resolve_conflict(cid: int, body: ResolveIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    c = db.get(Conflict, cid)
    if c is None or not access.is_member(db, user.id, c.project_id):
        raise HTTPException(404)
    c.status, c.resolution = "resolved", body.resolution.strip()
    if body.keep_item_id in (c.item_a, c.item_b):
        other = c.item_b if body.keep_item_id == c.item_a else c.item_a
        db.get(Item, other).superseded_by = body.keep_item_id
    record(db, "conflict.resolved", actor="user", user_id=user.id, entity="conflict", entity_id=c.id,
           project_id=c.project_id, after={"resolution": c.resolution, "kept": body.keep_item_id})
    return as_dict(c)


@router.get("/projects/{pid}/sessions")
def project_sessions(pid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    access.require_project(db, user, pid)
    names = _names(db)
    return [session_out(s, names) for s in db.scalars(select(SessionRow).where(SessionRow.project_id == pid)
                                                      .order_by(SessionRow.started_at.desc()))]


@router.get("/projects/{pid}/events")
def project_events(pid: int, limit: int = 300, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """The full, append-only history of this project: every change, by whom, when and why."""
    access.require_project(db, user, pid)
    return [as_dict(e) for e in db.scalars(select(Event).where(Event.project_id == pid)
                                           .order_by(Event.id.desc()).limit(limit))]


# ---------------------------------------------------------------- documents


def _doc_visible(db: Session, user: User, d: Document) -> bool:
    return access.is_member(db, user.id, d.project_id) if d.project_id else d.user_id == user.id


@router.get("/documents")
def list_documents(project_id: int | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """This project's shared documents plus your personal ones (which apply to all your conversations)."""
    if project_id is not None:
        access.require_project(db, user, project_id)
    personal = Document.project_id.is_(None) & (Document.user_id == user.id)
    q = select(Document).where((Document.project_id == project_id) | personal if project_id else personal)
    facts = dict(db.execute(select(DocFact.document_id, func.count()).group_by(DocFact.document_id)).all())
    names = _names(db)
    return [{"id": d.id, "name": d.name, "project_id": d.project_id, "created_at": d.created_at.isoformat(),
             "chars": len(d.text), "facts": facts.get(d.id, 0), "added_by": names.get(d.user_id, "")}
            for d in db.scalars(q.order_by(Document.id.desc()))]


@router.post("/documents")
async def upload_document(file: UploadFile = File(...), project_id: int | None = Form(None),
                          user: User = Depends(current_user), db: Session = Depends(get_db)):
    data = await file.read()
    name = (file.filename or "document").rsplit(".", 1)[0].replace("_", " ").replace("-", " ").title()
    text = docs.pdf_text(data) if (file.filename or "").lower().endswith(".pdf") else data.decode("utf-8", errors="ignore")
    if project_id is not None:
        access.require_project(db, user, project_id)
    if not text.strip():
        raise HTTPException(400, "no text found in this file")
    doc = docs.ingest(db, name, text, project_id, user.id)
    return {"id": doc.id, "name": doc.name, "project_id": project_id, "chars": len(text),
            "chunks": db.scalar(select(func.count()).select_from(DocChunk).where(DocChunk.document_id == doc.id)),
            "facts": [{"slot": f.slot, "value": f.value.get("text"), "direction": f.direction, "text": f.text}
                      for f in db.scalars(select(DocFact).where(DocFact.document_id == doc.id))]}


@router.delete("/documents/{doc_id}")
def delete_document(doc_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    d = db.get(Document, doc_id)
    if d is None or not _doc_visible(db, user, d):
        raise HTTPException(404)
    record(db, "document.removed", actor="user", user_id=user.id, entity="document", entity_id=d.id,
           project_id=d.project_id, before={"name": d.name})
    db.delete(d)
    return {"deleted": doc_id}
