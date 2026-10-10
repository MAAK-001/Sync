"""Play the test scenarios (demo/scripts/test_*.json) through Sync and print what it did, line by line.

    python -m demo.run_scenarios                         # all scenarios, live model on, real time, Normal nudges
    python -m demo.run_scenarios test_sales_call --t2    # also wait for the after-call record
    python -m demo.run_scenarios --mode active --no-llm

Each scenario runs in its own throwaway database (your data is never touched). The person using Sync is the
script's "user"; the company documents in demo/test_docs/<scenario>/ are their documents, and the conversation is
filed under a project as if they had picked it when the call started.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import tempfile
import textwrap
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).parent


def seed_negotiation(db, uid: int, pid: int) -> None:
    """Earlier calls with Riley: the May 14 Q2 check-in and last week's renewal proposal."""
    from sync.embed import embedder, to_blob
    from sync.live.signals import slot_value
    from sync.models import Item, Person, Utterance
    from sync.models import Session as SessionRow

    db.add(Person(project_id=pid, name="Riley", org="Client", role="Procurement Manager", concerns=[]))
    calls = [
        (datetime(2026, 5, 14, 10, tzinfo=UTC), "Q2 check-in with Riley", [
            ("them", "What's new on your roadmap? I've heard about an AI Analytics module."),
            ("you", "Yes, AI Analytics is a paid add-on, it isn't part of the base plan. When you renew, I can offer you a 30-day free trial of it."),
            ("them", "Okay, good to know."),
        ], [("fact", "AI Analytics is a paid add-on, not part of the base plan", "me"),
            ("proposal", "30-day free trial of AI Analytics when Riley renews", "me")]),
        (datetime.now(UTC) - timedelta(days=7), "Renewal proposal", [
            ("you", "For next year the renewal includes a 10% price adjustment, from $109,000 to $120,000."),
            ("them", "I'll take it to my CFO."),
        ], [("fact", "Current contract: $109,000 per year", "me"),
            ("proposal", "Renewal price for next year: $120,000 (a 10% adjustment)", "me")]),
    ]
    for when, title, lines, items in calls:
        s = SessionRow(user_id=uid, project_id=pid, mode="online", status="ended", title=title, caller="Riley",
                       started_at=when, ended_at=when + timedelta(minutes=20), recap_status="refined",
                       recap={"summary": title}, stats={"speakers": {"A": "Riley"}})
        db.add(s)
        db.flush()
        for i, (ch, text) in enumerate(lines, 1):
            db.add(Utterance(session_id=s.id, seq=i, channel=ch, speaker="" if ch == "you" else "A", text=text,
                             vec=to_blob(embedder.encode1(text))))
        for typ, text, speaker in items:
            slot = slot_value(text)
            db.add(Item(project_id=pid, session_id=s.id, user_id=uid, type=typ, text=text, status="open",
                        speaker=speaker, slot=slot[0] if slot else "", value=slot[1] if slot else {}, confirmed=True,
                        created_by="t2", confidence=0.95, created_at=when, updated_at=when,
                        vec=to_blob(embedder.encode1(text))))


