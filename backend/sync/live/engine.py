"""LiveSession: the runtime for one conversation.

utterance -> redact -> embed -> store -> T0 signals -> gate -> (maybe) one nudge
                                     +-> T1 jobs (small model, one at a time, never blocking T0)
Events go to every subscriber (overlay, UI, test client) as plain dicts.
"""
from __future__ import annotations

import asyncio
import re
import hashlib
import itertools
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import numpy as np
from sqlalchemy import select

from .. import access, bus
from .. import textparse as tp
from ..brain import memory as memory_mod
from ..config import settings
from ..db import session_scope
from ..embed import embedder, to_blob
from ..events import as_dict, changed, record
from ..guide import guide
from ..llm import llm
from ..models import GapStat, Item, Nudge, PlaybookEntry, Project, User, Utterance
from ..models import Session as SessionRow
from ..privacy import redact
from . import prompts
from .gate import Gate, TimingContext, learned_weight
from .signals import Candidate, Draft, Signals, Utt

log = logging.getLogger("sync.live")

_live: dict[int, LiveSession] = {}
# Near the end of a call these gaps are listed on the wrap-up card rather than nudged one by one.
WRAP_FOLDED = {"G1", "G4", "G5", "G10", "G11", "G15"}
# Live events that also go out on the app-wide bus: nudges only to the person on the call (their on-screen
# overlay); progress events also to the members of the conversation's project (their open app pages).
TO_OWNER = {"nudge", "nudge.outcome", "wrapup"}
TO_AUDIENCE = {"ended", "project.linked"}


def get_live(session_id: int) -> LiveSession | None:
    return _live.get(session_id)


async def start_live(session_id: int, speed: float = 1.0) -> LiveSession:
    if session_id in _live:
        return _live[session_id]
    s = LiveSession(session_id, speed=speed)
    await s.load()
    _live[session_id] = s
    s.start_background()
    return s


class T1Queue:
    """One small-model job at a time, highest priority first. T0 never waits on it."""

    def __init__(self) -> None:
        self.q: asyncio.PriorityQueue = asyncio.PriorityQueue()
        self.count = itertools.count()
        self.busy = False
        self.task: asyncio.Task | None = None
        self.pending_names: set[str] = set()

    def submit(self, prio: int, name: str, job: Callable[[], Awaitable[None]], unique: bool = True) -> None:
        if not settings.llm_enabled or (unique and name in self.pending_names):
            return
        self.pending_names.add(name)
        self.q.put_nowait((prio, next(self.count), name, job))

    def idle(self) -> bool:
        return not self.busy and self.q.empty()

    async def run(self) -> None:
        while True:
            _prio, _n, name, job = await self.q.get()
            self.pending_names.discard(name)
            self.busy = True
            try:
                await job()
            except Exception:  # a failed model job must never break the call
                log.exception("T1 job %s failed", name)
            finally:
                self.busy = False


