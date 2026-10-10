"""Shared helpers for demos and evals: load scripts, seed the demo world, replay a script in-process."""
from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from sync import access, bus
from sync import textparse as tp
from sync.brain import docs as docs_mod
from sync.db import session_scope
from sync.embed import embedder, to_blob
from sync.events import as_dict, record
from sync.live.engine import start_live
from sync.models import Document, Item, Person, Project, User
from sync.models import Session as SessionRow

HERE = Path(__file__).parent
SCRIPTS = HERE / "scripts"
DOCS = HERE / "docs"


def load_script(path: str | Path) -> dict:
    p = Path(path)
    if not p.exists():
        p = SCRIPTS / f"{path}.json" if not str(path).endswith(".json") else SCRIPTS / p.name
    script = json.loads(p.read_text(encoding="utf-8"))
    t = 1.0
    for u in script["utterances"]:
        words = len(u["text"].split())
        u["t_start"] = round(t, 2)
        u["t_end"] = round(t + max(1.2, words / 2.6), 2)
        t = u["t_end"] + u.get("pause", 0.9)
    script["duration"] = round(t, 1)
    script["name"] = p.stem
    return script


def ensure_user(name: str = "Meera", mode: str | None = None) -> int:
    """The demo user (created on first use). ``mode`` sets their nudge mode (quiet | normal | active)."""
    with session_scope() as db:
        user = db.scalar(select(User).where(User.name == name))
        if user is None:
            user = User(name=name)
            db.add(user)
            db.flush()
        if mode:
            user.nudge_mode = mode
        return user.id


def ingest_docs(user_id: int) -> None:
    """The vendor's price list and implementation guide, as the user's personal documents."""
    with session_scope() as db:
        have = {d.name for d in db.scalars(select(Document).where(Document.user_id == user_id))}
        for f in sorted(DOCS.glob("*.md")):
            name = f.stem.replace("_", " ").title()
            if name not in have:
                docs_mod.ingest(db, name, f.read_text(encoding="utf-8"), None, user_id, actor="system")


def seed_acme(user_id: int) -> int:
    """The Acme project as it stands after Monday's intro call (deterministic, no model needed)."""
    monday = datetime.now(UTC) - timedelta(days=3)
    with session_scope() as db:
        existing = db.scalar(select(Project).where(Project.name == "Acme · CRM integration"))
        if existing:
            access.add_member(db, existing.id, user_id, added_by=user_id)
            return existing.id
        p = Project(name="Acme · CRM integration", objective="Replace Acme's sales/billing reconciliation with Northwind CRM",
                    context="Acme reconciles sales and billing data by hand every month.", created_by=user_id,
                    brief="- Acme (IT: Priya, Finance: Arjun) evaluating Northwind CRM.\n- SAP ECC on-prem; legacy in-house billing.\n- Budget about 40 lakh; data must stay in the EU.",
                    created_at=monday)
        db.add(p)
        db.flush()
        access.add_member(db, p.id, user_id, added_by=user_id)
        for name, role in (("Priya", "Head of IT"), ("Arjun", "Finance")):
            db.add(Person(project_id=p.id, name=name, org="Acme", role=role, concerns=[]))
        s = SessionRow(user_id=user_id, project_id=p.id, mode="script", status="ended", title="Acme intro call",
                       started_at=monday, ended_at=monday + timedelta(minutes=20),
                       recap={"summary": "Intro call with Priya (Head of IT)."}, recap_status="refined",
                       stats={"speakers": {"A": "Priya"}})
        db.add(s)
        db.flush()
        budget = tp.quantities("forty lakh")[0].as_dict()
        facts = [
            dict(type="fact", text="Budget: about 40 lakh for the whole project", slot="budget", value=budget, speaker="Priya"),
            dict(type="requirement", text="Data must be hosted in the EU region (parent company in Germany)", speaker="Priya"),
            dict(type="fact", text="Finance runs on SAP ECC on-premise; billing is an old in-house system", speaker="Priya"),
            dict(type="question", text="Do you need the data hosted in a specific region?", status="done",
                 answer="Yes, the EU region", speaker="me"),
            dict(type="commitment", text="Priya will send the architecture diagram (by Thursday)", status="agreed",
                 owner="Priya", due="by Thursday", speaker="Priya"),
        ]
        for f in facts:
            it = Item(project_id=p.id, session_id=s.id, user_id=user_id, status=f.pop("status", "open"), confirmed=True,
                      created_by="t2", confidence=0.95, created_at=monday, updated_at=monday,
                      vec=to_blob(embedder.encode1(f["text"])), **f)
            db.add(it)
            db.flush()
            record(db, "item.created", actor="t2", user_id=user_id, entity="item", entity_id=it.id, project_id=p.id,
                   session_id=s.id, after=as_dict(it), reason="seeded demo memory")
        return p.id


