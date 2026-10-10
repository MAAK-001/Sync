"""After a call: an instant draft recap, then a deep-model (T2) pass that produces the final record,
merges it into the project (answers, duplicates, conflicts, people) and refreshes the project brief."""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Literal

import numpy as np
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import access, bus
from .. import textparse as tp
from ..config import settings
from ..db import session_scope
from ..embed import embedder, from_blob, to_blob
from ..events import as_dict, changed, record
from ..guide import guide
from ..llm import llm
from ..models import Conflict, Item, Nudge, Person, Project, User, Utterance
from ..models import Session as SessionRow
from . import memory as memory_mod

log = logging.getLogger("sync.brain")
_tasks: set[asyncio.Task] = set()

ITEM_TYPES = Literal["decision", "commitment", "proposal", "requirement", "concern", "fact", "question", "risk"]


class CItem(BaseModel):
    type: ITEM_TYPES
    text: str
    status: Literal["open", "proposed", "conditional", "agreed", "done"] = "open"
    owner: str = ""
    due: str = ""
    condition: str = ""
    answer: str = ""
    speaker: str = ""


class CPerson(BaseModel):
    name: str
    org: str = ""
    role: str = ""
    concerns: list[str] = Field(default_factory=list)


class CAnswered(BaseModel):
    q: int
    answer: str


class Consolidation(BaseModel):
    summary: str
    items: list[CItem] = Field(default_factory=list)
    people: list[CPerson] = Field(default_factory=list)
    answered: list[CAnswered] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)
    brief: str = ""


class _ItemsOut(BaseModel):
    items: list[CItem] = Field(default_factory=list)


class _SummaryOut(BaseModel):
    summary: str
    next_steps: list[str] = Field(default_factory=list)
    people: list[CPerson] = Field(default_factory=list)
    answered: list[CAnswered] = Field(default_factory=list)
    brief: str = ""


# Two focused calls on one shared prompt prefix (the second reuses the model's cache): small models are far
# more accurate on one job at a time than on one big JSON object.
SYSTEM = """You are Sync's post-call analyst working for ONE user (marked YOU in the transcript).
Other speakers appear by name. Be exact about WHO said or promised WHAT.
- Only what was actually said. Never invent names, numbers or dates.
- A statement by YOU is the user's claim, not something the others agreed to.
- Keep numbers exactly as said (budgets, timelines, volumes, prices).
Reply with JSON only."""

TASK_ITEMS = """TASK ITEMS: list every important item from the TRANSCRIPT (at most 15), in order:
- requirement: something the other side needs ("must work with our legacy billing system")
- fact: a stated number or fact ("phase-1 budget is 25 lakh")
- decision: something explicitly agreed by both sides (one person's plan or errand is not a decision)
- commitment: who will do what (owner "me" for YOU, otherwise the person's name) and the due date if said
- proposal: an offer; keep its condition in "condition"
- concern: a worry someone raised; "speaker" is who raised it
- question: an important question; status "done" with the answer if answered, otherwise "open"
Use status "agreed" for commitments and decisions, "open" otherwise. Skip greetings and small talk.
Leave a field out when it is empty."""

TASK_SUMMARY = """TASK SUMMARY:
- summary: 2-3 sentences about this call, with correct attribution (who said what).
- next_steps: up to 5 concrete next steps. Name the owner only if the transcript says who will do it, otherwise
  write "(no owner yet)". Add a date only if that date was said; never make one up.
- people: the other participants (name, organisation, role, concerns they raised).
- answered: previously open questions (by number) that this call answered, with the answer.
- brief: where the whole project stands now, at most 5 short lines, using PROJECT MEMORY and this call;
  mention open questions and open conflicts. Anything listed under FLAGGED is unresolved, not agreed: mention
  the flagged problems in the summary when they were not resolved in the call."""


def _items(db: Session, session_id: int) -> list[Item]:
    return list(db.scalars(select(Item).where(Item.session_id == session_id, Item.status != "dropped")))