class LiveSession:
    T_ANSWER = 0.70  # a document sentence or past item this close to the question answers it (bge-small)

    def __init__(self, session_id: int, speed: float = 1.0):
        self.id = session_id
        self.speed = speed
        self.t0 = time.monotonic()
        self.subscribers: set[asyncio.Queue] = set()
        self.lock = asyncio.Lock()
        self.t1 = T1Queue()
        self.tasks: list[asyncio.Task] = []
        self.running = True
        self.seq = 0
        self.utts: dict[int, Utt] = {}
        self.lines: list[str] = []
        self.items: dict[str, int] = {}  # draft key -> item id
        self.item_vecs: dict[int, tuple[str, np.ndarray]] = {}  # item id -> (type, vec) for dedupe
        self.shown: list[dict] = []  # nudges shown: {id, kind, gap_type, t, vec, outcome}
        self.sharpened: dict[str, str] = {}
        self.wrapping = False
        self.you_speaking = self.them_speaking = False
        self.last_you_end = self.last_utt_end = -1e9
        self.last_topic_shift = -1e9
        self.last_nextq_t = 0.0
        self.nextq_seq = 0
        self.show_nudges = True  # False for pasted text: build the record, don't nudge
        self.audio = None  # AudioPipeline when this session receives raw audio
        self.stats: dict = {"t1_calls": 0, "t1_seconds": 0.0, "nudges": 0}
        self.looked_up: set[str] = set()  # questions already answered from documents/memory
        self.cards_shown: set[str] = set()  # source sentences already shown as answer cards
        self.agenda = ""

    # ------------------------------------------------------------ setup

    def now(self) -> float:
        return (time.monotonic() - self.t0) * self.speed

    async def load(self) -> None:
        with session_scope() as db:
            row = db.get(SessionRow, self.id)
            if row is None:
                raise KeyError(f"session {self.id} not found")
            user = db.get(User, row.user_id)
            self.user_id = row.user_id
            self.learns = row.mode != "script"  # sample calls don't change what Sync learned about the user
            self.user_name = user.name if user else ""
            self.mode = user.nudge_mode if user else "normal"
            self.project_id = row.project_id
            self.title, self.caller, self.app = row.title, row.caller, row.app
            self.started_at = row.started_at.isoformat()
            project = db.get(Project, row.project_id) if row.project_id else None
            self.agenda = " ".join(x for x in (row.objective, project.objective if project else "") if x)
            weights = {
                g.gap_type: learned_weight(g.shown, g.used, g.useful, g.dismissed)
                for g in db.scalars(select(GapStat).where(GapStat.user_id == row.user_id))
            }
            playbook = [p.text for p in db.scalars(select(PlaybookEntry).where(PlaybookEntry.user_id == row.user_id,
                                                                                 PlaybookEntry.active.is_(True)))]
            memory, self.docs, self.memory_text = memory_mod.load(db, self.project_id, self.user_id)
        self.gate = Gate(mode=self.mode, weights=weights)
        self.signals = await asyncio.to_thread(Signals, memory, embedder.encode, 1800.0, self.agenda)
        first = re.match(r"\s*([A-Z][a-z]{1,30})\b", self.caller or "")
        if first:  # a known caller ("Casey", "Priya (Acme)") is the first remote voice until someone else speaks
            self.signals.names.setdefault("A", first.group(1))
        self.memory = memory
        self.t1_system = prompts.system_prompt(guide().profile(), playbook)
        with session_scope() as db:
            row = db.get(SessionRow, self.id)
            row.criteria = self.signals.criteria_state()
            row.status = "live"

    def start_background(self) -> None:
        llm.live_sessions += 1
        self.tasks.append(asyncio.create_task(self.t1.run()))
        self.tasks.append(asyncio.create_task(self._ticker()))
        self.t1.submit(0, "warm", self._job_warm)

    # ------------------------------------------------------------ events

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self.subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self.subscribers.discard(q)

    def publish(self, event: dict) -> None:
        event = {"session_id": self.id, "owner_id": self.user_id, "t": round(self.now(), 2), **event}
        for q in list(self.subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass
        if event["type"] in TO_OWNER:
            bus.publish(event, {self.user_id})
        elif event["type"] in TO_AUDIENCE:
            with session_scope() as db:
                bus.publish(event, {self.user_id} | access.member_ids(db, self.project_id))

    def snapshot(self) -> dict:
        """Everything a newly connected viewer needs."""
        return {
            "type": "state",
            "status": "wrapping" if self.wrapping else "live",
            "owner_id": self.user_id,
            "project_id": self.project_id,
            "criteria": self.signals.criteria_state(),
            "speakers": self.signals.names,
            "transcript": [{"id": u.id, "seq": u.seq, "channel": u.channel, "speaker": u.speaker, "text": u.text,
                            "t": round(u.t_start, 1)} for u in list(self.utts.values())[-80:]],
            "items": [{"id": i.id, "type": i.type, "text": i.text, "status": i.status, "owner": i.owner, "due": i.due}
                      for i in self._items()],
            "started_at": self.started_at,
            "title": self.title, "app": self.app, "caller": self.caller,
        }

    # ------------------------------------------------------------ input

    def speech(self, channel: str, active: bool) -> None:
        """VAD state from the audio pipeline: lets the gate avoid talking over the user."""
        if channel == "you":
            self.you_speaking = active
            if not active:
                self.last_you_end = self.now()
        else:
            self.them_speaking = active

    async def add_utterance(self, channel: str, text: str, *, speaker: str = "", t_start: float | None = None,
                            t_end: float | None = None) -> Utt | None:
        text = redact(" ".join(text.split()))
        if not text or not self.running:
            return None
        channel = "you" if channel == "you" else "them"
        if channel == "them" and not speaker:
            speaker = "A"
        if channel == "you":
            speaker = ""
        async with self.lock:
            now = self.now()
            t_end = now if t_end is None else t_end
            t_start = t_end if t_start is None else t_start
            vec = await asyncio.to_thread(embedder.encode1, text)
            self.seq += 1
            with session_scope() as db:
                row = Utterance(session_id=self.id, seq=self.seq, channel=channel, speaker=speaker, text=text,
                                t_start=t_start, t_end=t_end, vec=to_blob(vec))
                db.add(row)
                db.flush()
                uid = row.id
            u = Utt(id=uid, seq=self.seq, channel=channel, speaker=speaker, text=text, t_start=t_start, t_end=t_end, vec=vec)
            self.utts[uid] = u
            label = "YOU" if channel == "you" else f"THEM-{speaker}"
            self.lines.append(f"[{u.seq}] {label}: {text}")
            self.last_utt_end = max(self.last_utt_end, t_end)
            if channel == "you":
                self.last_you_end = max(self.last_you_end, t_end)
            self.publish({"type": "transcript", "id": uid, "seq": u.seq, "channel": channel, "speaker": speaker,
                          "t": round(t_start, 1),
                          "name": self.signals.names.get(speaker, ""), "text": text})

            out = await asyncio.to_thread(self.signals.process, u, now)
            if out.names:
                self.publish({"type": "speakers", "speakers": self.signals.names})
            if out.topic_shift:
                self.last_topic_shift = now
            for d in out.drafts:
                await self._upsert(d, actor="t0")
            if out.criteria_changed:
                self.publish({"type": "criteria", "criteria": self.signals.criteria_state()})
            self.gate.resolve(out.answered)
            for c in out.candidates:
                if c.key in self.sharpened:
                    c.text = self.sharpened[c.key]
            if out.closing:
                self.wrapping = True
            cands = out.candidates
            if self.wrapping:  # at the close, these gaps go on one wrap-up card instead of separate nudges
                cands = [c for c in cands if c.gap_type not in WRAP_FOLDED]
                self.gate.pending = {k: c for k, c in self.gate.pending.items() if c.gap_type not in WRAP_FOLDED}
            self.gate.offer(cands)
            if self.show_nudges:
                for key, query, qu in out.lookups:
                    card = await asyncio.to_thread(self._answer_card, key, query, qu)
                    if card is not None:
                        cands.append(card)
                        self.gate.offer([card])
            for key, sentence in out.concern_sharpen:
                self.t1.submit(2, f"sharpen:{key}", lambda k=key, s=sentence: self._job_sharpen(k, s))
            for cu, sentence in out.claim_checks:
                self.t1.submit(0, f"claim:{cu.id}", lambda cu=cu, s=sentence: self._job_claim(cu, s), unique=False)
            if self.wrapping:
                self._publish_card()
            if channel == "you":
                await self._detect_used(u)
            await self._tick()
            return u

    # ------------------------------------------------------------ ledger

    async def _upsert(self, d: Draft, actor: str) -> None:
        """Create or update a live item; near-duplicates (same type, similar text) merge."""
        vec = await asyncio.to_thread(embedder.encode1, d.text)
        item_id = self.items.get(d.key)
        if item_id is None:
            for iid, (typ, v) in self.item_vecs.items():
                if typ == d.type and float(v @ vec) >= 0.88:
                    item_id = iid
                    break
        with session_scope() as db:
            if item_id is not None:
                it = db.get(Item, item_id)
                before = as_dict(it)
                for f in ("status", "owner", "due", "condition", "answer", "slot"):
                    val = getattr(d, f)
                    if val and val != getattr(it, f):
                        setattr(it, f, val)
                if d.value:
                    it.value = d.value
                it.evidence = sorted(set(it.evidence or []) | set(d.evidence))
                b, a = changed(before, as_dict(it))
                if not a or set(a) == {"evidence"}:
                    self.items[d.key] = item_id
                    return
                record(db, "item.updated", user_id=self.user_id, actor=actor, entity="item", entity_id=it.id, project_id=it.project_id,
                       session_id=self.id, before=b, after=a)
            else:
                it = Item(project_id=self.project_id, session_id=self.id, user_id=self.user_id, type=d.type, text=d.text, status=d.status,
                          speaker=d.speaker, owner=d.owner, due=d.due, condition=d.condition, answer=d.answer,
                          slot=d.slot, value=d.value or {}, evidence=d.evidence, quote=d.quote,
                          confidence=d.confidence, created_by=actor, vec=to_blob(vec))
                db.add(it)
                db.flush()
                record(db, "item.created", user_id=self.user_id, actor=actor, entity="item", entity_id=it.id, project_id=self.project_id,
                       session_id=self.id, after=as_dict(it))
                self.item_vecs[it.id] = (d.type, vec)
            self.items[d.key] = it.id
            payload = as_dict(it)
        self.publish({"type": "item", "item": payload})

    # ------------------------------------------------------------ nudges

    def _ctx(self) -> TimingContext:
        return TimingContext(now=self.now(), you_speaking=self.you_speaking, them_speaking=self.them_speaking,
                             last_you_end=self.last_you_end, last_utt_end=self.last_utt_end,
                             last_topic_shift=self.last_topic_shift, wrapping=self.wrapping)

    async def _tick(self) -> None:
        if not self.show_nudges:
            return
        picked = self.gate.tick(self._ctx())
        if picked:
            await self._show(*picked)

    async def _show(self, c: Candidate, score: float) -> None:
        now = self.now()
        trigger_end = max((self.utts[e].t_end for e in c.evidence if e in self.utts), default=c.t)
        latency = max(0.0, now - trigger_end)
        core = c.text.split(":", 1)[-1].strip()
        vec = await asyncio.to_thread(embedder.encode1, core)
        with session_scope() as db:
            n = Nudge(session_id=self.id, tier=c.tier, kind=c.kind, gap_type=c.gap_type, key=c.key, text=c.text,
                      why=c.why, source_ref=c.source_ref, score=round(score, 3), t=round(now, 2), latency=round(latency, 2))
            db.add(n)
            db.flush()
            nid = n.id
            if self.learns:
                stat = db.get(GapStat, (self.user_id, c.gap_type)) or GapStat(user_id=self.user_id, gap_type=c.gap_type)
                stat.shown = (stat.shown or 0) + 1
                db.add(stat)
            record(db, "nudge.shown", user_id=self.user_id, actor=c.tier, entity="nudge", entity_id=nid, project_id=self.project_id,
                   session_id=self.id, after={"text": c.text, "gap_type": c.gap_type, "score": round(score, 3)})
        self.shown.append({"id": nid, "kind": c.kind, "gap_type": c.gap_type, "t": now, "vec": vec, "outcome": "shown"})
        self.stats["nudges"] += 1
        self.publish({"type": "nudge", "id": nid, "kind": c.kind, "gap_type": c.gap_type, "tier": c.tier,
                      "text": c.text, "why": c.why, "source_ref": c.source_ref, "ttl": 20, "latency": round(latency, 2)})

    async def _detect_used(self, u: Utt) -> None:
        for n in self.shown:
            if n["outcome"] != "shown" or n["kind"] not in ("ask", "confirm") or u.t_end - n["t"] > 90:
                continue
            if float(u.vec @ n["vec"]) >= 0.6:
                await self.feedback(n["id"], "used", actor="t0")

    async def feedback(self, nudge_id: int, action: str, actor: str = "user") -> None:
        if action not in ("used", "useful", "dismissed"):
            return
        with session_scope() as db:
            n = db.get(Nudge, nudge_id)
            if n is None or n.session_id != self.id:
                return
            before = n.outcome
            n.outcome = action
            if self.learns:
                stat = db.get(GapStat, (self.user_id, n.gap_type)) or GapStat(user_id=self.user_id, gap_type=n.gap_type)
                setattr(stat, action, (getattr(stat, action) or 0) + 1)
                db.add(stat)
            record(db, f"nudge.{action}", user_id=self.user_id, actor=actor, entity="nudge", entity_id=nudge_id, project_id=self.project_id,
                   session_id=self.id, before={"outcome": before}, after={"outcome": action})
        for n in self.shown:
            if n["id"] == nudge_id:
                n["outcome"] = action
        self.publish({"type": "nudge.outcome", "id": nudge_id, "outcome": action})

    # ------------------------------------------------------------ background

    async def _ticker(self) -> None:
        # Every 0.5 s of conversation time (faster in wall-clock time when a script is replayed at speed).
        interval = max(0.02, 0.5 / self.speed)
        while self.running:
            await asyncio.sleep(interval)
            try:
                async with self.lock:
                    await self._tick()
                self._schedule_t1()
            except Exception:
                log.exception("ticker error")

    def _schedule_t1(self) -> None:
        if not self.t1.idle() or self.seq == 0:
            return
        now = self.now()
        if not self.wrapping and now - self.last_nextq_t >= 45 and self.seq - self.nextq_seq >= 5:
            self.last_nextq_t, self.nextq_seq = now, self.seq
            self.t1.submit(1, "next_question", self._job_next_question)

    # ------------------------------------------------------------ T1 jobs (small model)

    def t1_messages(self, task: str) -> list[dict]:
        names = self.signals.names
        speakers = (" Speakers: " + ", ".join(f"THEM-{k} = {v}" for k, v in names.items()) + ".") if names else ""
        content = f"PROJECT MEMORY\n{self.memory_text}\n\nTRANSCRIPT\n" + "\n".join(self.lines) + f"\n\nTASK: {task}{speakers}"
        return [{"role": "system", "content": self.t1_system}, {"role": "user", "content": content}]

    async def _call(self, task: str, schema, num_predict: int):
        res, st = await llm.structured(settings.live_model, self.t1_messages(task), schema, num_predict=num_predict)
        self.stats["t1_calls"] += 1
        self.stats["t1_seconds"] += st.wall_s
        log.info("T1 %s: %.1fs (prefill %d tok %.1fs, gen %d tok %.1fs)", task.split()[0], st.wall_s,
                 st.prompt_tokens, st.prompt_s, st.gen_tokens, st.gen_s)
        return res

    async def _job_warm(self) -> None:
        st = await llm.warm(settings.live_model, self.t1_messages("WARMUP"))
        log.info("T1 warm-up: %.1fs (%d prompt tokens)", st.wall_s, st.prompt_tokens)

    async def _job_claim(self, u: Utt, sentence: str) -> None:
        """Promise Guard for claims without numbers: the model only judges; the nudge quotes the document."""
        vec = await asyncio.to_thread(embedder.encode1, sentence)
        hits = self.docs.search(vec, k=3, min_score=0.45)
        if not hits:
            return
        # Rank individual sentences (a chunk can match on one shared word, e.g. "billing").
        cands = [(s, cid, ref) for cid, text, ref, _ in hits for s in (tp.sentences(text) or [text])]
        svecs = await asyncio.to_thread(embedder.encode, [s for s, _, _ in cands])
        order = np.argsort(-(svecs @ vec))[:2]
        for i in order:
            source, chunk_id, ref = cands[int(i)]
            res = await self._call(f'CHECK_CLAIM CLAIM="{sentence}" SOURCE="{source}"', prompts.ClaimCheck, 12)
            if res is None or res.verdict == "consistent":
                return  # a source supports the claim: nothing to flag
            if res.verdict == "contradicts":
                break
        else:
            return  # inconclusive: stay silent (grounded-or-silent)
        cand = Candidate(key=f"G8:c{chunk_id}", gap_type="G8", kind="caution",
                         text=f"Caution: {tp.short(source, 14)} [{ref}]",
                         why=f"You said: “{tp.short(sentence, 12)}”", importance=5, confidence=0.85, t=self.now(),
                         evidence=[u.id], source_ref=f"doc:{chunk_id}", ttl=45, tier="t1")
        async with self.lock:
            self.gate.offer([cand])
            await self._tick()

    async def _job_sharpen(self, key: str, sentence: str) -> None:
        res = await self._call(f'SHARPEN question="What worries you most here?" context: "{sentence}"', prompts.Sharpened, 30)
        if res and res.question and len(res.question.split()) <= 16:
            text = "Ask: " + res.question.strip().rstrip("?") + "?"
            self.sharpened[key] = text
            async with self.lock:
                if key in self.gate.pending:
                    self.gate.pending[key].text = text
                    self.gate.pending[key].tier = "t1"

    async def _job_next_question(self) -> None:
        open_c = self.signals.open_goals()
        if not open_c:
            return
        goals = "; ".join(f"[{c.id}] {c.text}" for c in sorted(open_c, key=lambda c: -c.importance)[:5])
        res = await self._call(f"NEXT_QUESTION open goals: {goals}", prompts.NextQuestion, 40)
        if res is None or not res.question.strip():
            return
        if res.criterion not in {c.id for c in open_c}:
            return  # the model must pick one of the offered goals, not improvise a topic
        q = res.question.strip().rstrip("?") + "?"
        if len(q.split()) > 16:
            return
        qvec = await asyncio.to_thread(embedder.encode1, q)
        asked = [x.vec for x in self.signals.open_qs] + [k.vec for k in self.memory.known if k.vec is not None]
        if asked and float(np.max(np.vstack(asked) @ qvec)) >= 0.82:
            return  # already asked or already known
        crit = next((c for c in open_c if c.id == res.criterion), None)
        cand = Candidate(key=f"T1Q:{hashlib.md5(q.lower().encode()).hexdigest()[:8]}", gap_type="G4", kind="ask",
                         text=f"Ask: {q}", why=f"Not covered yet: {crit.text}" if crit else "Suggested next question",
                         importance=crit.importance if crit else 4, confidence=0.75, t=self.now(), evidence=[],
                         ttl=90, tier="t1", meta={"criterion": res.criterion})
        async with self.lock:
            self.gate.offer([cand])

    async def catchup(self) -> list[str]:
        if settings.llm_enabled:
            done = asyncio.Event()
            result: list[str] = []

            async def job():
                res = await self._call("CATCHUP", prompts.Catchup, 60)
                if res:
                    result.extend(res.lines[:2])
                done.set()

            self.t1.submit(1, "catchup", job)
            try:
                await asyncio.wait_for(done.wait(), timeout=25)
            except TimeoutError:
                pass
            if result:
                return result
        recent = list(self.utts.values())[-4:]
        return [f"{'You' if u.channel == 'you' else self.signals.names.get(u.speaker, 'They')}: {tp.short(u.text, 14)}" for u in recent[-2:]]

    # ------------------------------------------------------------ answer cards (G12)

    def _answer_card(self, key: str, query: str, u: Utt) -> Candidate | None:
        """Their question (or the moment you stall) answered from your documents and past calls, instantly and only
        when a source clearly matches. Prices per seat/user also get the sum done ("15 × $1,200 × 6/12 = $9,000")."""
        if key in self.looked_up:
            return None
        self.looked_up.add(key)
        qvec = embedder.encode1(query)
        cands: list[tuple[str, str, str]] = []  # (sentence, how to cite it, source ref)
        for cid, text, ref, _ in self.docs.search(qvec, k=4, min_score=0.5):
            cands += [(sent, f"[{ref}]", f"doc:{cid}") for sent in (tp.sentences(text) or [text])]
        for k, _ in self.memory.nearest(qvec, k=4):
            cands.append((k.text + (f" — {k.answer}" if k.answer else ""), f"— {k.who}, {k.when}", f"item:{k.item_id}"))
        if not cands:
            return None
        scores = embedder.encode([c[0] for c in cands]) @ qvec
        want = [q for q in tp.quantities(query) if q.kind == "count"]
        best = None
        for (sent, cite, ref), sc in zip(cands, scores, strict=True):
            sc = float(sc)
            for w in want:  # "for 50 users" picks "50-99 users", not "1-49 users"
                for q in tp.quantities(sent):
                    if q.kind == "count" and q.unit == w.unit:
                        sc += 0.05 if q.lo <= w.lo <= q.hi else -0.05
            if best is None or sc > best[0]:
                best = (sc, sent, cite, ref)
        if best is None or best[0] < self.T_ANSWER or best[1] in self.cards_shown:
            return None
        self.cards_shown.add(best[1])
        transcript = [x.text for x in list(self.utts.values())[-6:]]
        math = tp.quick_math(query, [best[1], *transcript])
        text = f"Answer: {math} — {tp.short(best[1], 18)} {best[2]}" if math else f"Answer: {tp.short(best[1], 24)} {best[2]}"
        return Candidate(key=f"G12:{key}", gap_type="G12", kind="answer", text=text,
                         why="From your documents and past calls" if not math else "Worked out from your price list",
                         importance=5, confidence=0.9, t=self.now(), evidence=[u.id], source_ref=best[3], ttl=45)

    # ------------------------------------------------------------ private questions

    async def ask(self, question: str) -> dict:
        """Answer a private question instantly from memory, this call and documents (retrieval, no model)."""
        vec = await asyncio.to_thread(embedder.encode1, question)
        hits: list[dict] = []
        for k, score in self.memory.nearest(vec, k=4):
            if score >= 0.55:
                text = f"{k.text}" + (f" — {k.answer}" if k.answer else "")
                hits.append({"source": f"{k.who}, {k.when}", "text": text, "score": round(score, 3), "ref": f"item:{k.item_id}"})
        for u in self.utts.values():
            s = float(u.vec @ vec)
            if s >= 0.55:
                who = "You" if u.channel == "you" else self.signals.names.get(u.speaker, f"Speaker {u.speaker}")
                hits.append({"source": f"{who}, this call", "text": u.text, "score": round(s, 3), "ref": f"utt:{u.id}"})
        for cid, text, ref, s in self.docs.search(vec, k=3, min_score=0.5):
            hits.append({"source": ref, "text": text, "score": round(s, 3), "ref": f"doc:{cid}"})
        hits.sort(key=lambda h: -h["score"])
        return {"question": question, "answers": hits[:5]}

    # ------------------------------------------------------------ project (chosen by the user)

    async def link_project(self, project_id: int, actor: str = "user", reason: str = "") -> None:
        async with self.lock:
            name = self._link(project_id, actor, reason)
        self.publish({"type": "project.linked", "project_id": project_id, "name": name})

    def _link(self, project_id: int, actor: str, reason: str) -> str:
        """Attach this call to a project and load what the project already knows. Caller holds the lock."""
        with session_scope() as db:
            row = db.get(SessionRow, self.id)
            project = db.get(Project, project_id)
            if project is None:
                raise KeyError("project not found")
            name = project.name
            row.project_id = project_id
            for it in db.scalars(select(Item).where(Item.session_id == self.id)):
                it.project_id = project_id
            record(db, "session.linked", user_id=self.user_id, actor=actor, entity="session", entity_id=self.id, project_id=project_id,
                   session_id=self.id, after={"project_id": project_id}, reason=reason)
            memory, self.docs, self.memory_text = memory_mod.load(db, project_id, self.user_id)
        self.project_id = project_id
        self.memory = memory
        self.signals.memory = memory
        self.signals._seed_criteria_from_memory()
        self.publish({"type": "criteria", "criteria": self.signals.criteria_state()})
        self.t1.submit(0, "warm", self._job_warm)
        return name

    # ------------------------------------------------------------ wrap-up & end

    def _items(self) -> list[Item]:
        with session_scope() as db:
            return list(db.scalars(select(Item).where(Item.session_id == self.id, Item.status != "dropped")))

    def _publish_card(self) -> None:
        """Publish the wrap-up card (again) if its content changed."""
        card = self.wrapup_card()
        if card != getattr(self, "_last_card", None):
            self._last_card = card
            self.publish({"type": "wrapup", **card})

    def start_wrapup(self) -> dict:
        self.wrapping = True
        self.gate.pending = {k: c for k, c in self.gate.pending.items() if c.gap_type not in WRAP_FOLDED}
        self._publish_card()
        return self._last_card

    def wrapup_card(self) -> dict:
        items = self._items()
        commitments = [i for i in items if i.type == "commitment"]
        decisions = [i.text for i in items if i.type == "decision"]
        mine = [i.text for i in commitments if i.owner == "me"]
        theirs = [i.text for i in commitments if i.owner != "me"]
        recap = decisions[:1] + [i.text for i in commitments[:2]]
        open_c = sorted(self.signals.open_goals(), key=lambda c: -c.importance)
        return {
            "open_criteria": [{"id": c.id, "text": c.text, "question": c.question} for c in open_c],
            "unanswered": [q.text for q in self.signals.open_qs if not q.answered][-5:],
            "missing_dates": [i.text for i in commitments if not i.due and i.status != "open"],
            "asked_for": list(self.signals.asked_for),
            "unowned": self.signals.unowned(),
            "no_time": self.signals.unscheduled(),
            "my_commitments": mine,
            "their_commitments": theirs,
            "conflicts": [c.text for c in self.gate.pending.values() if c.gap_type == "G7"],
            "parked": [t for t, _ in self.signals.parked],
            "say_back": recap[:3],
        }

    async def end(self, delete: bool = False) -> None:
        if not self.running:
            return
        self.running = False
        for t in self.tasks:
            t.cancel()
        if self.audio is not None:
            await self.audio.stop()
        llm.live_sessions = max(0, llm.live_sessions - 1)
        _live.pop(self.id, None)
        from ..brain.consolidate import draft_recap, schedule_consolidation

        with session_scope() as db:
            row = db.get(SessionRow, self.id)
            if delete:
                pid = row.project_id
                db.delete(row)
                record(db, "session.deleted", user_id=self.user_id, actor="user", entity="session", entity_id=self.id, project_id=pid,
                       reason="stopped and deleted by user")
            else:
                row.status = "ended"
                row.ended_at = datetime.now(UTC)
                row.criteria = self.signals.criteria_state()
                row.stats = {**(row.stats or {}), **self.stats, "duration_s": round(self.now(), 1),
                             "speakers": dict(self.signals.names)}
                row.recap = draft_recap(db, self.id, self.wrapup_card())
                row.recap_status = "draft"
                record(db, "session.ended", user_id=self.user_id, actor="system", entity="session", entity_id=self.id,
                       project_id=row.project_id, session_id=self.id, after={"duration_s": round(self.now(), 1)})
                record(db, "recap.drafted", user_id=self.user_id, actor="t0", entity="session", entity_id=self.id,
                       project_id=row.project_id, session_id=self.id)
        self.publish({"type": "ended", "deleted": delete})
        if not delete:
            schedule_consolidation(self.id)