async def replay(script: dict, *, user_id: int, speed: float = 1.0, project_id: int | None = None,
                 mode: str | None = None, on_event=None, tail: float = 8.0) -> dict:
    """Run a scripted conversation as ``user_id``'s conversation through the real live engine (in-process) and
    collect what happened. ``mode`` overrides the user's nudge mode (evals)."""
    with session_scope() as db:
        if mode:
            db.get(User, user_id).nudge_mode = mode
        row = SessionRow(user_id=user_id, mode="script", project_id=project_id, title=script.get("title", ""),
                         caller=script.get("caller", ""), status="live")
        db.add(row)
        db.flush()
        sid = row.id
    live = await start_live(sid, speed=speed)
    with session_scope() as db:
        audience = access.audience(db, db.get(SessionRow, sid))
    bus.publish({"type": "session.live", "session_id": sid, "owner_id": user_id, "project_id": project_id,
                 "app": "sample", "title": script.get("title", ""), "caller": script.get("caller", "")}, audience)
    q = live.subscribe()
    events: list[dict] = []

    async def collect():
        while True:
            ev = await q.get()
            events.append(ev)
            if on_event:
                on_event(ev)

    collector = asyncio.create_task(collect())
    start = asyncio.get_running_loop().time()
    for u in script["utterances"]:
        wait = u["t_end"] / speed - (asyncio.get_running_loop().time() - start)
        if wait > 0:
            await asyncio.sleep(wait)
        await live.add_utterance(u["channel"], u["text"], speaker=u.get("speaker", ""), t_start=u["t_start"], t_end=u["t_end"])
    await asyncio.sleep(tail / speed)
    card = live.wrapup_card()
    await live.end()
    await asyncio.sleep(0.2)
    collector.cancel()
    return {"session_id": sid, "events": events, "wrapup": card,
            "nudges": [e for e in events if e.get("type") == "nudge"],
            "items": [e["item"] for e in events if e.get("type") == "item"]}


def score(script: dict, result: dict, llm: bool = False) -> dict:
    """Match nudges to planted gaps: a hit is a nudge of the right type inside the gap's window.
    Gaps marked needs_llm only count when the live model is on."""
    utts = script["utterances"]
    hits, missed, latencies = [], [], []
    used = set()
    gaps = [g for g in script.get("gaps", []) if llm or not g.get("needs_llm")]
    script = {**script, "gaps": gaps}
    cards = [e for e in result["events"] if e.get("type") == "wrapup"]
    card_field = {"G1": "unanswered", "G4": "open_criteria", "G5": "missing_dates", "G10": "parked",
                  "G11": "no_time"}
    card_hits = 0
    for g in gaps:
        start = utts[g["after"] - 1]["t_end"]
        end = start + g["within"]
        match = next((n for n in result["nudges"] if n["id"] not in used and n["gap_type"] == g["gap"]
                      and start - 0.5 <= n["t"] <= end), None)
        card = next((c for c in cards if g["gap"] in card_field and start - 0.5 <= c["t"] <= end
                     and c.get(card_field[g["gap"]])), None)
        if match:
            used.add(match["id"])
            hits.append(g)
            latencies.append(round(match["t"] - start, 2))
        elif card:  # covered by the wrap-up card at the close
            hits.append(g)
            card_hits += 1
            latencies.append(round(max(0.0, card["t"] - start), 2))
        else:
            missed.append(g)
    n = len(result["nudges"])
    forbidden = [x for x in result["nudges"] if x["gap_type"] in script.get("forbid", [])]  # e.g. off-topic questions
    return {"gaps": len(script.get("gaps", [])), "hits": len(hits), "card_hits": card_hits, "missed": missed, "nudges": n,
            "forbidden": [f"{x['gap_type']}: {x['text']}" for x in forbidden],
            "precision": round(len(used) / n, 2) if n else 1.0,
            "recall": round(len(hits) / max(1, len(script.get("gaps", []))), 2),
            "latency_avg": round(sum(latencies) / len(latencies), 2) if latencies else None,
            "latencies": latencies,
            "per_10min": round(n / max(script["duration"], 1) * 600, 1)}