def draft_recap(db: Session, session_id: int, card: dict | None = None) -> dict:
    """Instant recap from what the live engine captured (shown while the deep pass runs)."""
    items = _items(db, session_id)

    def texts(cond) -> list[str]:
        return [i.text + (f" — {i.answer}" if i.answer else "") for i in items if cond(i)]

    return {
        "summary": "",
        "decisions": texts(lambda i: i.type == "decision"),
        "my_commitments": texts(lambda i: i.type == "commitment" and i.owner == "me"),
        "their_commitments": texts(lambda i: i.type == "commitment" and i.owner != "me"),
        "open_questions": texts(lambda i: i.type == "question" and i.status == "open"),
        "answered": texts(lambda i: i.type == "question" and i.status == "done"),
        "concerns": texts(lambda i: i.type == "concern"),
        "facts": texts(lambda i: i.type in ("fact", "requirement")),
        "proposals": texts(lambda i: i.type == "proposal"),
        "next_steps": (card or {}).get("say_back", []),
        "open_criteria": [c["text"] for c in (card or {}).get("open_criteria", [])],
        "asked_for": (card or {}).get("asked_for", []),
        "unowned": (card or {}).get("unowned", []),
        "flagged": [n.text for n in db.scalars(select(Nudge).where(Nudge.session_id == session_id,
                                                                  Nudge.gap_type.in_(FLAG_TYPES)).order_by(Nudge.id))],
        "answers": [n.text.removeprefix("Answer: ") for n in db.scalars(select(Nudge).where(
            Nudge.session_id == session_id, Nudge.gap_type == "G12").order_by(Nudge.id))],
    }


def schedule_consolidation(session_id: int) -> None:
    task = asyncio.create_task(consolidate(session_id))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


_EMPTY = {"due", "none", "n/a", "na", "null", "-", "unknown", "not specified", "tbd", "unspecified", "no", "owner",
          "not mentioned", "not stated"}


_PLACEHOLDER = re.compile(r"^(?:me|you|user|them|they|unknown|caller|customer|speaker(?:\s*[a-z0-9]{1,2})?|them-[a-z0-9])$", re.I)


def _you(text: str) -> str:
    """The transcript labels the user "YOU"; small models copy the capitals into what they write."""
    out = re.sub(r"\bYOU(R|RS)?\b", lambda m: "you" + (m.group(1) or "").lower(), text)
    return out[:1].upper() + out[1:] if out else out


# What Sync flagged live is kept in the record word for word (not left to the model to remember).
FLAG_TYPES = ("G7", "G8", "G13", "G14", "G15", "G16")
_DATE_BIT = re.compile(r"\s*(?:\(|,|-|—)?\s*\b(?:by|before|on|due|until)\s+(\d{1,2}(?:st|nd|rd|th)?\s+[A-Z][a-z]{2,8}|"
                       r"[A-Z][a-z]{2,8}\s+\d{1,2}(?:st|nd|rd|th)?|\d{1,2}/\d{1,2}(?:/\d{2,4})?)\)?", re.I)


_MON = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
_DATE_ANY = re.compile(rf"(?:\s*(?:,|\bby|\bbefore|\bon|\bdue|\buntil))?\s*\b(?:(\d{{1,2}})(?:st|nd|rd|th)?\s+{_MON}|"
                       rf"{_MON}\s+(\d{{1,2}})(?:st|nd|rd|th)?)\b", re.I)


def _no_invented_dates(step: str, transcript: str) -> str:
    """Small models like to add "by 15 Oct" or "(you, 10 Oct)". Keep a date only if that day and month were said."""
    said = {(int(d1 or d2), (m1 or m2)[:3].lower())
            for d1, m1, m2, d2 in re.findall(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?{_MON}|\b{_MON}\s+(\d{{1,2}})",
                                             transcript, re.I)}

    def keep(m: re.Match) -> str:
        day, mon = int(m.group(1) or m.group(4)), (m.group(2) or m.group(3))[:3].lower()
        return m.group(0) if (day, mon) in said else ""
    out = _DATE_ANY.sub(keep, step)
    return re.sub(r"\(\s*([^(),]*?)\s*,?\s*\)", lambda m: f"({m.group(1)})" if m.group(1) else "", out).strip()


