"""Guards on the after-call record: no invented dates, and owners taken from what was actually promised."""
from sync.brain.consolidate import _fix_owners, _no_invented_dates
from sync.models import Item


def test_dates_survive_only_if_said():
    said = "reports in under 10 seconds, a 10% adjustment, fully tested by November 18th"
    assert _no_invented_dates("Confirm terms with Riley by 10 Oct (Riley)", said) == "Confirm terms with Riley (Riley)"
    assert _no_invented_dates("Send the invite (you, 10 Oct)", said) == "Send the invite (you)"
    assert _no_invented_dates("Deploy the build by November 18th (Jamie)", said) == "Deploy the build by November 18th (Jamie)"


def test_owner_comes_from_the_recorded_commitment():
    items = [Item(type="commitment", text="Jordan asked you to send your SOC2 security whitepaper", owner="me", user_id=1),
             Item(type="commitment", text="Priya will send the architecture diagram (by Thursday)", owner="Priya", user_id=1)]
    steps = ["Send SOC2 security whitepaper (no owner yet)", "Send the architecture diagram (you)", "Book a demo (Jordan)"]
    assert _fix_owners(steps, items, {}, 1) == ["Send SOC2 security whitepaper (you)",
                                                "Send the architecture diagram (Priya)", "Book a demo (Jordan)"]