async def run_one(name: str, args) -> None:
    from demo import harness
    from sync import access
    from sync.brain import docs as docs_mod
    from sync.db import session_scope
    from sync.models import Item, Project, User
    from sync.models import Session as SessionRow

    script = harness.load_script(name)
    with session_scope() as db:
        user = User(name=script["user"], nudge_mode=args.mode)
        db.add(user)
        db.flush()
        uid = user.id
        p = Project(name=script["title"], objective=script.get("objective", ""), created_by=uid)
        db.add(p)
        db.flush()
        access.add_member(db, p.id, uid, added_by=uid)
        pid = p.id
        for f in sorted((HERE / "test_docs" / name.removeprefix("test_")).glob("*.md")):
            docs_mod.ingest(db, f.stem.replace("_", " ").title(), f.read_text(encoding="utf-8"), None, uid, actor="system")
        if name == "test_negotiation":
            seed_negotiation(db, uid, pid)

    after: dict[int, list[str]] = {}
    utts = script["utterances"]

    def on_event(ev: dict) -> None:
        if ev.get("type") not in ("nudge", "wrapup"):
            return
        idx = sum(1 for u in utts if u["t_end"] <= ev["t"] + 0.01)  # the line just before the nudge
        if ev["type"] == "nudge":
            after.setdefault(idx, []).append(f"NUDGE [{ev['gap_type']} {ev['tier']}] {ev['text']}   ({ev['why']})")
        else:
            rows = [f"{k}: {'; '.join(v if isinstance(v[0], str) else [x['question'] for x in v])}"
                    for k, v in ev.items() if k in ("asked_for", "unowned", "missing_dates", "no_time", "unanswered", "parked", "open_criteria") and v]
            if rows:
                after.setdefault(idx, []).append("WRAP-UP CARD  " + " | ".join(rows))

    print(f"\n{'=' * 100}\n{name}: {script['title']}  (Sync user: {script['user']}, nudges: {args.mode}, "
          f"model {'on' if args.llm else 'off'})\n{'=' * 100}")
    t0 = time.monotonic()
    res = await harness.replay(script, user_id=uid, speed=args.speed, project_id=pid, on_event=on_event)
    names = {"A": script.get("caller") or "Speaker A"}
    if name == "test_project_sync":
        names = {"A": "Jamie", "B": "Pat"}
    expect = {i: t for i, t in script.get("expect", [])}
    for i, u in enumerate(utts, 1):
        who = script["user"] if u["channel"] == "you" else names.get(u.get("speaker", "A"), "Them")
        print(textwrap.fill(f"[{i}] {who}: {u['text']}", 100, subsequent_indent="      "))
        if i in expect:
            print(f"      >>> EXPECTED: {expect[i]}")
        for line in after.get(i, []):
            print(textwrap.fill(f"      ** {line}", 100, subsequent_indent="         "))
    print(f"\n  ({len(res['nudges'])} nudges, {time.monotonic() - t0:.0f}s)")

    with session_scope() as db:
        items = db.query(Item).filter(Item.session_id == res["session_id"], Item.status != "dropped").all()
        print("\n  Captured live:")
        for it in items:
            extra = ", ".join(x for x in (f"owner {it.owner}" if it.owner else "", f"due {it.due}" if it.due else "") if x)
            print(f"    - {it.type}: {it.text}" + (f"  ({extra})" if extra else ""))
    if args.t2:
        print("\n  Waiting for the after-call record…")
        for _ in range(240):
            await asyncio.sleep(5)
            with session_scope() as db:
                row = db.get(SessionRow, res["session_id"])
                if row.recap_status == "refined" or (not args.llm and row.recap_status == "draft"):
                    recap = row.recap
                    break
        else:
            recap = {}
        for key in ("summary", "decisions", "my_commitments", "their_commitments", "open_questions", "next_steps",
                    "concerns", "conflicts"):
            val = recap.get(key)
            if val:
                print(textwrap.fill(f"    {key}: {val if isinstance(val, str) else ' | '.join(val)}", 100,
                                    subsequent_indent="        "))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("scripts", nargs="*")
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--mode", default="normal", choices=["quiet", "normal", "active"])
    ap.add_argument("--no-llm", dest="llm", action="store_false")
    ap.add_argument("--t2", action="store_true", help="wait for the after-call record (deep model, minutes)")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    os.environ["SYNC_LLM_ENABLED"] = "true" if args.llm else "false"
    from sync.config import settings

    settings.llm_enabled = args.llm
    names = args.scripts or sorted(p.stem for p in (HERE / "scripts").glob("test_*.json"))

    async def run_all():
        from sync.db import init_db

        for name in names:
            init_db(f"sqlite:///{Path(tempfile.mkdtemp(prefix='sync-scenario-')) / 'test.db'}")
            await run_one(name, args)

    asyncio.run(run_all())


if __name__ == "__main__":
    main()
