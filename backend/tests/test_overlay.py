"""On-screen overlay: which cards show, and when (no window is opened)."""
from sync.desktop.overlay import Overlay, _wrapup_lines


def nudge(i, kind="confirm", sid=1, text="Confirm: by when will Alex send the guide?"):
    return {"type": "nudge", "id": i, "session_id": sid, "kind": kind, "text": text, "why": "No date", "ttl": 20}


def feed(ov, *events):
    for ev in events:
        ov._handle("event", {"ev": ev})


def test_nudge_card_drops_the_kind_prefix():
    ov = Overlay()
    feed(ov, nudge(1))
    (card,) = ov._visible()
    assert card.kind == "confirm" and card.text == "By when will Alex send the guide?" and card.note == "No date"


def test_newest_nudges_win_and_the_strip_stays_small():
    ov = Overlay()
    feed(ov, nudge(1), nudge(2, "ask"), nudge(3, "caution"))
    assert [c.nudge_id for c in ov._visible()] == [3, 2]  # newest first, at most two nudges
    ov._handle("gate", {"app": "phone link", "seconds": 5, "ask": False})
    ov._handle("notice", {"text": "Listening", "style": "on", "seconds": 4})
    assert len(ov._visible()) == Overlay.MAX_CARDS and ov._visible()[0].kind == "gate"


def test_hiding_nudges_never_hides_the_gate_or_notices():
    ov = Overlay()
    feed(ov, nudge(1))
    ov._handle("gate", {"app": "zoom", "seconds": 5, "ask": False})
    ov._handle("toggle", {})
    kinds = {c.kind for c in ov._visible()}
    assert "confirm" not in kinds and "gate" in kinds and "info" in kinds
    ov._handle("toggle", {})
    assert "confirm" in {c.kind for c in ov._visible()}


def test_used_nudges_and_ended_calls_clear_their_cards():
    ov = Overlay()
    feed(ov, nudge(1), nudge(2, sid=2))
    feed(ov, {"type": "nudge.outcome", "id": 1, "outcome": "used", "session_id": 1})
    assert [c.nudge_id for c in ov._visible() if c.nudge_id] == [2]
    feed(ov, {"type": "ended", "session_id": 2, "deleted": True})
    (card,) = ov._visible()
    assert card.kind == "off" and "deleted" in card.text


def test_wrapup_card_lists_what_is_still_open():
    lines = _wrapup_lines({"missing_dates": ["Alex will send the guide"], "no_time": ["training next Thursday"],
                           "unanswered": [], "parked": [], "open_criteria": [{"question": "Ask: who signs off?"}]})
    assert lines == ["No date yet: Alex will send the guide", "No time set: training next Thursday",
                     "Still to cover: who signs off?"]
    ov = Overlay()
    feed(ov, {"type": "wrapup", "session_id": 1, "missing_dates": [], "no_time": [], "unanswered": [], "parked": [],
              "open_criteria": []})
    assert ov._visible() == []  # nothing open: no card