def _fix_owners(steps: list[str], items: list[Item], names: dict[int, str], owner_id: int) -> list[str]:
    """A next step that restates a recorded commitment gets that commitment's owner (the model sometimes swaps them)."""
    commits = [i for i in items if i.type == "commitment"]
    if not commits or not steps:
        return steps
    actions = [re.sub(r"^(?:[\w ]{1,30}? asked you to|you will|[A-Z][\w-]* will)\s+", "", i.text, flags=re.I) for i in commits]
    actions = [re.sub(r"\s*\([^()]*\)\s*$", "", a) for a in actions]  # "(by Monday)"
    vecs = embedder.encode([re.sub(r"\s*\([^()]*\)\s*$", "", s) for s in steps])
    mat = embedder.encode(actions)
    out = []
    for step, v in zip(steps, vecs, strict=True):
        sims = mat @ v
        best = int(np.argmax(sims))
        if sims[best] >= 0.85:  # only clear restatements (look-alikes score up to ~0.7)
            it = commits[best]
            owner = "you" if it.owner in ("me", "") and it.user_id in (None, owner_id) else (it.owner or "")
            if owner:
                step = re.sub(r"\s*\((?:[^()]*)\)\s*$", "", step).strip() + f" ({owner})"
        out.append(step)
    return out


def _clean(value: str) -> str:
    v = (value or "").strip().strip(".")
    return "" if v.lower() in _EMPTY else v


def _transcript(db: Session, session_id: int, names: dict[str, str], max_words: int = 1900) -> str:
    utts = db.scalars(select(Utterance).where(Utterance.session_id == session_id).order_by(Utterance.seq)).all()

    def label(u: Utterance) -> str:
        return "YOU" if u.channel == "you" else names.get(u.speaker, f"THEM-{u.speaker}")

    lines = [f"[{u.seq}] {label(u)}: {u.text}" for u in utts]
    words = sum(len(x.split()) for x in lines)
    if words <= max_words:
        return "\n".join(lines)
    # Long call: keep the opening and the most recent part; live notes cover the middle.
    head, tail, budget = [], [], max_words
    for x in lines[:20]:
        head.append(x)
        budget -= len(x.split())
    for x in reversed(lines[20:]):
        if budget - len(x.split()) < 0:
            break
        tail.insert(0, x)
        budget -= len(x.split())
    return "\n".join(head + ["[… middle of the call summarised in LIVE NOTES …]"] + tail)


MIN_WORDS = 8  # below this there is nothing to summarise
NOTHING_HEARD = ("Sync didn't hear anything in this conversation, so nothing was filed. If people were talking, check "
                 "the microphone and speakers in Settings → Desktop & runtime.")
NEXT_TO_NOTHING = "Too little was said in this conversation to summarise. Nothing was filed beyond what's below."


