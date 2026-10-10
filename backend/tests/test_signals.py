"""T0 signal detectors with a tiny deterministic embedder (no model download needed)."""
import hashlib
import re

import numpy as np

from sync.live.signals import Memory, Signals, Utt


def fake_embed(texts):
    out = np.zeros((len(texts), 384), dtype=np.float32)
    for i, t in enumerate(texts):
        for w in re.findall(r"[a-z0-9]+", t.lower()):
            out[i, int(hashlib.md5(w.encode()).hexdigest(), 16) % 384] += 1
    n = np.linalg.norm(out, axis=1, keepdims=True)
    return out / np.maximum(n, 1e-9)


class Talk:
    def __init__(self, memory=None):
        self.s = Signals(memory or Memory(), fake_embed)
        self.n = 0
        self.t = 0.0

    def say(self, channel, text, speaker="A"):
        self.n += 1
        self.t += 4
        u = Utt(id=self.n, seq=self.n, channel=channel, speaker=speaker if channel == "them" else "", text=text,
                t_start=self.t - 3, t_end=self.t, vec=fake_embed([text])[0])
        return self.s.process(u, self.t)


def gaps(out):
    return [c.gap_type for c in out.candidates]


def test_commitment_without_date_and_with_date():
    t = Talk()
    out = t.say("them", "Sure. I'll send you our current integration list.")
    assert "G5" in gaps(out)
    assert any(d.type == "commitment" and d.owner == "Speaker A" for d in out.drafts)
    out = t.say("you", "I'll share the certificate by Monday.")
    assert "G5" not in gaps(out)
    assert any(d.type == "commitment" and d.owner == "me" and d.due == "by Monday" for d in out.drafts)


def test_request_then_yes_is_their_commitment():
    t = Talk()
    t.say("them", "This is Ravi. The API work is done.")
    t.say("you", "Ravi, can you chase the vendor?")
    out = t.say("them", "Sure.")
    assert any(d.type == "commitment" and d.owner == "Ravi" for d in out.drafts)
    assert "G5" in gaps(out)


def test_conditional_offer_and_vague_time():
    t = Talk()
    out = t.say("them", "If you sign for two years, we can give you a ten percent discount.")
    assert "G6" in gaps(out)
    assert any(d.status == "conditional" for d in out.drafts)
    out = t.say("them", "We really need it soon.")
    assert "G2" in gaps(out)


def test_names_parked_and_closing():
    t = Talk()
    t.say("them", "Hi, this is Priya from Acme.")
    assert t.s.names == {"A": "Priya"}
    t.say("them", "We should come back to the data migration question later.")
    t.say("you", "Sure, let's park that.")
    out = t.say("them", "I think that's all from me. Anything else?")
    assert out.closing and "G10" in gaps(out)


def test_slot_conflict_with_memory_and_promise_guard():
    from sync import textparse as tp
    from sync.live.signals import DocFactRef, KnownItem

    mem = Memory(
        known=[KnownItem(item_id=7, type="fact", text="Budget: about 40 lakh", who="Priya", when="6 Oct", slot="budget",
                         value=tp.quantities("forty lakh")[0].as_dict(), vec=fake_embed(["budget forty lakh"])[0])],
        doc_facts=[DocFactRef(id=3, slot="go_live", value=tp.quantities("6-8 weeks")[0].as_dict(), direction="min",
                              text="Standard implementation takes 6-8 weeks from kickoff to go-live.",
                              ref="Implementation Guide §2")],
    )
    t = Talk(memory=mem)
    out = t.say("them", "For this phase our budget is twenty five lakh.")
    g7 = [c for c in out.candidates if c.gap_type == "G7"]
    assert g7 and "Priya said 40 lakh" in g7[0].text.replace("forty", "40")
    out = t.say("you", "We can definitely have you live in four weeks.")
    g8 = [c for c in out.candidates if c.gap_type == "G8"]
    assert g8 and "Implementation Guide §2" in g8[0].text


def test_already_known_answer_is_not_re_asked():
    from sync import textparse as tp
    from sync.live.signals import KnownItem

    mem = Memory(known=[KnownItem(item_id=9, type="fact", text="Budget: about 40 lakh", who="Priya", when="6 Oct",
                                  slot="budget", value=tp.quantities("forty lakh")[0].as_dict())])
    t = Talk(memory=mem)
    out = t.say("you", "What budget are you working with?")
    assert [c.gap_type for c in out.candidates] == ["G9"]
    assert out.candidates[0].kind == "fact"
