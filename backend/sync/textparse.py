"""Deterministic text understanding for live transcripts: sentences, questions, spoken numbers,
money (incl. lakh/crore), durations, percentages, counts and due dates.

Everything here is pure Python and runs in microseconds, which is what lets Sync react instantly on a CPU.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass

# ---------------------------------------------------------------- sentences & questions

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Za-z0-9\"'])")
_AUX_START = re.compile(
    r"^(can|could|would|will|do|does|did|is|are|was|were|should|shall|have|has|had|may|might|must)\s+"
    r"(you|we|they|i|it|he|she|there|this|that|your|our|their|anyone|someone)\b",
    re.I,
)
_WH_START = re.compile(r"^(what|when|where|who|whom|whose|which|why|how)\b", re.I)
_TAG_ONLY = re.compile(
    r"^(right|okay|ok|correct|yeah|yes|no|really|sorry|pardon|huh|eh|you know|isn't it|is it|"
    r"aren't they|don't you think|does that make sense|makes sense|sound good|sounds good|all good|"
    r"can you hear me|are you there|hello)\??$",
    re.I,
)
_LEADING_FILLER = re.compile(r"^(so|and|but|okay|ok|well|um+|uh+|right|alright|yeah|also|now)[,\s]+", re.I)


_ASR_LAKH = re.compile(
    r"\b(\d[\d,.]*|one|two|three|four|five|six|seven|eight|nine|ten|twenty|thirty|forty|fifty|sixty|seventy|eighty|"
    r"ninety|hundred|[a-z]+ty[\s-][a-z]+)\s+(locks?|lacks?|lacs?|laks?|lakhs?|lack's)\b",
    re.I,
)
_ASR_CRORE = re.compile(r"\b(\d[\d,.]*|one|two|three|four|five|ten|twenty|fifty|hundred)\s+(cores?|crows?|crore's)\b", re.I)


_ASR_GOLIVE = re.compile(r"\b(go ?liv(?:e)?|goliv(?:e)?|go-liv)\b", re.I)


def fix_asr(text: str) -> str:
    """Repair common speech-recognition slips ("25 lock" -> "25 lakh", "Goliv" -> "go-live")."""
    text = _ASR_LAKH.sub(lambda m: f"{m.group(1)} lakh", text)
    text = _ASR_GOLIVE.sub("go-live", text)
    return _ASR_CRORE.sub(lambda m: f"{m.group(1)} crore", text)


_TERM_CACHE: dict[str, re.Pattern] = {}


def term_re(term: str) -> re.Pattern:
    """Whole-word (or phrase) match that also accepts a plural: "refund" matches "Refunds"."""
    pat = _TERM_CACHE.get(term)
    if pat is None:
        pat = re.compile(r"(?<![\w-])" + re.escape(term) + r"(?:s|es)?(?![\w-])", re.I)
        _TERM_CACHE[term] = pat
    return pat


def sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT_SPLIT.split(text.strip()) if s.strip()]


def strip_filler(s: str) -> str:
    prev = None
    while prev != s:
        prev, s = s, _LEADING_FILLER.sub("", s).strip()
    return s


def questions(text: str) -> list[str]:
    """Real questions in an utterance (not tags like "right?" or "can you hear me?")."""
    out = []
    for s in sentences(text):
        core = strip_filler(s)
        words = core.split()
        if _TAG_ONLY.match(core.lower().rstrip("?.! ")) or _TAG_ONLY.match(core.lower()):
            continue
        if core.endswith("?") and len(words) >= 3:
            out.append(core)
        elif (_AUX_START.match(core) or _WH_START.match(core)) and len(words) >= 5 and not core.endswith("."):
            out.append(core.rstrip(".!") + "?")
    return out


# ---------------------------------------------------------------- spoken numbers -> digits

_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
_SCALES = {"hundred": 100, "thousand": 1000}
_NUMWORD = re.compile(
    r"\b(?:(?:" + "|".join(list(_UNITS) + list(_TENS) + list(_SCALES)) + r")(?:[\s-]+(?:and[\s-]+)?)?)+\b",
    re.I,
)


def _words_value(phrase: str) -> int | None:
    total, current = 0, 0
    for w in re.split(r"[\s-]+", phrase.lower()):
        if w in ("", "and"):
            continue
        if w in _UNITS:
            current += _UNITS[w]
        elif w in _TENS:
            current += _TENS[w]
        elif w == "hundred":
            current = max(current, 1) * 100
        elif w == "thousand":
            total += max(current, 1) * 1000
            current = 0
        else:
            return None
    return total + current


def words_to_digits(text: str) -> str:
    """"six to eight weeks" -> "6 to 8 weeks"; "twenty five lakh" -> "25 lakh"."""

    def sub(m: re.Match) -> str:
        phrase = m.group(0)
        trail = phrase[len(phrase.rstrip(" -")):]  # keep "two-year" -> "2-year"
        words = re.split(r"[\s-]+", phrase.strip().lower())
        before = text[: m.start()].rstrip()
        if before[-1:].isdigit() and all(w in _SCALES or w == "and" for w in words):
            return phrase  # "10 thousand" stays a multiplier
        val = _words_value(phrase.strip())
        return f"{val}{trail}" if val is not None else phrase

    return _NUMWORD.sub(sub, text)


# ---------------------------------------------------------------- quantities


@dataclass
class Quantity:
    kind: str  # money | duration | percent | count
    lo: float
    hi: float
    unit: str  # money: currency code; duration: "days"; percent: "%"; count: noun
    text: str

    def as_dict(self) -> dict:
        return asdict(self)

    def human(self) -> str:
        return self.text


_NUM = r"(\d[\d,]*(?:\.\d+)?)"
_RANGE = rf"{_NUM}(?:\s*(?:-|–|to|or)\s*{_NUM})?"
_MULT = {
    "k": 1e3, "thousand": 1e3, "lakh": 1e5, "lakhs": 1e5, "lac": 1e5, "lacs": 1e5, "l": 1e5,
    "crore": 1e7, "crores": 1e7, "cr": 1e7, "million": 1e6, "mn": 1e6, "m": 1e6, "billion": 1e9, "bn": 1e9,
}
_MULT_RE = r"(k|thousand|lakhs?|lacs?|crores?|cr|million|mn|billion|bn)"
_CUR_PRE = r"(₹|rs\.?|inr|\$|usd|€|eur)"
_CUR_POST = r"(rupees?|rs\.?|inr|dollars?|usd|euros?|eur)"
_CUR_MAP = {"₹": "INR", "rs": "INR", "rs.": "INR", "inr": "INR", "rupee": "INR", "rupees": "INR",
            "$": "USD", "usd": "USD", "dollar": "USD", "dollars": "USD",
            "€": "EUR", "eur": "EUR", "euro": "EUR", "euros": "EUR"}

_MONEY_PATTERNS = [
    re.compile(rf"{_CUR_PRE}\s*{_RANGE}\s*{_MULT_RE}?\b", re.I),
    re.compile(rf"{_RANGE}\s*{_MULT_RE}\b(?:\s*{_CUR_POST})?", re.I),
    re.compile(rf"{_RANGE}\s*{_CUR_POST}\b", re.I),
]
_DUR_UNITS = {"hour": 1 / 24, "hr": 1 / 24, "day": 1, "business day": 1.4, "working day": 1.4,
              "week": 7, "month": 30, "quarter": 91, "year": 365}
_DURATION = re.compile(
    rf"(?:{_RANGE}|\b(an?|one)\b)[\s-]*(business days?|working days?|hours?|hrs?|days?|weeks?|months?|quarters?|years?)\b",
    re.I,
)
_PERCENT = re.compile(rf"{_RANGE}\s*(?:%|percent|per cent)", re.I)
_COUNT = re.compile(
    rf"{_RANGE}\s*{_MULT_RE}?\s+(users|seats|licen[cs]es|units|people|employees|agents|stores|locations|branches|"
    r"systems|sites|calls|tickets|customers|orders|devices|machines|servers|schools|students)\b",
    re.I,
)


_MONTH_NUM = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
_MONTH_WORD = r"(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sept|sep|oct|nov|dec)"
_DATES = [
    re.compile(rf"\b(?:the\s+)?(\d{{1,2}})(?:st|nd|rd|th)(?:\s+(?:of\s+)?{_MONTH_WORD})?\b", re.I),  # the 15th (of March)
    re.compile(rf"\b{_MONTH_WORD}\s+(\d{{1,2}})(?:st|nd|rd|th)?\b", re.I),  # March 15
]


def _num(s: str | None) -> float | None:
    if not s:
        return None
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


def _range(a: str, b: str | None) -> tuple[float, float] | None:
    lo, hi = _num(a), _num(b)
    if lo is None:
        return None
    if hi is None:
        hi = lo
    return (min(lo, hi), max(lo, hi))


def quantities(text: str) -> list[Quantity]:
    """All money/duration/percent/count quantities in a sentence, normalised for comparison."""
    t = words_to_digits(text)
    out: list[Quantity] = []
    taken: list[tuple[int, int]] = []

    def free(span: tuple[int, int]) -> bool:
        return all(span[1] <= s or span[0] >= e for s, e in taken)

    # Counts first, so "10 thousand users" is a count and not money.
    for m in _COUNT.finditer(t):
        r = _range(m.group(1), m.group(2))
        if r:
            factor = _MULT.get((m.group(3) or "").lower(), 1.0)
            out.append(Quantity("count", r[0] * factor, r[1] * factor, m.group(4).lower(), m.group(0).strip()))
            taken.append(m.span())

    for i, pat in enumerate(_MONEY_PATTERNS):
        for m in pat.finditer(t):
            if not free(m.span()):
                continue
            g = m.groups()
            if i == 0:
                cur, a, b, mult = g
            elif i == 1:
                a, b, mult, cur = g
            else:
                a, b, cur = g
                mult = None
            r = _range(a, b)
            if r is None:
                continue
            factor = _MULT.get((mult or "").lower(), 1.0)
            currency = _CUR_MAP.get((cur or "").lower().rstrip("."), "INR" if mult and mult.lower().startswith(("lakh", "lac", "crore", "cr")) else "")
            out.append(Quantity("money", r[0] * factor, r[1] * factor, currency, m.group(0).strip()))
            taken.append(m.span())

    for m in _PERCENT.finditer(t):
        if free(m.span()):
            r = _range(m.group(1), m.group(2))
            if r:
                out.append(Quantity("percent", r[0], r[1], "%", m.group(0).strip()))
                taken.append(m.span())

    for m in _DURATION.finditer(t):
        if not free(m.span()):
            continue
        unit_word = m.group(4).lower().rstrip("s")
        unit_word = "hr" if unit_word in ("hr", "hrs") else unit_word
        days = next(v for k, v in _DUR_UNITS.items() if unit_word.startswith(k))
        if m.group(3):  # "a week", "an hour"
            r = (1.0, 1.0)
        else:
            r = _range(m.group(1), m.group(2))
        if r:
            out.append(Quantity("duration", r[0] * days, r[1] * days, "days", m.group(0).strip()))
            taken.append(m.span())

    for i, pat in enumerate(_DATES):
        for m in pat.finditer(t):
            if not free(m.span()):
                continue
            day_s, month_s = (m.group(1), m.group(2)) if i == 0 else (m.group(2), m.group(1))
            day = int(day_s)
            if not 1 <= day <= 31:
                continue
            if month_s:
                month = _MONTH_NUM[month_s.lower()[:3]]
                out.append(Quantity("date", month * 100 + day, month * 100 + day, "month-day", m.group(0).strip()))
            else:
                out.append(Quantity("date", day, day, "day", m.group(0).strip()))
            taken.append(m.span())
    return out


def conflicting(a: dict | Quantity, b: dict | Quantity) -> bool:
    """True when two values for the same slot clearly disagree (ranges that don't overlap, with tolerance)."""
    a = a if isinstance(a, dict) else a.as_dict()
    b = b if isinstance(b, dict) else b.as_dict()
    if a["kind"] != b["kind"]:
        return False
    if a["kind"] == "date":
        if a["unit"] == b["unit"]:
            return a["lo"] != b["lo"]
        return int(a["lo"]) % 100 != int(b["lo"]) % 100  # compare day of month when one has no month
    if a["kind"] == "money" and a.get("unit") and b.get("unit") and a["unit"] != b["unit"]:
        return False
    tol = {"money": 0.1, "count": 0.1, "duration": 0.0, "percent": 0.0}.get(a["kind"], 0.0)
    lo_a, hi_a = a["lo"] * (1 - tol), a["hi"] * (1 + tol)
    return b["hi"] < lo_a or b["lo"] > hi_a


def overpromise(claim: dict | Quantity, fact: dict, direction: str) -> bool:
    """A claim breaks a documented limit. direction=min: the doc gives a minimum (e.g. 6-8 weeks to go live),
    so promising less is an over-promise. direction=max: the doc gives a ceiling (e.g. max 10% discount)."""
    c = claim if isinstance(claim, dict) else claim.as_dict()
    if c["kind"] != fact["kind"]:
        return False
    if direction == "min":
        return c["lo"] < fact["lo"] - 1e-9
    if direction == "max":
        return c["hi"] > fact["hi"] + 1e-9
    return conflicting(c, fact)


# ---------------------------------------------------------------- due dates

_WEEKDAYS = r"(monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon|tue|tues|wed|thu|thurs|fri|sat|sun)"
_MONTHS = r"(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)"
_DUE = re.compile(
    r"\b("
    r"(?:by|before|on|until|till|this|next|coming)\s+(?:the\s+)?" + _WEEKDAYS + r"(?:\s+(?:morning|evening|afternoon|eod))?"
    r"|(?:by|before|on|until|till)\s+(?:the\s+)?\d{1,2}(?:st|nd|rd|th)?(?:\s+(?:of\s+)?" + _MONTHS + r")?"
    r"|(?:by|before|on|until|till)\s+" + _MONTHS + r"\s+\d{1,2}(?:st|nd|rd|th)?"
    r"|(?:by\s+)?(?:tomorrow|tonight|today)(?:\s+(?:morning|evening|afternoon|eod))?"
    r"|(?:this|later)\s+(?:morning|afternoon|evening|today|tonight)"
    r"|(?:by\s+)?(?:the\s+)?end of (?:the\s+)?(?:day|week|month|quarter)"
    r"|\beod\b|\beow\b|\bcob\b"
    r"|(?:by\s+|within\s+|in\s+)(?:the\s+)?next\s+(?:\d+\s+)?(?:days?|weeks?|month)"
    r"|next week|this week|next month|this month"
    r"|(?:within|in)\s+\d+\s+(?:hours?|days?|weeks?)"
    r"|(?:right\s+)?after\s+(?:this\s+|the\s+)?(?:call|meeting|session)"
    r"|in a minute|in a moment|right away"
    r")\b",
    re.I,
)


_DAY = re.compile(
    r"\b((?:(?:next|this|coming|on)\s+)?" + _WEEKDAYS + r"|tomorrow|(?:on\s+)?the\s+\d{1,2}(?:st|nd|rd|th)(?:\s+of\s+"
    + _MONTHS + r")?|(?:on\s+)?" + _MONTHS + r"\s+\d{1,2}(?:st|nd|rd|th)?)\b",
    re.I,
)
_CLOCK = re.compile(
    r"\b(?:\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)|\d{1,2}:\d{2}|at\s+\d{1,2}(?::\d{2})?(?!\d)|noon|midnight"
    r"|\d{1,2}\s*o'?clock|(?:half|quarter)\s+(?:past|to)\s+\w+)\b",
    re.I,
)


def day_phrase(text: str) -> str:
    """The day a thing is planned for ("next Thursday", "tomorrow", "the 15th"), or ""."""
    m = _DAY.search(text)
    return re.sub(r"^on\s+", "", m.group(1).strip(), flags=re.I) if m else ""


def has_clock_time(text: str) -> bool:
    """An actual time of day ("3 pm", "at 10:30", "noon"). "Afternoon" on its own is not one."""
    return bool(_CLOCK.search(words_to_digits(text)))


def due_phrase(text: str) -> str:
    m = _DUE.search(words_to_digits(text))
    return m.group(0).strip() if m else ""


_UNIT_COUNT = re.compile(r"\b(\d+)\s+(?:extra\s+|additional\s+|more\s+|new\s+)?(seat|user|licen[cs]e|agent|employee|"
                         r"person|people|unit|location|device)s?\b", re.I)
_UNIT_PRICE = r"\$\s?([\d,]+(?:\.\d+)?)\s*(?:per|a|/|each)\s*{unit}s?\b[^.]{{0,30}}?\b(annually|per year|a year|yearly|" \
              r"per annum|per month|a month|monthly)"


def quick_math(question: str, sources: list[str]) -> str:
    """'add 15 extra seats halfway through the contract — what's the cost?' + '$1,200 per seat annually'
    -> '15 × $1,200 × 6/12 = $9,000'. Empty unless the question asks for a cost of a number of units and a source
    gives a per-unit price (the sum is shown with its parts, so it can be checked at a glance)."""
    q = words_to_digits(question)
    m = _UNIT_COUNT.search(q)
    if not m or not re.search(r"\b(cost|price|budget|how much|total|pay|charge|fee)\b", q, re.I):
        return ""
    n, unit = int(m.group(1)), m.group(2).lower()
    unit = "(?:person|people)" if unit in ("person", "people") else unit
    for src in sources:
        r = re.search(_UNIT_PRICE.format(unit=unit), words_to_digits(src), re.I)
        if not r:
            continue
        price = float(r.group(1).replace(",", ""))
        yearly = r.group(2).lower() in ("annually", "per year", "a year", "yearly", "per annum")
        months = re.search(r"\b(\d+)\s+months?\b", q)
        if yearly and re.search(r"\bhalf(?:way)?\b", q, re.I):
            num, den = 6, 12
        elif yearly and months:
            num, den = int(months.group(1)), 12
        else:
            num, den = 1, 1
        total = n * price * num / den
        part = f" × {num}/{den}" if den != 1 else ""
        period = "" if den != 1 else (" a year" if yearly else " a month")
        return f"{n} × ${price:,.0f}{part} = ${total:,.0f}{period}"
    return ""


def mentions_number(text: str) -> bool:
    """Any amount, quantity, percentage, date, day or time ("40 lakh", "two weeks", "next Thursday", "3 pm")."""
    return bool(re.search(r"\d", words_to_digits(text)) or day_phrase(text) or due_phrase(text) or has_clock_time(text))


def short(text: str, n: int = 8) -> str:
    """First n words of a clause, trimmed of trailing punctuation (for nudge templates)."""
    words = strip_filler(text).split()
    out = " ".join(words[:n]).rstrip(",.;:!?")
    return out + ("…" if len(words) > n else "")
