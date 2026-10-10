from datetime import datetime

from sync import autolisten
from sync.live.gate import Gate, TimingContext, learned_weight
from sync.live.signals import Candidate
from sync.privacy import redact


def test_redaction():
    assert "[card number]" in redact("my card is 4111 1111 1111 1111 thanks")
    assert "[code]" in redact("the OTP is 482913")
    assert "[ID number]" in redact("PAN ABCDE1234F")
    assert "[password]" in redact("my password is hunter2")
    assert redact("call me on 98765 43210") == "call me on 98765 43210"  # phone numbers stay


def test_autolisten_rules():
    now = datetime(2026, 10, 9, 11, 0)  # Friday 11:00
    assert autolisten.decide({}, "zoom", now=now)[0] == "auto"
    assert autolisten.decide({"mode": "off"}, "zoom", now=now)[0] == "never"
    assert autolisten.decide({}, "phone link", caller="Mom", now=now)[0] == "auto"
    assert autolisten.decide({"never_contacts": ["Mom"]}, "phone link", caller="Mom", now=now)[0] == "never"
    assert autolisten.decide({}, "teams", title="Personal: doctor", now=now)[0] == "never"
    assert autolisten.decide({}, "discord", now=now)[0] == "ask"
    wh = {"work_hours": {"days": [0, 1, 2, 3, 4], "start": "09:00", "end": "18:00"}}
    assert autolisten.decide(wh, "zoom", now=datetime(2026, 10, 10, 11, 0))[0] == "never"  # Saturday


def cand(key, gap="G2", kind="ask", imp=4, conf=0.8, t=0.0, ref="", text=None):
    topics = {"a": "budget range", "b": "decision makers", "c": "pricing tiers", "d": "go-live timeline", "e": "x"}
    return Candidate(key=key, gap_type=gap, kind=kind, text=text or f"Ask: what about the {topics.get(key, key)}?",
                     why="", importance=imp, confidence=conf, t=t, evidence=[], source_ref=ref)


def test_gate_timing_budget_and_grounding():
    g = Gate(mode="normal")
    g.offer([cand("a")])
    assert g.tick(TimingContext(now=1.0, you_speaking=True)) is None  # never talk over the user
    assert g.tick(TimingContext(now=1.0, last_utt_end=0.9)) is None  # wait for a beat
    shown = g.tick(TimingContext(now=2.0, last_utt_end=1.0))
    assert shown and shown[0].key == "a"
    g.offer([cand("a")])  # never repeat
    g.offer([cand("b", t=3.0)])
    assert g.tick(TimingContext(now=10.0)) is None  # min gap in normal mode
    assert g.tick(TimingContext(now=50.0))[0].key == "b"
    g.offer([cand("c", gap="G8", kind="caution")])  # caution without a source is dropped
    assert "c" not in g.pending
    g.offer([cand("d", gap="G8", kind="caution", imp=5, conf=0.9, t=55.0, ref="docfact:1")])
    assert g.tick(TimingContext(now=57.0))[0].key == "d"  # urgent: allowed despite the budget
    g.offer([cand("e", gap="G8", kind="caution", imp=5, conf=0.9, t=58.0, ref="doc:9",
                  text="Ask: what about the go-live timeline?")])
    assert "e" not in g.pending  # the same advice found by a second check is not repeated


def test_learned_weight_bounds():
    assert learned_weight(10, 10, 0, 0) == 1.4
    assert learned_weight(10, 0, 0, 10) == 0.6
    assert learned_weight(0, 0, 0, 0) == 1.0
