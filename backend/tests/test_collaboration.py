"""Shared projects, sign-in, manual project choice and what the live agent knows (the real app, model off)."""
import asyncio

from sqlalchemy import select


def signup(c, name="Aqueel"):
    return {"X-Sync-Token": c.post("/users", json={"name": name}).json()["token"]}


def join(c, code, name):
    r = c.post("/join", json={"code": code, "name": name}).json()
    return {"X-Sync-Token": r["token"]}, r


def test_sign_in_is_required_and_setup_happens_once(app_client):
    c = app_client
    assert c.get("/projects").status_code == 401
    a = signup(c)
    assert c.get("/me", headers=a).json()["name"] == "Aqueel"
    assert c.post("/users", json={"name": "Someone else"}).status_code == 403  # others join by invite
    assert c.get("/me", headers={"X-Sync-Token": "wrong"}).status_code == 401


def test_shared_project_everything_common_and_every_edit_named(app_client):
    c = app_client
    a = signup(c)
    pid = c.post("/projects", headers=a, json={"name": "Acme rollout"}).json()["id"]
    c.post(f"/projects/{pid}/items", headers=a, json={"type": "fact", "text": "Budget is about 40 lakh"})

    # Sam isn't a member yet: the project doesn't exist for them.
    code = c.post(f"/projects/{pid}/invite", headers=a).json()["invite_code"]
    assert c.get(f"/join/{code}").json()["project"] == "Acme rollout"
    b, joined = join(c, code, "Sam")
    assert joined["project_id"] == pid
    assert [p["name"] for p in c.get("/projects", headers=b).json()] == ["Acme rollout"]
    assert {m["name"] for m in c.get(f"/projects/{pid}/members", headers=a).json()["members"]} == {"Aqueel", "Sam"}

    # Sam edits; Aqueel sees the same data, and the history names who did what.
    item = c.post(f"/projects/{pid}/items", headers=b, json={"type": "commitment", "text": "Send the pilot plan",
                                                             "owner": "me", "due": "Friday"}).json()
    assert item["added_by"] == "Sam"
    mine = next(i for i in c.get(f"/projects/{pid}/items", headers=a).json() if i["id"] == item["id"])
    assert mine["owner_label"] == "Sam"  # Aqueel sees Sam's promise under Sam's name, not "you"
    c.patch(f"/projects/{pid}", headers=b, json={"name": "Acme · CRM rollout", "brief": "Pilot agreed in principle."})
    c.patch(f"/items/{item['id']}", headers=a, json={"status": "done"})
    events = c.get(f"/projects/{pid}/events", headers=a).json()
    by = {(e["type"], e["user_name"]) for e in events}
    assert {("project.created", "Aqueel"), ("member.added", "Sam"), ("item.created", "Sam"), ("project.updated", "Sam"),
            ("item.updated", "Aqueel")} <= by
    assert all(e["ts"] for e in events)
    assert c.get(f"/projects/{pid}", headers=a).json()["name"] == "Acme · CRM rollout"


def test_items_people_and_documents_are_editable(app_client):
    c = app_client
    a = signup(c)
    pid = c.post("/projects", headers=a, json={"name": "Globex"}).json()["id"]
    it = c.post(f"/projects/{pid}/items", headers=a, json={"type": "fact", "text": "Budget: 30 lakh"}).json()
    assert it["slot"] == "budget" and it["value"]["text"] == "30 lakh"  # typed numbers are tracked like spoken ones
    it = c.patch(f"/items/{it['id']}", headers=a, json={"text": "Budget: 35 lakh"}).json()
    assert it["value"]["text"] == "35 lakh"
    assert c.patch(f"/items/{it['id']}", headers=a, json={"type": "nonsense"}).status_code == 400
    c.delete(f"/items/{it['id']}", headers=a)
    assert c.get(f"/projects/{pid}/items", headers=a).json() == []
    assert "item.deleted" in {e["type"] for e in c.get(f"/projects/{pid}/events", headers=a).json()}

    person = c.post(f"/projects/{pid}/people", headers=a, json={"name": "Neha", "role": "CFO"}).json()
    c.patch(f"/people/{person['id']}", headers=a, json={"org": "Globex"})
    assert c.get(f"/projects/{pid}/people", headers=a).json()[0]["org"] == "Globex"
    c.delete(f"/people/{person['id']}", headers=a)
    assert c.get(f"/projects/{pid}/people", headers=a).json() == []

    doc = c.post("/documents", headers=a, data={"project_id": str(pid)},
                 files={"file": ("price_list.txt", b"Discounts are capped at 10 percent.", "text/plain")}).json()
    assert [d["name"] for d in c.get(f"/documents?project_id={pid}", headers=a).json()] == ["Price List"]
    c.delete(f"/documents/{doc['id']}", headers=a)
    assert c.get(f"/documents?project_id={pid}", headers=a).json() == []


