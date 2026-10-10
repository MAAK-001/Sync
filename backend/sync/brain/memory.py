"""Load what a project already knows, in the shapes the live engine needs."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

import numpy as np
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .. import textparse as tp
from ..embed import from_blob
from ..live.signals import DocFactRef, KnownItem, Memory
from ..models import Conflict, DocChunk, DocFact, Document, Item, Person, Project, ProjectMember, User

MEMORY_TYPES = ("fact", "requirement", "decision", "commitment", "question", "concern", "proposal", "risk", "note")


def day(dt: datetime | None) -> str:
    """"9 Oct" in the user's own time zone (the database stores UTC)."""
    if dt is None:
        return ""
    local = (dt if dt.tzinfo else dt.replace(tzinfo=UTC)).astimezone()
    return f"{local.day} {local:%b}"


@dataclass
class DocIndex:
    """Sentence-window chunks of the user's documents, for claim checks and private questions."""

    ids: list[int] = field(default_factory=list)
    texts: list[str] = field(default_factory=list)
    refs: list[str] = field(default_factory=list)
    mat: np.ndarray = field(default_factory=lambda: np.zeros((0, 384), dtype=np.float32))

    def search(self, vec: np.ndarray, k: int = 3, min_score: float = 0.45) -> list[tuple[int, str, str, float]]:
        if self.mat.shape[0] == 0:
            return []
        scores = self.mat @ vec
        order = np.argsort(-scores)[:k]
        return [(self.ids[i], self.texts[i], self.refs[i], float(scores[i])) for i in order if scores[i] >= min_score]


def _documents(db: Session, project_id: int | None, user_id: int | None) -> list[Document]:
    """The project's shared documents plus the user's own personal ones."""
    mine = (Document.project_id.is_(None)) & (Document.user_id == user_id)
    q = select(Document).where(or_(Document.project_id == project_id, mine) if project_id else mine)
    return list(db.scalars(q))


def who(it: Item, viewer_id: int | None, names: dict[int, str]) -> str:
    """Who said or owns an item, from the point of view of ``viewer_id`` ("you", a teammate's name, or a contact)."""
    if it.speaker in ("me", "you"):
        return "you" if it.user_id in (None, viewer_id) else names.get(it.user_id, "a teammate")
    return it.speaker or "someone"


def owner_label(it: Item, viewer_id: int | None, names: dict[int, str]) -> str:
    if it.owner in ("me", "you"):
        return "you" if it.user_id in (None, viewer_id) else names.get(it.user_id, "a teammate")
    return it.owner


def _line(it: Item, viewer_id: int | None, names: dict[int, str]) -> str:
    status = f" [{it.status}]" if it.type in ("question", "commitment", "proposal") else ""
    extra = f" — answer: {it.answer}" if it.answer else ""
    owner = f"; owner {owner_label(it, viewer_id, names)}" if it.owner else ""
    due = f"; due {it.due}" if it.due else ""
    return f"- {it.type}{status}: {it.text}{extra}{owner}{due} ({who(it, viewer_id, names)}, {day(it.created_at)})"


def has_number(it: Item) -> bool:
    return bool(it.slot or it.due or tp.mentions_number(f"{it.text} {it.answer} {it.condition}"))


def load(db: Session, project_id: int | None, user_id: int | None,
         full: bool = False) -> tuple[Memory, DocIndex, str]:
    """What a conversation starts from: (memory for the instant checks, document index, text for model prompts).

    The text always carries the project summary, its goal and context, open conflicts and EVERY number on record
    (amounts, quantities, dates, days, times). Other items are capped for speed on a CPU unless ``full``.
    ``user_id`` is whose conversation it is: "you" in the memory means that person.
    """
    known: list[KnownItem] = []
    lines: list[str] = []
    p = db.get(Project, project_id) if project_id else None
    if p is not None:
        items = db.scalars(
            select(Item).where(Item.project_id == project_id, Item.superseded_by.is_(None), Item.status != "dropped")
            .order_by(Item.updated_at)
        ).all()
        names = {u.id: u.name for u in db.scalars(select(User))}
        for it in items:
            if it.type in MEMORY_TYPES:
                known.append(KnownItem(item_id=it.id, type=it.type, text=it.text, who=who(it, user_id, names),
                                       when=day(it.created_at), slot=it.slot, value=it.value or None, answer=it.answer,
                                       vec=from_blob(it.vec)))
        lines.append(f"PROJECT: {p.name}" + (" (closed)" if p.status != "active" else ""))
        if p.objective.strip():
            lines.append(f"GOAL: {p.objective.strip()}")
        if p.brief.strip():
            lines.append(f"SUMMARY (where things stand):\n{p.brief.strip()}")
        if p.context.strip():
            lines.append(f"CONTEXT:\n{p.context.strip()[:1500]}")
        conflicts = db.scalars(select(Conflict).where(Conflict.project_id == project_id, Conflict.status == "open")).all()
        if conflicts:
            lines.append("OPEN CONFLICTS (two different values on record):\n" + "\n".join(f"- {c.explanation}" for c in conflicts))
        numbered = [it for it in items if has_number(it)]
        if numbered:
            lines.append("NUMBERS ON RECORD (every amount, quantity, date, day and time):")
            lines += [_line(it, user_id, names) for it in numbered[-150:]]
        rank = {"fact": 0, "requirement": 1, "decision": 2, "commitment": 3, "question": 4, "concern": 5, "note": 6,
                "proposal": 7, "risk": 8}
        rest = [it for it in items if not has_number(it)]
        rest = sorted(rest, key=lambda i: (rank.get(i.type, 9), -i.id))
        if rest:
            lines.append("OTHER MEMORY:")
            lines += [_line(it, user_id, names) for it in (rest if full else rest[:24])]
        people = db.scalars(select(Person).where(Person.project_id == project_id)).all()
        if people:
            lines.append("PEOPLE: " + "; ".join(
                f"{x.name} ({', '.join(v for v in (x.role, x.org) if v)})" if (x.role or x.org) else x.name for x in people))
        team = [names[m] for m in db.scalars(select(ProjectMember.user_id).where(ProjectMember.project_id == project_id))
                if m in names]
        if len(team) > 1:
            lines.append("TEAM (Sync users sharing this project): " + ", ".join(team))

    docs = _documents(db, project_id, user_id)
    doc_names = {d.id: d.name for d in docs}
    facts: list[DocFactRef] = []
    index = DocIndex()
    if docs:
        for f in db.scalars(select(DocFact).where(DocFact.document_id.in_(doc_names))):
            ref = doc_names[f.document_id] + (f" §{f.section}" if f.section else "")
            facts.append(DocFactRef(id=f.id, slot=f.slot, value=f.value, direction=f.direction, text=f.text, ref=ref,
                                    vec=from_blob(f.vec)))
        chunks = db.scalars(select(DocChunk).where(DocChunk.document_id.in_(doc_names))).all()
        vecs = [from_blob(c.vec) for c in chunks if c.vec is not None]
        index = DocIndex(
            ids=[c.id for c in chunks if c.vec is not None],
            texts=[c.text for c in chunks if c.vec is not None],
            refs=[doc_names[c.document_id] + (f" §{c.section}" if c.section else "") for c in chunks if c.vec is not None],
            mat=np.vstack(vecs) if vecs else np.zeros((0, 384), dtype=np.float32),
        )
    memory = Memory(known=known, doc_facts=facts, has_docs=bool(index.ids))
    text = "\n".join(lines) if lines else "(no project chosen yet — nothing on record)"
    return memory, index, text
