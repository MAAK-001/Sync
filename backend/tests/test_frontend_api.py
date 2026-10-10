"""What the app's pages rely on: commitments grouped by whose, the after-call pass keeping "you", playbook controls,
and the frontend being served fresh."""
from sqlalchemy import select


def signup(c, name="Meera"):
    return {"X-Sync-Token": c.post("/users", json={"name": name}).json()["token"]}


def test_after_call_pass_writes_the_user_as_you(db, uid):
    from sync.brain.consolidate import CItem, Consolidation, _reconcile
    from sync.models import Item
    from sync.models import Session as SessionRow

    with db() as s:
        row = SessionRow(user_id=uid, mode="online", status="ended")
        s.add(row)
        s.flush()
        _reconcile(s, row, Consolidation(summary="", items=[
            CItem(type="commitment", text="Meera will share the certificate by Monday", owner="Meera", status="agreed"),
            CItem(type="commitment", text="Priya will send the integration list", owner="Priya", status="agreed")]))
        items = {i.owner: i.text for i in s.scalars(select(Item).where(Item.session_id == row.id))}
    assert items == {"me": "You will share the certificate by Monday", "Priya": "Priya will send the integration list"}


def test_commitments_grouped_mine_team_theirs(app_client):
    c = app_client
    a = signup(c)
    pid = c.post("/projects", headers=a, json={"name": "Acme"}).json()["id"]
    code = c.post(f"/projects/{pid}/invite", headers=a).json()["invite_code"]
    b = {"X-Sync-Token": c.post("/join", json={"code": code, "name": "Sam"}).json()["token"]}
    for headers, owner, text in ((a, "me", "Send the plan"), (a, "Meera", "Book the demo"), (b, "me", "Check legal"),
                                 (a, "Sam", "Review pricing"), (a, "Priya", "Send the diagram")):
        c.post(f"/projects/{pid}/items", headers=headers, json={"type": "commitment", "text": text, "owner": owner})
    whose = {x["text"]: x["whose"] for x in c.get("/commitments", headers=a).json()}
    assert whose == {"Send the plan": "mine", "Book the demo": "mine", "Check legal": "team", "Review pricing": "team",
                     "Send the diagram": "theirs"}
    assert {x["text"]: x["whose"] for x in c.get("/commitments", headers=b).json()}["Check legal"] == "mine"


def test_playbook_add_reset_and_history(app_client):
    c = app_client
    a = signup(c)
    assert c.post("/me/playbook", headers=a, json={"kind": "style", "text": "Short follow-ups"}).status_code == 200
    assert c.post("/me/playbook", headers=a, json={"kind": "nonsense", "text": "x"}).status_code == 400
    assert len(c.get("/me/playbook", headers=a).json()) == 1
    assert c.post("/me/playbook/reset", headers=a).json() == {"deleted": 1}
    assert c.get("/me/playbook", headers=a).json() == []
    assert [e["type"] for e in c.get("/me/playbook/history", headers=a).json()] == ["playbook.reset", "playbook.added"]


def test_frontend_served_without_stale_cache(app_client):
    r = app_client.get("/app/js/app.js")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-cache"
    assert app_client.get("/app/").status_code == 200