def test_project_is_only_ever_chosen_by_the_user(app_client):
    c = app_client
    a = signup(c)
    c.post("/projects", headers=a, json={"name": "Initech CRM", "objective": "CRM migration for Initech"})
    s = c.post("/sessions", headers=a, json={"mode": "script", "title": "Call"}).json()
    lines = ["Hi, this is Peter from Initech about the CRM migration project."] * 12  # > 150 words, > 40 s of talk
    for text in lines:
        c.post(f"/sessions/{s['id']}/utterances", headers=a, json={"channel": "them", "speaker": "A", "text": text})
    assert c.get(f"/sessions/{s['id']}", headers=a).json()["project_id"] is None  # no automatic guess
    r = c.post(f"/sessions/{s['id']}/link", headers=a, json={"new_project": True}).json()
    assert r["name"].startswith("Call ·")  # named after the call; rename it any time
    assert c.get(f"/sessions/{s['id']}", headers=a).json()["project_id"] == r["project_id"]
    c.post(f"/sessions/{s['id']}/end", headers=a, json={})


def test_conversations_belong_to_their_owner(app_client):
    c = app_client
    a = signup(c)
    pid = c.post("/projects", headers=a, json={"name": "Acme"}).json()["id"]
    private = c.post("/sessions", headers=a, json={"mode": "script"}).json()["id"]
    shared = c.post("/sessions", headers=a, json={"mode": "script", "project_id": pid}).json()["id"]
    code = c.post(f"/projects/{pid}/invite", headers=a).json()["invite_code"]
    b, _ = join(c, code, "Sam")
    assert c.get(f"/sessions/{private}", headers=b).status_code == 404  # unfiled calls stay private
    assert c.get(f"/sessions/{shared}", headers=b).json()["owner_name"] == "Aqueel"
    assert c.post(f"/sessions/{shared}/end", headers=b, json={}).status_code == 403  # only the owner controls it
    assert c.post(f"/sessions/{shared}/link", headers=b, json={"new_project": True}).status_code == 403
    assert {x["id"] for x in c.get("/sessions", headers=b).json()} == {shared}
    for sid in (private, shared):
        c.post(f"/sessions/{sid}/end", headers=a, json={})


def test_live_memory_has_summary_context_conflicts_and_every_number(db, uid):
    from sync.brain import memory as memory_mod
    from sync.models import Conflict, Item, Project, ProjectMember

    with db() as s:
        p = Project(name="Acme", objective="Win the CRM deal", context="They reconcile billing by hand.",
                    brief="Pilot agreed; budget still being discussed.", created_by=uid)
        s.add(p)
        s.flush()
        s.add(ProjectMember(project_id=p.id, user_id=uid))
        texts = [("fact", "Budget is about forty lakh"), ("commitment", "Priya will send the diagram"),
                 ("requirement", "Data must stay in the EU"), ("note", "Neha (CFO) approves anything above 10 lakh")]
        items = []
        for typ, text in texts:
            it = Item(project_id=p.id, user_id=uid, type=typ, text=text, speaker="Priya",
                      due="by Thursday" if typ == "commitment" else "")
            s.add(it)
            items.append(it)
        s.flush()
        s.add(Conflict(project_id=p.id, item_a=items[0].id, item_b=items[0].id, explanation="budget: 40 lakh vs 25 lakh"))
        pid = p.id
    with db() as s:
        _mem, _docs, text = memory_mod.load(s, pid, uid)
    numbers = text.split("NUMBERS ON RECORD")[1].split("OTHER MEMORY")[0]
    for must in ("PROJECT: Acme", "GOAL: Win the CRM deal", "Pilot agreed; budget still being discussed.",
                 "They reconcile billing by hand.", "OPEN CONFLICTS", "budget: 40 lakh vs 25 lakh"):
        assert must in text
    assert "forty lakh" in numbers and "due by Thursday" in numbers and "above 10 lakh" in numbers
    assert "Data must stay in the EU" in text.split("OTHER MEMORY")[1]


def test_nudges_reach_only_the_person_on_the_call():
    from sync import bus

    async def run():
        mine, theirs = bus.subscribe(1), bus.subscribe(2)
        bus.publish({"type": "nudge"}, {1})
        bus.publish({"type": "recap.refined"}, {1, 2})
        got = (mine.qsize(), theirs.qsize())
        bus.unsubscribe(mine)
        bus.unsubscribe(theirs)
        return got

    assert asyncio.run(run()) == (2, 1)


def test_sample_calls_never_teach_sync(db, uid):
    from demo import harness
    from sync.models import GapStat

    script = harness.load_script("phone_followup")
    asyncio.run(harness.replay(script, user_id=uid, speed=20, mode="active"))
    with db() as s:
        assert s.scalars(select(GapStat)).all() == []