async def consolidate(session_id: int) -> None:
    await llm.wait_until_idle()
    with session_scope() as db:
        row = db.get(SessionRow, session_id)
        if row is None:
            return
        heard = " ".join(db.scalars(select(Utterance.text).where(Utterance.session_id == session_id)))
        if len(heard.split()) < MIN_WORDS:
            # Nothing (or next to nothing) was said: a model given an empty transcript invents a meeting. Don't ask it.
            row.recap = {**(row.recap or {}), "summary": NOTHING_HEARD if not heard else NEXT_TO_NOTHING}
            row.recap_status = "refined"
            record(db, "recap.refined", actor="t2", user_id=row.user_id, entity="session", entity_id=session_id,
                   project_id=row.project_id, session_id=session_id, reason="too little was said to summarise")
            audience, project_id, owner_id = access.audience(db, row), row.project_id, row.user_id
            bus.publish({"type": "recap.refined", "session_id": session_id, "project_id": project_id,
                         "owner_id": owner_id}, audience)
            return
        row.recap_status = "refining"
        owner = db.get(User, row.user_id)
        owner_name = owner.name if owner else "the user"
        live_notes = "\n".join(f"- {i.type}: {i.text}" + (f" (owner {i.owner}, due {i.due})" if i.owner else "")
                               for i in _items(db, session_id)) or "(none)"
        _mem, _docs, memory_text = memory_mod.load(db, row.project_id, row.user_id)
        open_qs = []
        if row.project_id:
            open_qs = list(db.scalars(select(Item).where(Item.project_id == row.project_id, Item.type == "question",
                                                         Item.status == "open", Item.session_id != session_id,
                                                         Item.superseded_by.is_(None))))[-10:]
        names = (row.stats or {}).get("speakers", {})
        transcript = _transcript(db, session_id, names)
        project_id = row.project_id
        flagged = [f"- {n.text} ({n.why})" for n in db.scalars(select(Nudge).where(
            Nudge.session_id == session_id, Nudge.gap_type.in_(FLAG_TYPES + ("G12",))))]
    if not settings.llm_enabled:
        _finish(session_id, None)
        return
    oq = "\n".join(f"[{i + 1}] {q.text}" for i, q in enumerate(open_qs)) or "(none)"
    # Live notes only matter when the transcript had to be shortened (they cover the middle of long calls).
    notes = f"LIVE NOTES (captured during the call)\n{live_notes}\n\n" if "[… middle of the call" in transcript else ""
    flags = ("FLAGGED DURING THE CALL (Sync showed these privately to the user on screen; NOBODY said them aloud. They "
             "are contradictions, over-promises, concessions, tasks nobody owns, and answers from the user's documents "
             "and past calls. Never report them as said or agreed in the call)\n"
             + "\n".join(flagged) + "\n\n") if flagged else ""
    context = (f"THE USER (\"YOU\" in the transcript): {owner_name}\n{guide().profile()}\n\nPROJECT MEMORY\n{memory_text}\n\nPREVIOUSLY OPEN QUESTIONS\n{oq}\n\n"
               f"{notes}{flags}TRANSCRIPT\n{transcript}\n\n")

    def msgs(task: str) -> list[dict]:
        return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": context + task}]

    items, st1 = await llm.structured(settings.deep_model, msgs(TASK_ITEMS), _ItemsOut, num_predict=700, temperature=0.1)
    summary, st2 = await llm.structured(settings.deep_model, msgs(TASK_SUMMARY), _SummaryOut, num_predict=500,
                                        temperature=0.1)
    log.info("T2 session %s: items %.1fs (prefill %d, gen %d) + summary %.1fs (prefill %d, gen %d)", session_id,
             st1.wall_s, st1.prompt_tokens, st1.gen_tokens, st2.wall_s, st2.prompt_tokens, st2.gen_tokens)
    res = None
    if items is not None or summary is not None:
        res = Consolidation(summary=summary.summary if summary else "", items=items.items if items else [],
                            people=summary.people if summary else [], answered=summary.answered if summary else [],
                            next_steps=summary.next_steps if summary else [], brief=summary.brief if summary else "")
    _finish(session_id, res, open_qs_ids=[q.id for q in open_qs], project_id=project_id)
    from ..learning import reflect

    await reflect(session_id)


