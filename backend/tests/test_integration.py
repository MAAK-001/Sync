"""End-to-end with the real embedding model and the live engine (language model off for speed/determinism)."""
import asyncio

from sqlalchemy import select

from demo import harness
from sync.models import Conflict, DocFact, Event, Item


def test_document_facts(db, uid):
    harness.ingest_docs(uid)
    with db() as s:
        facts = s.scalars(select(DocFact)).all()
    by_slot = {}
    for f in facts:
        by_slot.setdefault(f.slot, []).append(f)
    assert any(f.value["lo"] == 42 and f.direction == "min" for f in by_slot["go_live"])
    assert any(f.value["lo"] == 50 and f.direction == "max" for f in by_slot["refund"])
    assert any(f.value["hi"] == 10 and f.direction == "max" for f in by_slot["discount"])


def test_acme_thursday_live(db, uid):
    harness.ingest_docs(uid)
    pid = harness.seed_acme(uid)
    script = harness.load_script("acme_thursday")
    res = asyncio.run(harness.replay(script, user_id=uid, speed=12, project_id=pid, mode="active"))
    shown = {n["gap_type"] for n in res["nudges"]}
    assert {"G7", "G9", "G8", "G5"} <= shown, shown
    sc = harness.score(script, res)
    assert sc["recall"] >= 0.6, sc
    with db() as s:
        items = s.scalars(select(Item).where(Item.session_id == res["session_id"])).all()
        types = {i.type for i in items}
        assert {"commitment", "question", "fact", "concern"} <= types
        assert any(i.owner == "me" and "certificate" in i.text for i in items)
        assert all(i.user_id == uid for i in items)
        evs = s.scalars(select(Event).where(Event.session_id == res["session_id"])).all()
    assert {"item.created", "nudge.shown", "session.ended", "recap.drafted"} <= {e.type for e in evs}
    assert all(e.user_name == "Meera" for e in evs)  # every change says whose conversation it came from
    card = res["wrapup"]
    assert card["parked"] and card["my_commitments"]


def test_post_call_merge_records_conflict_without_model(db, uid):
    """Without a model the draft recap stays; slot conflicts are recorded by the deterministic merge."""
    from sync.brain.consolidate import Consolidation, _finish

    harness.ingest_docs(uid)
    pid = harness.seed_acme(uid)
    script = harness.load_script("acme_thursday")
    res = asyncio.run(harness.replay(script, user_id=uid, speed=20, project_id=pid, mode="normal"))
    _finish(res["session_id"], Consolidation(summary="Deep dive with Arjun and Priya."), open_qs_ids=[], project_id=pid)
    with db() as s:
        conflicts = s.scalars(select(Conflict).where(Conflict.project_id == pid)).all()
    assert conflicts and "budget" in conflicts[0].explanation


def test_api_flow(app_client):
    c = app_client
    assert c.get("/setup").json() == {"has_users": False}
    token = c.post("/users", json={"name": "Aqueel"}).json()["token"]
    h = {"X-Sync-Token": token}
    p = c.post("/projects", headers=h, json={"name": "Globex rollout", "objective": "Pilot",
                                             "people": [{"name": "Sam", "org": "Globex"}]}).json()
    c.post(f"/projects/{p['id']}/items", headers=h, json={"text": "Go-live must be before March", "type": "requirement"})
    s = c.post("/sessions", headers=h, json={"mode": "script", "project_id": p["id"]}).json()
    for text in ("Hi, this is Sam from Globex.", "I'll send the vendor list."):
        r = c.post(f"/sessions/{s['id']}/utterances", headers=h, json={"channel": "them", "speaker": "A", "text": text})
        assert r.status_code == 200
    c.post(f"/sessions/{s['id']}/end", headers=h, json={})
    recap = c.get(f"/sessions/{s['id']}/recap", headers=h).json()
    assert any("vendor list" in x for x in recap["recap"]["their_commitments"])
    events = c.get(f"/projects/{p['id']}/events", headers=h).json()
    assert {"project.created", "person.added", "item.created", "session.ended"} <= {e["type"] for e in events}
    assert {e["user_name"] for e in events} == {"Aqueel"}
    assert c.get("/search", headers=h, params={"q": "when must go-live happen"}).json()[0]["kind"] == "requirement"
    assert c.post("/calls/detected", headers=h, json={"app": "discord"}).json()["decision"] == "ask"
