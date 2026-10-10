"""The detectors added after the final test scripts: each one fires when it should and stays quiet otherwise."""
from sync import textparse as tp
from sync.live.signals import Signals
from test_signals import Talk, gaps  # noqa: I001


def cands(out, gap):
    return [c for c in out.candidates if c.gap_type == gap]


def test_unacknowledged_emotion_and_urgency():
    t = Talk()
    t.say("them", "I am absolutely furious. I have a presentation to my board in two hours and nothing works.")
    out = t.say("you", "Okay, are you on the desktop app or the web browser?")
    g = cands(out, "G13")
    assert g and "furious" in g[0].text and "board in two hours" in g[0].text
    t = Talk()
    t.say("them", "I am absolutely furious. I have a presentation to my board in two hours.")
    assert "G13" not in gaps(t.say("you", "I'm so sorry, let's get you in before your board meeting."))


def test_their_deadline_is_acknowledged_or_flagged():
    t = Talk()
    t.say("them", "Also, we are finalizing our Q4 budget by next Thursday, so we're moving quickly.")
    g = cands(t.say("you", "Our platform was built for speed. Let me show you the dashboard."), "G13")
    assert g and "Q4 budget by next Thursday" in g[0].text
    t = Talk()
    t.say("them", "We are finalizing our Q4 budget by next Thursday.")
    assert "G13" not in gaps(t.say("you", "Good to know, then let's get you a proposal before Thursday."))
    t = Talk()
    t.say("them", "I'll send the signed form by Friday.")  # their promise, not their pressure
    assert "G13" not in gaps(t.say("you", "Great."))


def test_identifiers_are_remembered():
    t = Talk()
    t.say("them", "My account number is EXP-8842 and nothing loads.")
    g = cands(t.say("you", "Could you repeat your account number for me?"), "G9")
    assert g and "EXP-8842" in g[0].text


def test_compound_question_tracks_both_parts():
    t = Talk()
    t.say("them", "Does it integrate with HubSpot, and what is the exact API rate limit per minute?")
    assert len(t.s.open_qs) == 2


def test_partly_confirmed_request_flags_the_rest():
    t = Talk()
    t.say("them", "Can you send me your SOC2 whitepaper, introduce me to an implementation engineer, and send a "
                  "calendar invite for next Tuesday at 2 PM?")
    out = t.say("you", "Absolutely, I'll send that calendar invite right away.")
    g = cands(out, "G1")
    assert g and "SOC2 whitepaper" in g[0].text and "implementation engineer" in g[0].text and "calendar" not in g[0].text
    t = Talk()
    t.say("them", "Can you send me the deck and the price list?")
    assert "G1" not in gaps(t.say("you", "Sure, will do."))  # a plain yes covers everything


def test_in_call_actions_and_third_party_errands_are_not_promises_to_chase():
    t = Talk()
    out = t.say("you", "Let me share my screen and show you the dashboard.")
    assert not [d for d in out.drafts if d.type == "commitment"] and "G5" not in gaps(out)
    out = t.say("them", "Fine, I will email billing, but right now I just need access.")
    assert "G5" not in gaps(out)
    assert any(d.type == "commitment" and d.text.endswith("email billing") for d in out.drafts)


def test_concession_without_anything_in_return():
    t = Talk()
    t.say("you", "The renewal price for next year is $120,000.")
    t.say("them", "We need to keep the price flat at $109,000.")
    g = cands(t.say("you", "Okay, I can keep the price flat at $109,000 for this renewal."), "G14")
    assert g and "$120,000" in g[0].text and "$109,000" in g[0].text
    t = Talk()
    t.say("you", "The renewal price for next year is $120,000.")
    t.say("them", "We need to keep the price flat at $109,000.")
    assert "G14" not in gaps(t.say("you", "I can do a price of $109,000 if you sign for two years."))


def test_launch_before_ready_is_a_timeline_conflict():
    t = Talk()
    t.say("them", "We will have the build deployed to staging and fully tested by November 18th.", speaker="A")
    g = cands(t.say("them", "The campaign goes live on November 15th.", speaker="B"), "G7")
    assert g and "15th" in g[0].text and "18th" in g[0].text
    t = Talk()
    t.say("them", "The build will be fully tested by November 18th.", speaker="A")
    assert "G7" not in gaps(t.say("them", "The campaign goes live on November 25th.", speaker="B"))


def test_task_nobody_owns():
    t = Talk()
    t.say("them", "Someone really needs to update the sandbox API keys before the beta.", speaker="A")
    t.say("them", "Good call.", speaker="B")
    g = cands(t.say("you", "Agreed. Now, moving on to the retrospective."), "G15")
    assert g and "update the sandbox API keys" in g[0].text
    t = Talk()
    t.say("them", "Someone really needs to update the sandbox API keys before the beta.", speaker="A")
    out = t.say("them", "I'll update the sandbox keys today.", speaker="B")
    out2 = t.say("you", "Great, moving on.")
    assert "G15" not in gaps(out) + gaps(out2)


def test_off_agenda_debate():
    t = Talk()
    t.say("you", "Let's go through the launch schedule.")
    out = t.say("them", "Are we going to talk about the v2.0 UI overhaul? I really think we should change the colours.",
                speaker="B")
    out2 = t.say("them", "I disagree, that is three weeks of work.", speaker="A")
    g = cands(out, "G16") + cands(out2, "G16")
    assert len(g) == 1 and "v2.0 UI overhaul" in g[0].text
    from test_signals import fake_embed
    from sync.live.signals import Memory

    s = Signals(Memory(), fake_embed, agenda="Plan the v2.0 UI overhaul")  # it IS the agenda
    t2 = Talk()
    t2.s = s
    t2.say("them", "Are we going to talk about the v2.0 UI overhaul? I really think we should change the colours.", speaker="B")
    t2.say("them", "I disagree, that is three weeks of work.", speaker="A")
    assert not s.drift_reported


def test_quick_math():
    q = "If we add 15 extra seats halfway through the contract, what is the pro-rated cost?"
    assert tp.quick_math(q, ["Additional seats cost $1,200 per seat annually."]) == "15 × $1,200 × 6/12 = $9,000"
    assert tp.quick_math("What would 20 users cost?", ["It is $40 per user per month."]) == "20 × $40 = $800 a month"
    assert tp.quick_math("What is the onboarding fee for 50 users?", ["Onboarding for 50-99 users is a $2,500 flat fee."]) == ""
