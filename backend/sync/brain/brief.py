"""Before a call: an instant, deterministic brief. After a call: a follow-up draft (deep model)."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..embed import embedder
from ..live.signals import Signals
from ..llm import llm
from ..models import Conflict, Item, Person, PlaybookEntry, Project, User
from ..models import Session as SessionRow
from . import memory as memory_mod


def brief(db: Session, project_id: int, viewer_id: int | None) -> dict:
    """Where a project stands, from ``viewer_id``'s point of view ("you" = the viewer; teammates by name)."""
    p = db.get(Project, project_id)
    if p is None:
        raise KeyError("project not found")
    items = db.scalars(select(Item).where(Item.project_id == project_id, Item.superseded_by.is_(None),
                                          Item.status != "dropped")).all()
    last = db.scalar(select(SessionRow).where(SessionRow.project_id == project_id, SessionRow.status == "ended")
                     .order_by(SessionRow.id.desc()))
    memory, _docs, _text = memory_mod.load(db, project_id, viewer_id)
    sig = Signals(memory, embedder.encode)
    names = {u.id: u.name for u in db.scalars(select(User))}

    def fmt(i: Item) -> str:
        due = f" (due {i.due})" if i.due else ""
        owner = memory_mod.owner_label(i, viewer_id, names) if i.owner == "me" else ""
        return f"{i.text}{due}" + (f" — {owner}" if owner and owner != "you" else "")

    open_commitments = [i for i in items if i.type == "commitment" and i.status != "done"]
    return {
        "project": {"id": p.id, "name": p.name, "objective": p.objective, "status": p.brief},
        "last_call": (last.recap or {}).get("summary", "") if last else "",
        "open_questions": [i.text for i in items if i.type == "question" and i.status == "open"],
        "our_commitments": [fmt(i) for i in open_commitments if i.owner == "me"],
        "their_commitments": [fmt(i) for i in open_commitments if i.owner not in ("me", "")],
        "conflicts": [c.explanation for c in db.scalars(select(Conflict).where(Conflict.project_id == project_id,
                                                                                 Conflict.status == "open"))],
        "people": [{"name": x.name, "org": x.org, "role": x.role, "concerns": x.concerns or []}
                   for x in db.scalars(select(Person).where(Person.project_id == project_id))],
        "suggested_questions": [c.question for c in sorted(sig.criteria, key=lambda c: -c.importance) if c.status != "covered"][:4],
        "known": [i.text for i in items if i.type in ("fact", "requirement", "decision")][:12],
    }


FOLLOWUP_SYSTEM = """You write a short follow-up message on behalf of the user after a conversation.
Use only the recap provided. Plain, warm, professional. Structure: one line of thanks, what was agreed,
who does what by when, open questions. At most 150 words. No subject line. Sign off with the user's name."""


async def followup(db_session_id: int, user_id: int) -> str:
    """A follow-up message for a conversation, written for ``user_id`` to send."""
    from ..db import session_scope

    with session_scope() as db:
        row = db.get(SessionRow, db_session_id)
        if row is None:
            raise KeyError("session not found")
        user = db.get(User, user_id)
        recap = row.recap or {}
        notes = [p.text for p in db.scalars(select(PlaybookEntry).where(PlaybookEntry.user_id == user_id,
                                                                         PlaybookEntry.kind == "style",
                                                                         PlaybookEntry.active.is_(True)))]
        style = ("\nWrite in the user's style: " + "; ".join(notes)) if notes else ""
    lines = [f"Summary: {recap.get('summary', '')}"]
    for key, label in (("decisions", "Decided"), ("my_commitments", "I will"), ("their_commitments", "You/They will"),
                       ("open_questions", "Open"), ("next_steps", "Next steps")):
        for x in recap.get(key, []):
            lines.append(f"{label}: {x}")
    if not settings.llm_enabled:
        return "Thanks for your time today.\n\n" + "\n".join(lines[1:]) + f"\n\n{user.name if user else ''}"
    messages = [{"role": "system", "content": FOLLOWUP_SYSTEM + style},
                {"role": "user", "content": f"User's name: {user.name if user else ''}\nRECAP\n" + "\n".join(lines)}]
    text = []
    async for chunk in llm.chat_stream(settings.chat_model, messages, num_predict=300, temperature=0.4):
        text.append(chunk.content)
    return "".join(text).strip()
