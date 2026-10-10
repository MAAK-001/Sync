"""Semantic search over everything a user may see: their projects' items, conversations and documents, plus
their own unfiled conversations and personal documents."""
from __future__ import annotations

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from .. import access
from ..brain.memory import day, who
from ..embed import embedder, stack
from ..models import DocChunk, Document, Item, Project, User, Utterance
from ..models import Session as SessionRow


def search(db: Session, query: str, user_id: int, project_id: int | None = None, k: int = 8,
           min_score: float = 0.5) -> list[dict]:
    """``project_id`` narrows the search to one project (the caller checks membership)."""
    qv = embedder.encode1(query)
    results: list[dict] = []
    mine = access.project_ids(db, user_id)
    scope = {project_id} if project_id else mine
    projects = {p.id: p.name for p in db.scalars(select(Project).where(Project.id.in_(mine)))}
    names = {u.id: u.name for u in db.scalars(select(User))}
    own_sessions = select(SessionRow.id).where(SessionRow.user_id == user_id, SessionRow.project_id.is_(None))

    iq = select(Item).where(Item.superseded_by.is_(None), Item.status != "dropped", Item.vec.is_not(None))
    in_scope = Item.project_id.in_(scope)
    iq = iq.where(in_scope if project_id else or_(in_scope, and_(Item.project_id.is_(None),
                                                                 Item.session_id.in_(own_sessions))))
    items = db.scalars(iq).all()
    if items:
        scores = stack([i.vec for i in items]) @ qv
        for i, s in zip(items, scores, strict=True):
            if s >= min_score:
                extra = f" — answer: {i.answer}" if i.answer else ""
                results.append({"kind": i.type, "text": i.text + extra, "status": i.status, "score": float(s),
                                "source": f"{who(i, user_id, names)}, {day(i.created_at)}",
                                "project": projects.get(i.project_id, ""),
                                "ref": f"item:{i.id}"})

    uq = select(Utterance, SessionRow).join(SessionRow, SessionRow.id == Utterance.session_id).where(Utterance.vec.is_not(None))
    in_scope = SessionRow.project_id.in_(scope)
    uq = uq.where(in_scope if project_id else or_(in_scope, and_(SessionRow.user_id == user_id,
                                                                 SessionRow.project_id.is_(None))))
    rows = db.execute(uq.order_by(Utterance.id.desc()).limit(4000)).all()
    if rows:
        scores = stack([u.vec for u, _ in rows]) @ qv
        for (u, s_row), s in zip(rows, scores, strict=True):
            if s >= min_score + 0.05:
                if u.channel == "you":
                    speaker = "you" if s_row.user_id == user_id else names.get(s_row.user_id, "a teammate")
                else:
                    speaker = (s_row.stats or {}).get("speakers", {}).get(u.speaker, "them")
                results.append({"kind": "said", "text": u.text, "score": float(s) - 0.03,
                                "source": f"{speaker}, {day(s_row.started_at)}", "project": projects.get(s_row.project_id, ""),
                                "ref": f"utt:{u.id}"})

    dq = select(DocChunk, Document).join(Document, Document.id == DocChunk.document_id).where(
        or_(Document.project_id.in_(scope), and_(Document.project_id.is_(None), Document.user_id == user_id)))
    docs = db.execute(dq).all()
    if docs:
        scores = stack([c.vec for c, _ in docs]) @ qv
        for (c, d), s in zip(docs, scores, strict=True):
            if s >= min_score:
                results.append({"kind": "document", "text": c.text, "score": float(s),
                                "source": d.name + (f" §{c.section}" if c.section else ""), "project": projects.get(d.project_id, ""),
                                "ref": f"doc:{c.id}"})

    results.sort(key=lambda r: -r["score"])
    seen, out = set(), []
    for r in results:
        key = r["text"][:80].lower()
        if key not in seen:
            seen.add(key)
            r["score"] = round(r["score"], 3)
            out.append(r)
        if len(out) >= k:
            break
    return out
