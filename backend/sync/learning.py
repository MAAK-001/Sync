"""How Sync learns the user: transparent playbook entries derived from outcomes (no model training).

* gap-type weights (in GapStat) adapt the gate after every nudge outcome;
* the playbook records habits ("often leaves X open"), questions that worked, and nudge types to avoid.
The playbook is shown to the user (editable) and fed to the live model's prompt.
"""
from __future__ import annotations

from collections import Counter

from rapidfuzz import fuzz
from sqlalchemy import select

from .db import session_scope
from .events import record
from .models import GapStat, Nudge, PlaybookEntry
from .models import Session as SessionRow

GAP_LABEL = {
    "G1": "unanswered-question", "G2": "pin-down-vague-words", "G3": "explore-concern", "G4": "missing-topic",
    "G5": "commitment-date", "G6": "condition", "G7": "contradiction", "G8": "over-promise", "G9": "already-known",
    "G10": "parked-topic",
}


def _add(db, user_id: int, kind: str, text: str, evidence: list, session_id: int) -> bool:
    existing = db.scalars(select(PlaybookEntry).where(PlaybookEntry.user_id == user_id)).all()
    if any(fuzz.token_set_ratio(e.text, text) >= 85 for e in existing):
        return False
    e = PlaybookEntry(user_id=user_id, kind=kind, text=text, evidence=evidence)
    db.add(e)
    db.flush()
    record(db, "playbook.updated", actor="system", user_id=user_id, entity="playbook", entity_id=e.id,
           session_id=session_id, after={"kind": kind, "text": text}, reason="learned from outcomes")
    return True


async def reflect(session_id: int) -> None:
    with session_scope() as db:
        row = db.get(SessionRow, session_id)
        if row is None or row.mode == "script":
            return  # sample calls are demonstrations, not the user's behaviour
        uid = row.user_id
        # 1. Goals the user tends to leave open (2 of their last 3 conversations).
        recent = db.scalars(select(SessionRow).where(SessionRow.user_id == uid, SessionRow.status == "ended",
                                                     SessionRow.mode != "script")
                            .order_by(SessionRow.id.desc()).limit(3)).all()
        open_counts: Counter = Counter()
        texts = {}
        for s in recent:
            for c in s.criteria or []:
                if c.get("status") != "covered":
                    open_counts[c["id"]] += 1
                    texts[c["id"]] = c["text"]
        for cid, n in open_counts.items():
            if n >= 2:
                _add(db, uid, "habit", f"Often ends calls without: {texts[cid].lower()} — raise it earlier.",
                     [s.id for s in recent], session_id)
        # 2. Suggestions the user actually used become reusable questions.
        for n in db.scalars(select(Nudge).where(Nudge.session_id == session_id, Nudge.outcome == "used",
                                                Nudge.kind == "ask")):
            _add(db, uid, "effective_question", f"Asked when suggested: “{n.text.split(':', 1)[-1].strip()}”",
                 [n.id], session_id)
        # 3. Nudge types the user keeps dismissing.
        for g in db.scalars(select(GapStat).where(GapStat.user_id == uid)):
            if g.dismissed >= 3 and g.used + g.useful == 0:
                _add(db, uid, "avoid", f"Prefers fewer {GAP_LABEL.get(g.gap_type, g.gap_type)} nudges.",
                     [g.gap_type], session_id)
