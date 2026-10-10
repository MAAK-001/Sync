from sync import textparse as tp


def q1(text):
    qs = tp.quantities(text)
    assert qs, f"no quantity in {text!r}"
    return qs[0]


def test_money_lakh_crore_and_symbols():
    assert q1("our budget is twenty five lakh").lo == 25e5
    assert q1("about forty lakh for the project").lo == 40e5
    assert q1("₹1.2 crore").lo == 1.2e7
    assert q1("$50,000 a year").lo == 50000 and q1("$50,000 a year").unit == "USD"
    assert q1("Rs 40 lakh").unit == "INR"


def test_durations_ranges_and_words():
    d = q1("standard implementation takes 6-8 weeks")
    assert (d.kind, d.lo, d.hi) == ("duration", 42, 56)
    d = q1("we can have you live in four weeks")
    assert (d.lo, d.hi) == (28, 28)
    assert q1("six to eight weeks").hi == 56
    assert q1("respond within 8 hours").lo == 8 / 24


def test_percent_count_date():
    assert q1("a ten percent discount").lo == 10
    c = q1("we have 10 thousand users")
    assert c.kind == "count" and c.lo == 10000
    d = q1("the launch is on the 22nd")
    assert d.kind == "date" and d.lo == 22
    assert q1("on March 15").lo == 315


def test_conflicts_and_overpromise():
    a, b = q1("forty lakh"), q1("twenty five lakh")
    assert tp.conflicting(a, b)
    assert not tp.conflicting(q1("6-8 weeks"), q1("7 weeks"))
    fact = q1("6-8 weeks").as_dict()
    assert tp.overpromise(q1("four weeks"), fact, "min")
    assert not tp.overpromise(q1("ten weeks"), fact, "min")
    assert tp.overpromise(q1("a hundred percent"), q1("50%").as_dict(), "max")
    assert tp.conflicting(q1("the 15th"), q1("the 22nd"))


def test_questions_and_tags():
    assert tp.questions("What does support look like after go-live?") == ["What does support look like after go-live?"]
    assert tp.questions("Right?") == []
    assert tp.questions("So, can you send the diagram by Friday") == ["can you send the diagram by Friday?"]
    assert tp.questions("We need it soon.") == []


def test_due_phrases():
    assert tp.due_phrase("I'll send it by Thursday") == "by Thursday"
    assert tp.due_phrase("I'll share it by the 15th") == "by the 15th"
    assert tp.due_phrase("let me send that over") == ""
    assert tp.due_phrase("we'll have it by end of week").lower() == "by end of week"