def _finish(session_id: int, res: Consolidation | None, open_qs_ids: list[int] | None = None,
            project_id: int | None = None) -> None:
    with session_scope() as db:
        row = db.get(SessionRow, session_id)
        if row is None:
            return
        if res is None:
            row.recap_status = "draft"
            return
        _reconcile(db, row, res)
        if row.project_id:
            _merge_into_project(db, row)
            for a in res.answered:
                if open_qs_ids and 1 <= a.q <= len(open_qs_ids) and a.answer.strip():
                    q = db.get(Item, open_qs_ids[a.q - 1])
                    before = as_dict(q)
                    q.answer, q.status = a.answer.strip(), "done"
                    b, af = changed(before, as_dict(q))
                    record(db, "item.updated", actor="t2", user_id=row.user_id, entity="item", entity_id=q.id, project_id=q.project_id,
                           session_id=session_id, before=b, after=af, reason="answered in a later conversation")
            _people(db, row, res.people)
            p = db.get(Project, row.project_id)
            if res.brief.strip():
                before = {"brief": p.brief}
                p.brief = res.brief.strip()
                record(db, "project.updated", actor="t2", user_id=row.user_id, entity="project", entity_id=p.id, project_id=p.id,
                       session_id=session_id, before=before, after={"brief": p.brief})
        draft = row.recap or {}
        recap = draft_recap(db, session_id, None)
        recap["asked_for"], recap["unowned"] = draft.get("asked_for", []), draft.get("unowned", [])
        recap["summary"] = _you(res.summary.strip())
        said = " ".join(u.text for u in db.scalars(select(Utterance).where(Utterance.session_id == session_id)))
        steps = [_no_invented_dates(_you(s), said) for s in res.next_steps if s.strip()][:6]
        recap["next_steps"] = _fix_owners(steps, _items(db, session_id), {}, row.user_id)
        recap["open_criteria"] = [c["text"] for c in (row.criteria or []) if c.get("status") != "covered"]
        recap["conflicts"] = [c.explanation for c in db.scalars(select(Conflict).where(Conflict.session_id == session_id))]
        before = {"recap_status": row.recap_status}
        row.recap, row.recap_status = recap, "refined"
        record(db, "recap.refined", actor="t2", user_id=row.user_id, entity="session", entity_id=session_id, project_id=row.project_id,
               session_id=session_id, before=before, after={"recap_status": "refined"})
        project_id = row.project_id
        audience = access.audience(db, row)
        owner_id = row.user_id
    bus.publish({"type": "recap.refined", "session_id": session_id, "project_id": project_id, "owner_id": owner_id},
                audience)


def _reconcile(db: Session, row: SessionRow, res: Consolidation) -> None:
    """Make the deep model's record the session's record, keeping deterministic facts (numbers) from T0."""
    live = _items(db, row.id)
    live_vecs = {i.id: from_blob(i.vec) for i in live if i.vec is not None}
    final = [c for c in res.items if c.text.strip()]
    user = db.get(User, row.user_id)
    me_names = {"me", "you", "user"} | ({user.name.strip().lower(), user.name.split()[0].lower()} if user and user.name.strip() else set())
    for c in final:
        c.text = _you(c.text)
        if user and user.name.strip():  # the model writes the user by name; the record says "you", as live items do
            c.text = re.sub(rf"^{re.escape(user.name.strip())}\b", "You", c.text)
    for c in final:  # tidy small-model output
        c.due, c.condition, c.answer = _clean(c.due), _clean(c.condition), _clean(c.answer)
        c.owner = _clean(c.owner)
        if c.type in ("commitment", "decision") and c.status in ("open", "proposed"):
            c.status = "agreed"
    vecs = embedder.encode([c.text for c in final]) if final else np.zeros((0, 384), dtype=np.float32)
    matched: set[int] = set()
    for c, v in zip(final, vecs, strict=True):
        best_id, best = None, 0.0
        for iid, lv in live_vecs.items():
            if iid in matched:
                continue
            it = db.get(Item, iid)
            if it.type != c.type and not {it.type, c.type} <= {"fact", "requirement"}:
                continue
            s = float(lv @ v)
            if s > best:
                best_id, best = iid, s
        ev: list[int] = []
        owner = "me" if c.owner.strip().lower() in me_names else c.owner.strip()
        if best_id is not None and best >= 0.78:
            it = db.get(Item, best_id)
            before = as_dict(it)
            if not it.slot:  # keep exact T0 wording for numbers
                it.text = c.text.strip()
            it.status = c.status or it.status
            it.owner = owner or it.owner
            it.due = c.due or it.due
            it.condition = c.condition or it.condition
            it.answer = c.answer or it.answer
            it.evidence = sorted(set(it.evidence or []) | set(ev))
            it.confidence = max(it.confidence, 0.85)
            it.vec = to_blob(v) if not it.slot else it.vec
            b, a = changed(before, as_dict(it))
            if a:
                record(db, "item.updated", actor="t2", user_id=row.user_id, entity="item", entity_id=it.id, project_id=it.project_id,
                       session_id=row.id, before=b, after=a, reason="post-call review")
            matched.add(best_id)
        else:
            it = Item(project_id=row.project_id, session_id=row.id, user_id=row.user_id, type=c.type, text=c.text.strip(), status=c.status,
                      speaker=c.speaker or owner, owner=owner, due=c.due, condition=c.condition, answer=c.answer,
                      evidence=ev, confidence=0.8, created_by="t2", vec=to_blob(v))
            db.add(it)
            db.flush()
            record(db, "item.created", actor="t2", user_id=row.user_id, entity="item", entity_id=it.id, project_id=row.project_id,
                   session_id=row.id, after=as_dict(it), reason="post-call review")
    for it in live:
        answered_q = it.type == "question" and it.status == "done"  # small-talk questions the review didn't keep
        if it.id not in matched and not it.slot and (it.confidence < 0.7 or answered_q) and final:
            before = {"status": it.status}
            it.status = "dropped"
            record(db, "item.updated", actor="t2", user_id=row.user_id, entity="item", entity_id=it.id, project_id=it.project_id,
                   session_id=row.id, before=before, after={"status": "dropped"}, reason="not confirmed by post-call review")


