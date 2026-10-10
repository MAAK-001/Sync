"""Nudge reliability from a real call: a day agreed with no time; the general goals; the nudge budget."""
from sync import textparse as tp
from sync.live.gate import BUDGET
from test_signals import Talk, gaps  # noqa: I001  (helpers live next to the other signal tests)


def test_day_and_clock_helpers():
    assert tp.day_phrase("are you ready for the review next Thursday?") == "next Thursday"
    assert tp.day_phrase("see you on the 15th") == "the 15th"
    assert tp.day_phrase("I'll send it by Friday") == "Friday"
    assert tp.has_clock_time("let's say 3 pm") and tp.has_clock_time("at 10:30") and tp.has_clock_time("at three o'clock")
    assert not tp.has_clock_time("Thursday afternoon works") and not tp.has_clock_time("in 4 weeks")
    assert tp.due_phrase("I'll email it to you this afternoon") == "this afternoon"


def test_agreed_day_without_time_is_flagged():
    t = Talk()
    t.say("them", "Are you ready for our full training review schedule for next Thursday?")
    out = t.say("you", "Yes, Thursday works for us.")
    assert "G11" not in gaps(out)  # give people a moment to say the time
    t.say("them", "Okay, consider it added.")
    out = t.say("them", "I'll update the meeting agenda and email it to you.")
    g11 = [c for c in out.candidates if c.gap_type == "G11"]
    assert g11 and g11[0].text == "Confirm: what time next Thursday?"


def test_time_given_means_no_nudge():
    t = Talk()
    t.say("them", "Can we have the review meeting on Thursday?")
    t.say("you", "Yes, Thursday works.")
    t.say("them", "Great, let's say 3 pm.")
    for line in ("Okay.", "Sounds good.", "Anything else?"):
        assert "G11" not in gaps(t.say("them", line))


def test_deadlines_and_tasks_are_not_meetings():
    t = Talk()
    out = t.say("them", "I'll send the training materials by Friday.")
    t.say("you", "Great.")
    for _ in range(3):
        out = t.say("them", "Okay.")
        assert "G11" not in gaps(out)


def test_general_goals_apply_to_every_conversation():
    t = Talk()
    assert {c.id for c in t.s.open_goals()} == {"goal", "timeline", "decision", "next_step"}
    t.say("them", "Our main goal is to cut the time it takes to close the books.")
    assert "goal" not in {c.id for c in t.s.open_goals()}


def test_normal_mode_is_not_too_quiet():
    gap, per10 = BUDGET["normal"]
    assert gap <= 20 and per10 >= 8