def _merge_into_project(db: Session, row: SessionRow) -> None:
    """Supersede duplicates from earlier calls and record slot conflicts (e.g. two different budgets)."""
    new = _items(db, row.id)
    names = {u.id: u.name for u in db.scalars(select(User))}
    old = list(db.scalars(select(Item).where(Item.project_id == row.project_id, Item.session_id != row.id,
                                             Item.superseded_by.is_(None), Item.status != "dropped")))
    for n in new:
        nv = from_blob(n.vec)
        for o in old:
            if o.superseded_by is not None:
                continue
            if n.slot and o.slot == n.slot and n.value and o.value:
                if tp.conflicting(o.value, n.value):
                    exists = db.scalar(select(Conflict).where(Conflict.item_a == o.id, Conflict.item_b == n.id))
                    if not exists:
                        expl = (f"{n.slot.replace('_', ' ')}: {o.value.get('text')} "
                                f"({memory_mod.who(o, row.user_id, names)}, {memory_mod.day(o.created_at)}) vs "
                                f"{n.value.get('text')} ({memory_mod.who(n, row.user_id, names)}, "
                                f"{memory_mod.day(n.created_at)})")
                        cf = Conflict(project_id=row.project_id, session_id=row.id, item_a=o.id, item_b=n.id,
                                      slot=n.slot, explanation=expl)
                        db.add(cf)
                        db.flush()
                        record(db, "conflict.detected", actor="t2", user_id=row.user_id, entity="conflict", entity_id=cf.id,
                               project_id=row.project_id, session_id=row.id, after={"explanation": expl})
                continue
            ov = from_blob(o.vec)
            if nv is None or ov is None or o.type != n.type:
                continue
            if float(nv @ ov) >= 0.9:
                o.superseded_by = n.id
                record(db, "item.superseded", actor="t2", user_id=row.user_id, entity="item", entity_id=o.id, project_id=row.project_id,
                       session_id=row.id, before={"superseded_by": None}, after={"superseded_by": n.id},
                       reason="restated in a later conversation")


def _people(db: Session, row: SessionRow, people: list[CPerson]) -> None:
    """Add the people heard in this conversation to its project (or fill in what was missing)."""
    for cp in people:
        name = cp.name.strip()
        if not name or _PLACEHOLDER.match(name):
            continue  # "Speaker B", "them", "unknown"… are labels for voices, not people
        cp.org, cp.role = _clean(cp.org), _clean(cp.role)
        person = db.scalar(select(Person).where(Person.project_id == row.project_id, Person.name.ilike(name)))
        if person is None:
            person = Person(project_id=row.project_id, name=name, org=cp.org, role=cp.role, concerns=[])
            db.add(person)
            db.flush()
            record(db, "person.added", actor="t2", user_id=row.user_id, entity="person", entity_id=person.id,
                   project_id=row.project_id, session_id=row.id, after={"name": name, "org": cp.org, "role": cp.role})
        else:
            person.org = person.org or cp.org
            person.role = person.role or cp.role
        person.concerns = list(dict.fromkeys([*(person.concerns or []), *[c for c in cp.concerns if c.strip()]]))[:8]
