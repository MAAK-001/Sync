"""T0 signals: instant, deterministic detection of conversational gaps.

Each final utterance goes through ``Signals.process`` (pure Python + one cached embedding per utterance).
It returns *candidates* (possible nudges, scored later by the gate) and *drafts* (live ledger items).

Gap types
  G1 unanswered question      G2 vague claim             G3 concern raised then dropped
  G4 goal untouched           G5 commitment without date G6 conditional offer
  G7 contradiction            G8 over-promise            G9 already known (don't re-ask)
  G10 parked thread           G11 day agreed, no time    G12 answer card (from your documents and past calls)
  G13 urgency or emotion not acknowledged                G14 concession without anything in return
  G15 task nobody owns        G16 debate about something off the agenda
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np

from .. import textparse as tp
from ..guide import Slot, guide

# ---------------------------------------------------------------- data


@dataclass
class Utt:
    id: int
    seq: int
    channel: str  # you | them
    speaker: str  # stable label, e.g. "A"
    text: str
    t_start: float
    t_end: float
    vec: np.ndarray


@dataclass
class Candidate:
    key: str
    gap_type: str
    kind: str  # ask | confirm | caution | fact
    text: str
    why: str
    importance: int
    confidence: float
    t: float
    evidence: list[int]
    source_ref: str = ""
    ttl: float = 120.0
    tier: str = "t0"
    meta: dict = field(default_factory=dict)


@dataclass
class Draft:
    key: str
    type: str  # fact | requirement | question | concern | proposal | decision | commitment | risk
    text: str
    status: str = "open"
    speaker: str = ""
    owner: str = ""
    due: str = ""
    condition: str = ""
    answer: str = ""
    slot: str = ""
    value: dict | None = None
    evidence: list[int] = field(default_factory=list)
    quote: str = ""
    confidence: float = 0.75


@dataclass
class KnownItem:
    """Something already in project memory (for the already-asked guard and conflict radar)."""

    item_id: int
    type: str
    text: str
    who: str
    when: str
    slot: str = ""
    value: dict | None = None
    answer: str = ""
    vec: np.ndarray | None = None


@dataclass
class DocFactRef:
    id: int
    slot: str
    value: dict
    direction: str
    text: str
    ref: str  # "Implementation Guide §2"
    vec: np.ndarray | None = None


@dataclass
class Memory:
    known: list[KnownItem] = field(default_factory=list)
    doc_facts: list[DocFactRef] = field(default_factory=list)
    has_docs: bool = False

    def __post_init__(self) -> None:
        vecs = [k.vec for k in self.known if k.vec is not None]
        self._idx = [i for i, k in enumerate(self.known) if k.vec is not None]
        self._mat = np.vstack(vecs) if vecs else np.zeros((0, 384), dtype=np.float32)

    def nearest(self, vec: np.ndarray, k: int = 3) -> list[tuple[KnownItem, float]]:
        if self._mat.shape[0] == 0:
            return []
        scores = self._mat @ vec
        order = np.argsort(-scores)[:k]
        return [(self.known[self._idx[i]], float(scores[i])) for i in order]

    def slot_value(self, slot: str) -> KnownItem | None:
        vals = [k for k in self.known if k.slot == slot and k.value]
        return vals[-1] if vals else None


@dataclass
class Output:
    candidates: list[Candidate] = field(default_factory=list)
    drafts: list[Draft] = field(default_factory=list)
    answered: list[str] = field(default_factory=list)  # candidate keys that are now moot
    claim_checks: list[tuple[Utt, str]] = field(default_factory=list)  # (utterance, sentence) for T1
    concern_sharpen: list[tuple[str, str]] = field(default_factory=list)  # (candidate key, concern sentence)
    names: dict[str, str] = field(default_factory=dict)
    topic_shift: bool = False
    closing: bool = False
    criteria_changed: bool = False
    # Questions from the other side, and moments you stall ("my mind went blank"): the engine looks for the
    # answer in your documents and past calls (G12).
    lookups: list[tuple[str, str, Utt]] = field(default_factory=list)  # (key, query, utterance)


# ---------------------------------------------------------------- patterns

_W = r"[^.?!]"
_CONCERN_STRONG = re.compile(
    r"\b(worr(?:y|ied|ies|ying)|concern(?:ed|s)?|risk(?:y|s)?|afraid|nervous|hesitant|not sure|unsure|doubt(?:ful|s)?|"
    r"can(?:'|no)?t afford|cannot afford|too (?:expensive|costly|slow|complex|complicated|risky|long)|"
    r"overloaded|disrupt\w*|scared|struggl\w*)\b",
    re.I,
)
_CONCERN_WEAK = re.compile(r"\b(?<!no )(?<!not a )(problem\w*|issue\w*|challeng\w*|difficult\w*|complicated|painful|"
                           r"frustrat\w*)\b", re.I)  # "no problem" is not a concern
_REQUIREMENT_VERB = re.compile(
    r"\b(need|needs|needed|must|has to|have to|should|want|wants|looking for|require[sd]?|important that|expect)\b", re.I
)
_ACTION = (
    r"(send|share|email|mail|forward|call|set up|setup|schedule|book|arrange|prepare|draft|check|confirm|look into|"
    r"look at|find out|get back|follow up|provide|deliver|review|update|introduce|loop in|connect|organi[sz]e|create|"
    r"put together|write|ask|talk to|speak to|reach out|demo|walk you through|fix|resolve|escalate|refund|process|"
    r"chase|ping|remind|handle|take care of|own|coordinate|sort out|finish|complete|test|deploy|approve|sign|"
    r"finali[sz]e|investigate|raise|file|submit|upload|get)"
)
# "we can …" is usually a capability or an offer, not a promise, so it isn't here.
_COMMIT = re.compile(
    r"\b(i'll|i will|i'm going to|i am going to|we'll|we will|we're going to|we are going to|let me|i can|"
    r"i shall|i'd be happy to|happy to|i'll make sure to)\s+(?:also\s+|just\s+|quickly\s+|then\s+)?"
    + _ACTION + r"\b([^.?!]*)",
    re.I,
)
# A request opens the sentence ("Ravi, can you chase the vendor?"), unlike "How quickly can you deliver?".
_REQUEST = re.compile(
    r"^(?:[\w ]{1,20},\s*)?(?:please\s+)?(?:can|could|would|will) you\s+(?:please\s+|also\s+|maybe\s+)?"
    + _ACTION + r"\b([^.?!]*)",
    re.I,
)
# "You can just email me the exact figure later." is a request too.
_OFFERED_REQUEST = re.compile(r"\b(?:you can|you could|feel free to)\s+(?:just\s+)?(email|send|share|forward|mail)\b([^.?!]*)",
                              re.I)
_AFFIRM = re.compile(
    r"^\W*(sure|yes|yeah|yep|of course|absolutely|definitely|certainly|okay|ok|will do|no problem|sounds good|"
    r"i can|i will|i'll|we can|we will|we'll|done|great|perfect)\b",
    re.I,
)
_YESNO = re.compile(r"^\W*(yes|no|yeah|nope|yep|sure|correct|not really|we do|we don't|it is|it isn't|exactly)\b", re.I)
_DECISION = re.compile(
    r"\b(let's go with|let us go with|we(?:'ll| will) go with|we(?:'ve| have) decided|we decided|the decision is|"
    r"that's agreed|we(?:'re| are) agreed|we agree|agreed\b|it's a deal|let's proceed|we(?:'ll| will) proceed|"
    r"go ahead with|let's finali[sz]e|we(?:'re| are) going with|approved)\b",
    re.I,
)
_CONDITION = re.compile(r"\b(only if|provided that|provided|as long as|assuming|subject to|on condition that|if)\b\s+([^.?!,;]*)", re.I)
_OFFER_WORDS = re.compile(
    r"\b(price|pricing|discount|rate|cost|fee|terms|deal|offer|free|waive|percent|lakh|crore|contract|year|volume|"
    r"commit|payment|deliver|delivery|timeline|go live|include|included|extend|extension)\b|%",
    re.I,
)
_CAPABILITY = re.compile(
    r"\b(we|our (?:product|platform|system|solution|team|tool|app|software))\b" + _W + r"{0,40}?\b"
    r"(support|supports|have|has|offer|offers|provide|provides|include|includes|integrate|integrates|work with|"
    r"works with|comply|complies|certified|guarantee|guarantees|handle|handles|can)\b",
    re.I,
)
_PARKED = re.compile(
    r"\b(?:come back to|circle back (?:to|on)|get back to|revisit)\s+([^.?!]{2,60}?)"
    r"(?:\s+(?:later|offline|for now|another time|next time))?\s*(?:[.?!]|$)"
    r"|\b(?:discuss|cover|talk about|look at|pick up|deal with)\s+([^.?!]{2,60}?)\s+(?:later|another time|next time|offline)\b"
    r"|\b(?:park|table)\s+(that|this|it)\b|\btake\s+(that|this|it)\s+offline\b|\b(parking lot)\b",
    re.I,
)
_VAGUE_TOPIC = {"that", "this", "it", "that one", "this one", "that for now", "this for now", "it for now", "parking lot"}
# Replies that sound related but don't answer ("let me check", "let me first walk you through...").
_DEFLECT = re.compile(
    r"^\W*(?:\w+,\s*)?(let me (?:first|check|see|find out|come back|get back|look into|start|begin|walk you|take you|show you)|"
    r"(?:i'll|i will|we'll|we will) (?:check|find out|get back|come back|come to that|look into)|good question|"
    r"before (?:that|we get to that)|i'm not sure|i am not sure|i don't know|not sure|hold on|one moment|let's come back)",
    re.I,
)
_CLOSING = re.compile(
    r"\b(wrap (?:this |it |things )?up|anything else\??$|before we (?:go|end|finish|close|hang up|wrap)|"
    r"thanks? (?:you )?(?:so much |again |all |everyone )?for (?:your|the) time|let's (?:end|stop|close) (?:here|there|it)|"
    r"that's all (?:from me|for today)|talk (?:to you )?(?:soon|later)|have a (?:good|great|nice) (?:day|one|evening|weekend)|"
    r"good ?bye|bye(?: bye| now)?|see you|take care)\b",
    re.I,
)
_INTRO = re.compile(
    r"\b(?i:I'm|I am|this is|my name is)\s+([A-Z][a-z]{2,})(?:\s+(?:from|with|at|in|heading|leading|on)\s+(?:the\s+)?([A-Z][\w&.-]+))?"
)
# Things people put in a diary. ("review" alone is left out: "review the designs by Friday" is a task.)
_EVENT = re.compile(
    r"\b(meeting|meet|call|session|demo|demonstration|workshop|training|walkthrough|walk-through|kick-?off|"
    r"check-?in|catch-?up|sync|visit|appointment|interview|presentation|stand-?up|discussion|webinar|lunch|dinner|"
    r"coffee|onboarding|handover|hand-over)\b",
    re.I,
)
_ACCEPT = re.compile(r"\b(works?|fine|good|great|perfect|okay|ok|sounds?|confirmed?|booked|agreed|sure|yes|yeah)\b", re.I)
# You're stuck mid-answer: Sync looks for the answer for you.
_STALL = re.compile(
    r"\b(my mind (?:just )?went blank|give me (?:a|one) (?:second|sec|moment|minute)|let me (?:check|think|look "
    r"that up|find out)|i(?:'m| am) not (?:entirely |quite |totally |100% )?sure|i don'?t (?:recall|remember|know)|"
    r"i can'?t remember|off the top of my head|do the math|i(?:'ll| will) have to check)\b",
    re.I,
)
_EMOTION = re.compile(
    r"\b(furious|angry|livid|upset|frustrated|fed up|annoyed|disappointed|unacceptable|ridiculous|outrageous|"
    r"not happy|very unhappy)\b",
    re.I,
)
_URGENCY = re.compile(
    r"\b(?:in (?:the next )?(?:\d+|a|an|one|two|three|four|five|ten|thirty|a couple of|a few) (?:minutes?|hours?)|"
    r"right now|immediately|asap|as soon as possible|urgent(?:ly)?|"
    r"(?:by|before) (?:the )?(?:end of (?:the )?(?:day|week|month)|today|tonight|tomorrow|"
    r"(?:next |this )?(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)))\b",
    re.I,
)
# A deadline is theirs to worry about (not a promise or a question): "we are finalizing our Q4 budget by Thursday".
_CONSTRAINT = re.compile(
    r"\b(deadline|finali[sz]\w*|due|have to|has to|need to|must|presentation|board|launch\w*|go(?:ing|es)? live|"
    r"cut-?off|expir\w*|trying to|budget)\b",
    re.I,
)
_ACKNOWLEDGE = re.compile(
    r"\b(sorry|apolog\w*|i understand|understand (?:how|that|the|your)|i hear you|i know (?:how|this|that)|"
    r"that(?:'s| is| sounds) (?:frustrating|stressful|tough|awful|really)|frustrat\w*|urgent\w*|right away|"
    r"immediately|priority|deadline|in time|before your|good to know|thanks for (?:the|letting)|timeline)\b",
    re.I,
)
# "My account number is EXP-8842", "order no. 55210", "ticket #4471".
_ID = re.compile(
    r"\b(account|order|ticket|case|reference|invoice|policy|customer|member|booking|tracking|serial|claim|"
    r"application|reservation)\s*(?:number|no\.?|id|#|code)?\s*(?:is|:|was)?\s*#?([A-Z0-9][A-Z0-9-]{3,})\b",
    re.I,
)
_ASK_ID = re.compile(
    r"\b(account|order|ticket|case|reference|invoice|policy|customer|member|booking|tracking|serial|claim|"
    r"application|reservation)\s*(?:number|no\b|id\b|#|code)",
    re.I,
)
# Asking for something in return when you give ground ("if you sign for two years").
_GIVE_GET = re.compile(
    r"\b(?:if you|in exchange|in return|provided (?:that )?you|as long as you|on condition|you(?:'d| would) need to|"
    r"we(?:'d| would) need you to|in turn)\b",
    re.I,
)
# Something has to happen but nobody took it: "someone needs to update the sandbox keys".
_UNOWNED = re.compile(
    r"\b(?:someone|somebody|anyone|someone on the team)\s+(?:really\s+|still\s+|also\s+|definitely\s+)?"
    r"(?:needs? to|should|has to|have to|must|ought to|got to|gotta)\s+" + _ACTION + r"\b([^.?!]*)",
    re.I,
)
_LETS_MAKE_SURE = re.compile(
    r"\blet'?s make sure\s+(?:that\s+)?(?:the\s+|our\s+)?([\w -]{3,40}?)\s+(?:is|are|gets?|get)\s+"
    r"(updated|done|fixed|sent|handled|set up|tested|deployed|reviewed|ready|changed|rotated)\b",
    re.I,
)
_MOVE_ON = re.compile(r"\b(moving on|move on|next (?:topic|item|point)|let'?s (?:move|go) to|switching to)\b", re.I)
# A launch that happens before what it depends on is ready.
_READY = re.compile(r"\b(ready|done|finish\w*|complet\w*|deployed|fully tested|tested|built|delivered|available)\b", re.I)
_LAUNCH = re.compile(r"\b(go(?:es)? live|goes out|launch\w*|release\w*|ship\w*|campaign|announce\w*|open (?:to|for))\b", re.I)
_MONTH_OF = re.compile(
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b|"
    r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b",
    re.I,
)
_MONTHS3 = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
# A debate about something for later (another version or phase) in a meeting about something else.
_LATER_SCOPE = re.compile(
    r"\b(v\d+(?:\.\d+)*|version \d+|next version|next release|phase (?:two|2|three|3)|future release|roadmap|"
    r"overhaul|redesign|rebrand)\b",
    re.I,
)
_DEBATE = re.compile(r"\b(i disagree|i don'?t agree|i don'?t think|i really think|we should (?:change|switch|move)|"
                     r"no way|that's not|but i think|i'd rather)\b", re.I)
_PROCEDURAL = re.compile(
    r"^(?:\w+,\s*)?(?:(?:shall|should|can|could|do) we (?:start|begin|get started|kick off|move on|go ahead|wrap|continue)|"
    r"(?:are|is) (?:you|everyone|everybody) (?:ready|there|with me|okay)|can (?:you|everyone) (?:hear|see) )",
    re.I,
)
_HERE = re.compile(r"^\W*(?:(?i:hi|hello|hey)[\s,]+)?([A-Z][a-z]{2,})\s+here\b()")  # "Neha here."
_NOT_NAMES = {
    "Not", "Sure", "Good", "Fine", "Happy", "Glad", "Sorry", "Okay", "Here", "Calling", "Just", "Also", "Going",
    "Looking", "Really", "Very", "The", "Afraid", "Worried", "Concerned", "Trying", "Thinking", "Back", "Done",
    "Ready", "Available", "Great", "Interested", "Curious", "Still", "Actually", "Now", "There", "Quite", "Pretty",
}
_COUNT_NOUN = re.compile(
    r"\b(users|seats|licen[cs]es|people|employees|agents|stores|locations|branches|systems|sites|customers|orders|"
    r"devices|servers|records|transactions|tickets|calls|integrations|teams|departments|offices)\b",
    re.I,
)


def _has(term: str, text: str) -> bool:
    return tp.term_re(term).search(text) is not None


def _owner_label(channel: str, speaker: str, names: dict[str, str]) -> str:
    return "me" if channel == "you" else names.get(speaker, f"Speaker {speaker}" if speaker else "them")


_TRAIL = {"and", "or", "with", "to", "the", "a", "an", "of", "for", "on", "in", "at", "by", "our", "your", "then", "so"}


def _clean_action(verb: str, rest: str, n: int = 9) -> str:
    """'send you our current integration list' -> 'send our current integration list'."""
    rest = re.split(r",\s*(?:but|so|because|and then|which)\b|\s+(?:but|because|so that|while)\s+|"
                    r"\s+so\s+(?=they|we|you|it|that|he|she)\b", rest)[0]
    due = tp.due_phrase(rest)
    if due:
        rest = re.sub(re.escape(due), "", rest, flags=re.I)
        rest = re.sub(r"\b(?:for|on|by)\s+(?=at\b)", "", rest, flags=re.I)  # "for next Tuesday at 2 PM" -> "at 2 PM"
    rest = re.sub(r"^\s*(?:you|us|me)\b\s*(?=\w+\s+\w)", "", rest, flags=re.I)  # keep "review them"
    words = re.sub(r"\s+", " ", f"{verb} {rest}").strip().rstrip(",;:").split()[:n]
    while len(words) > 1 and words[-1].lower().strip(",;:") in _TRAIL:
        words.pop()
    return " ".join(words).rstrip(",;:")


# Commitments worth a "by when?" nudge when no date was given (not "let me check").
_DELIVERABLE = {"send", "share", "email", "mail", "forward", "prepare", "draft", "provide", "deliver", "set up",
                "setup", "schedule", "book", "arrange", "introduce", "create", "put together", "write", "follow up",
                "get back", "fix", "resolve", "refund", "process", "demo", "walk you through", "organise", "organize"}


_ACTION_NC = "(?:" + _ACTION[1:]  # the same verbs without a capturing group (re.split would return them)
_SPLIT_ACTIONS = re.compile(r",\s*(?:and\s+)?(?=" + _ACTION_NC + r"\b)|\s+and\s+(?=" + _ACTION_NC + r"\b)", re.I)
_STOP = {"your", "with", "over", "send", "share", "email", "from", "that", "this", "them", "about", "into", "some",
         "just", "also", "please", "right", "away", "next", "the", "and", "for", "one", "via", "make", "sure"}


def _actions(verb: str, rest: str) -> list[tuple[str, str]]:
    """'send me X, introduce me to Y, and send over Z for Tuesday' -> one (action, due) per thing asked."""
    out = []
    for part in _SPLIT_ACTIONS.split(f"{verb}{rest}"):
        m = re.match(r"\s*" + _ACTION + r"\b(.*)", part, re.I)
        if m:
            out.append((_clean_action(m.group(1), m.group(2)), tp.due_phrase(m.group(2))))
    return out or [(_clean_action(verb, rest), tp.due_phrase(rest))]


def _content_words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]{4,}", text.lower()) if w not in _STOP}


def _clause(sentence: str, start: int, end: int, n: int = 12) -> str:
    """The clause of a sentence around [start, end) (commas, 'so', 'but' cut clauses)."""
    left = max(sentence.rfind(",", 0, start), sentence.lower().rfind(" so ", 0, start), sentence.lower().rfind(" but ", 0, start))
    right_cands = [i for i in (sentence.find(",", end), sentence.lower().find(" so ", end), sentence.lower().find(" but ", end)) if i >= 0]
    right = min(right_cands) if right_cands else len(sentence)
    clause = sentence[left + 1 if left >= 0 else 0:right].strip(" ,.")
    clause = re.sub(r"^(?:and|also|so|but|just)\b\s*", "", clause, flags=re.I)
    return tp.short(clause, n)


def _date_of(sentence: str) -> tuple[int, int, str] | None:
    """(month 1-12 or 0, day, text) for the first calendar date in a sentence."""
    m = _MONTH_OF.search(sentence)
    if m:
        mon, day = (m.group(1), m.group(2)) if m.group(1) else (m.group(4), m.group(3))
        return _MONTHS3.index(mon[:3].lower()) + 1, int(day), m.group(0)
    for q in tp.quantities(sentence):
        if q.kind == "date":
            return 0, int(q.lo), q.text
    return None


def slot_of(sentence: str, q: tp.Quantity) -> tuple[str, Slot] | None:
    """Which tracked slot (budget, go-live…) a quantity belongs to: the nearest matching trigger word."""
    best = None
    s_low = tp.words_to_digits(sentence).lower()  # same text the quantity was parsed from
    q_pos = s_low.find(q.text.lower())
    for sid, slot in guide().slots.items():
        if slot.kind != q.kind:
            continue
        for trig in slot.triggers:
            m = tp.term_re(trig).search(s_low)
            if m:
                dist = abs(m.start() - q_pos) if q_pos >= 0 else 0
                if dist <= 70 and (best is None or dist < best[0]):
                    best = (dist, sid, slot)
    return (best[1], best[2]) if best else None


def slot_value(text: str) -> tuple[str, dict] | None:
    """The first tracked number in a typed note ("Budget: 30 lakh" -> ("budget", {...})), so details people
    enter by hand are checked during calls exactly like numbers heard on a call."""
    for s in tp.sentences(text) or [text]:
        for q in tp.quantities(s):
            hit = slot_of(s, q)
            if hit:
                return hit[0], q.as_dict()
    return None


# ---------------------------------------------------------------- state


@dataclass
class _Criterion:
    id: str
    text: str
    question: str
    keywords: list[str]
    importance: int
    vecs: np.ndarray
    persist: bool = False
    status: str = "untouched"  # untouched | asked | covered
    evidence: list[int] = field(default_factory=list)


@dataclass
class _Plan:
    """A meeting/call that someone planned for a day ("training review next Thursday")."""

    utt: Utt
    day: str
    event: str
    agreed: bool
    timed: bool = False
    since: int = 0  # utterances heard since it was agreed
    reported: bool = False


@dataclass
class _OpenQ:
    key: str
    utt: Utt
    text: str
    vec: np.ndarray
    slot: str = ""
    yes_no: bool = False
    answered: bool = False
    reported: bool = False


@dataclass
class _Task:
    """Something that has to happen, said without anyone taking it on."""

    utt: Utt
    action: str
    words: set[str]
    owned: bool = False
    reported: bool = False


@dataclass
class _Concern:
    key: str
    utt: Utt
    text: str
    vec: np.ndarray
    addressed: bool = False
    you_turns_after: int = 0
    reported: bool = False


class Signals:
    """Per-session T0 state machine."""

    # Similarity thresholds for bge-small-en-v1.5 (calibrated on the golden scripts).
    T_CRITERION = 0.66
    T_ANSWER_NEAR = 0.45
    T_ANSWER_FAR = 0.68
    T_KNOWN = 0.80
    T_ADDRESSED = 0.55
    T_TOPIC = 0.55

    def __init__(self, memory: Memory, embed, expected_duration: float = 1800.0, agenda: str = ""):
        self.guide = guide()
        self.agenda = agenda.lower()  # the meeting's or project's purpose (for spotting off-agenda debates)
        self.memory = memory
        self.embed = embed  # callable: list[str] -> np.ndarray (unit rows)
        self.expected = expected_duration
        self.utts: list[Utt] = []
        self.names: dict[str, str] = {}
        self.orgs: dict[str, str] = {}
        self.open_qs: list[_OpenQ] = []
        self.concerns: list[_Concern] = []
        self.parked: list[tuple[str, int]] = []
        self.session_slots: dict[str, tuple[dict, str, int]] = {}  # slot -> (value, who, utt id)
        self.pending_request: tuple[Utt, list[tuple[str, str]], str] | None = None  # (utt, [(action, due)], channel)
        self.asked_for: list[str] = []  # things they asked you for that you never confirmed
        self.ids: dict[str, tuple[str, str, int]] = {}  # "account" -> (EXP-8842, who, utt id)
        self.tasks: list[_Task] = []
        self.dates: list[tuple[str, int, int, str, str, int]] = []  # (ready|launch, month, day, text, phrase, utt id)
        self.my_vals: dict[str, tuple[dict, int]] = {}  # slot -> (your last value, utt id)
        self.their_vals: dict[str, dict] = {}  # slot -> their last value
        self.pending_ack: tuple[Utt, str] | None = None  # their urgency/emotion waiting for your acknowledgement
        self.drift_reported = False
        self.commitments: list[tuple[str, np.ndarray, str, int]] = []  # (owner, vec, due, uid)
        self.plans: list[_Plan] = []
        self.closing_t: float | None = None
        self.last_shift_t = -1e9
        self.vague_seen: set[str] = set()
        self._q_counter = 0

        texts, owners = [], []
        for c in self.guide.goals:
            parts = [c.text, *c.examples]
            texts += parts
            owners += [c.id] * len(parts)
        vecs = embed(texts) if texts else np.zeros((0, 384), dtype=np.float32)
        self.criteria: list[_Criterion] = []
        for c in self.guide.goals:
            rows = vecs[[i for i, o in enumerate(owners) if o == c.id]]
            self.criteria.append(_Criterion(c.id, c.text, c.question, [k.lower() for k in c.keywords], c.importance,
                                            rows, persist=c.persist))
        self._seed_criteria_from_memory()

    # ------------------------------------------------------------ helpers

    def _seed_criteria_from_memory(self) -> None:
        """Facts already learned in earlier conversations start as covered (never re-ask)."""
        for c in self.criteria:
            if not c.persist:
                continue
            for k in self.memory.known:
                if k.vec is None or (k.type == "question" and not k.answer):
                    continue
                text = f"{k.text} {k.answer}"
                if (c.vecs @ k.vec).max(initial=0) >= self.T_CRITERION or any(_has(w, text) for w in c.keywords if len(w) > 3):
                    c.status, c.evidence = "covered", [-k.item_id]
                    break

    def _slot_of(self, sentence: str, q: tp.Quantity) -> tuple[str, Slot] | None:
        return slot_of(sentence, q)

    def criteria_state(self) -> list[dict]:
        return [{"id": c.id, "text": c.text, "question": c.question, "importance": c.importance,
                 "status": c.status, "evidence": c.evidence} for c in self.criteria]

    # ------------------------------------------------------------ main entry

    def process(self, u: Utt, now: float) -> Output:
        out = Output()
        self.utts.append(u)
        sents = tp.sentences(u.text) or [u.text]
        who = _owner_label(u.channel, u.speaker, self.names)

        self._names(u, out)
        self._topic(u, now, out)
        self._answers(u, out)
        self._criteria(u, out)
        qs = tp.questions(u.text)
        self._questions(u, qs, now, out)
        for s in sents:
            self._slots(u, s, who, now, out)
            self._conditions(u, s, who, now, out)
            self._decisions(u, s, who, out)
            if u.channel == "them":
                self._vague(u, s, now, out)
            already_flagged = any(c.gap_type == "G8" and u.id in c.evidence for c in out.candidates)
            if (u.channel == "you" and self.memory.has_docs and _CAPABILITY.search(s) and not s.endswith("?")
                    and not already_flagged):
                out.claim_checks.append((u, s))  # the numeric guard didn't catch it; let the model check
        self._commitments(u, sents, who, now, out)
        self._acknowledge(u, sents, now, out)
        self._identifiers(u, now, out)
        self._tasks(u, sents, now, out)
        self._timeline(u, sents, now, out)
        self._drift(u, now, out)
        if u.channel == "you" and _STALL.search(u.text):
            q = next((q for q in reversed(self.open_qs) if q.utt.channel == "them" and not q.answered), None)
            out.lookups.append((q.key if q else f"stall{u.id}", q.utt.text if q else u.text, u))
        self._concerns(u, sents, now, out)
        self._parked(u, now, out)
        self._unanswered(u, now, out)
        self._closing(u, now, out)
        self._schedule(u, sents, now, out)
        self._untouched(now, out)
        return out

    # ------------------------------------------------------------ detectors

    def _names(self, u: Utt, out: Output) -> None:
        if u.channel != "them":
            return
        m = _INTRO.search(u.text) or _HERE.match(u.text)
        if m and m.group(1) not in _NOT_NAMES and u.speaker not in self.names:
            self.names[u.speaker] = m.group(1)
            if m.group(2):
                self.orgs[u.speaker] = m.group(2)
            out.names[u.speaker] = m.group(1)

    def _topic(self, u: Utt, now: float, out: Output) -> None:
        recent = [x for x in self.utts if len(x.text.split()) >= 4]
        if len(recent) < 6 or now - self.last_shift_t < 25:
            return
        a = np.mean([x.vec for x in recent[-3:]], axis=0)
        b = np.mean([x.vec for x in recent[-6:-3]], axis=0)
        sim = float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))
        if sim < self.T_TOPIC:
            self.last_shift_t = now
            out.topic_shift = True

    def _answers(self, u: Utt, out: Output) -> None:
        """Credit this reply to the open question it answers best (deflections answer nothing)."""
        if _DEFLECT.match(u.text):
            return
        quants = tp.quantities(u.text)
        asks_back = bool(tp.questions(u.text))  # a reply that asks something new needs a strong match
        best: tuple[float, _OpenQ] | None = None
        for q in self.open_qs:
            if q.answered or q.utt.channel == u.channel:
                continue
            turns_since = sum(1 for x in self.utts if x.seq > q.utt.seq and x.channel == u.channel)
            sim = float(u.vec @ q.vec)
            near = turns_since <= 2 and not asks_back and len(u.text.split()) >= 3
            slot_hit = bool(q.slot) and any(self.guide.slots[q.slot].kind == x.kind for x in quants)
            yes_no = turns_since <= 2 and q.yes_no and bool(_YESNO.match(u.text))  # "Sure." answers a yes/no question
            if (near and sim >= self.T_ANSWER_NEAR) or sim >= self.T_ANSWER_FAR or slot_hit or yes_no:
                score = sim + (0.3 if slot_hit else 0) + (0.2 if yes_no else 0)
                if best is None or score > best[0]:
                    best = (score, q)
        if best is not None:
            q = best[1]
            q.answered = True
            out.answered.append(f"G1:{q.key}")
            out.drafts.append(Draft(key=f"q:{q.key}", type="question", text=q.text, status="done",
                                    speaker=_owner_label(q.utt.channel, q.utt.speaker, self.names),
                                    answer=tp.short(u.text, 25), evidence=[q.utt.id, u.id], quote=q.text))

    def _criteria(self, u: Utt, out: Output) -> None:
        low = u.text.lower()
        for c in self.criteria:
            if c.status == "covered":
                continue
            kw = any(_has(w, low) for w in c.keywords)
            sim = float((c.vecs @ u.vec).max(initial=0)) if c.vecs.shape[0] else 0.0
            if not (kw or sim >= self.T_CRITERION):
                continue
            substantive = u.channel == "them" and not u.text.strip().endswith("?") and len(u.text.split()) >= 5
            new = "covered" if substantive or (u.channel == "you" and not tp.questions(u.text) and sim >= self.T_CRITERION + 0.06) else "asked"
            if new != c.status and not (c.status == "asked" and new == "asked"):
                c.status = new
                c.evidence.append(u.id)
                out.criteria_changed = True
                if new == "covered":
                    out.answered.append(f"G4:{c.id}")

    def _questions(self, u: Utt, qs: list[str], now: float, out: Output) -> None:
        # "Does it integrate with HubSpot, and what is the API rate limit?" is two questions: track both, so an
        # answer to the first doesn't hide the second.
        parts = []
        for q in qs:
            pieces = re.split(r",?\s+and\s+(?=(?:what|how|when|where|who|which|why|is|are|do|does|can|could|will|would)\b)",
                              q, flags=re.I)
            parts += [x.strip().rstrip("?") + "?" if len(pieces) > 1 else x for x in pieces]
        qs = parts
        for qtext in qs:
            if _CLOSING.search(qtext) or _PROCEDURAL.match(qtext):
                continue  # "shall we start?", "anything else before we wrap up?" don't need tracking
            qvec = self.embed([qtext])[0] if len(qs) > 1 else u.vec
            slot = next((sid for sid, s in self.guide.slots.items() if any(_has(t, qtext) for t in s.triggers)), "")
            # Already known? (G9) Only for your own questions: don't make anyone repeat themselves.
            if u.channel == "you":
                known = self._known_answer(qtext, qvec, slot)
                if known is not None:
                    key, label, ref = known
                    out.candidates.append(Candidate(
                        key=f"G9:{key}", gap_type="G9", kind="fact", text=label,
                        why="This was already answered", importance=5, confidence=0.9, t=now,
                        evidence=[u.id], source_ref=ref, ttl=30))
                    continue
            self._q_counter += 1
            key = f"{u.id}.{self._q_counter}"
            self.open_qs.append(_OpenQ(key=key, utt=u, text=qtext, vec=qvec, slot=slot,
                                       yes_no=bool(re.match(r"^(?:[^,?]{1,30},\s*)?(can|could|would|will|do|does|did|is|are|was|were|should|shall|have|has|may)\b",
                                                            qtext, re.I))))
            out.drafts.append(Draft(key=f"q:{key}", type="question", text=qtext, status="open",
                                    speaker=_owner_label(u.channel, u.speaker, self.names), evidence=[u.id], quote=qtext))
            if u.channel == "them":
                # A short question leans on what was just said ("Can you confirm that's in the contract?").
                query = qtext if len(qtext.split()) >= 8 else u.text
                out.lookups.append((key, query, u))

    def _known_answer(self, qtext: str, qvec: np.ndarray, slot: str) -> tuple[str, str, str] | None:
        """(dedupe key, nudge text, source ref) if the answer is already known."""
        if slot:
            k = self.memory.slot_value(slot)
            if k is not None:
                return (f"item{k.item_id}", f"Known: {self.guide.slots[slot].label} {k.value.get('text', k.text)} — {k.who}, {k.when}",
                        f"item:{k.item_id}")
            if slot in self.session_slots:
                val, who, uid = self.session_slots[slot]
                return (f"slot{slot}", f"Already said: {self.guide.slots[slot].label} {val['text']} — {who}", f"utt:{uid}")
        for k, score in self.memory.nearest(qvec, k=3):
            if score < self.T_KNOWN:
                break
            if k.type == "question":
                if not k.answer:
                    continue
                return f"item{k.item_id}", f"Known: {tp.short(k.answer, 12)} (answered {k.when})", f"item:{k.item_id}"
            return f"item{k.item_id}", f"Known: {tp.short(k.text, 12)} — {k.who}, {k.when}", f"item:{k.item_id}"
        return None

    def _slots(self, u: Utt, s: str, who: str, now: float, out: Output) -> None:
        for q in tp.quantities(s):
            hit = self._slot_of(s, q)
            if not hit:
                continue
            sid, slot = hit
            val = q.as_dict()
            out.drafts.append(Draft(key=f"slot:{sid}:{u.id}:{q.text}", type="fact",
                                    text=f"{slot.label.capitalize()}: {q.text}" + (" (stated by you)" if u.channel == "you" else ""),
                                    status="agreed" if u.channel == "you" else "open", speaker=who, slot=sid,
                                    value=val, evidence=[u.id], quote=s, confidence=0.9))
            if u.channel == "them":
                prev = self.session_slots.get(sid)
                known = self.memory.slot_value(sid)
                if known is not None and known.value and tp.conflicting(known.value, val):
                    out.candidates.append(Candidate(
                        key=f"G7:{sid}", gap_type="G7", kind="confirm",
                        text=f"Confirm: {known.who} said {known.value.get('text')} on {known.when} — which is current?",
                        why=f"{slot.label.capitalize()} differs from what's on record", importance=5, confidence=0.9,
                        t=now, evidence=[u.id], source_ref=f"item:{known.item_id}", ttl=180))
                elif prev is not None and tp.conflicting(prev[0], val) and not (
                        prev[1] == "you" and val.get("kind") in ("money", "percent")):  # a counter-offer, not a contradiction
                    out.candidates.append(Candidate(
                        key=f"G7:{sid}:s", gap_type="G7", kind="confirm",
                        text=f"Confirm: earlier {prev[1]} said {prev[0]['text']} — which is right?",
                        why=f"Two different values for {slot.label} in this conversation", importance=5,
                        confidence=0.85, t=now, evidence=[prev[2], u.id], ttl=180))
                self.session_slots[sid] = (val, who, u.id)
                self.their_vals[sid] = val
            else:  # Promise Guard (numbers): your claim vs the most relevant line in your own documents
                best = None
                is_claim = not s.strip().endswith("?") and not tp.questions(s)
                for f in self.memory.doc_facts if is_claim else []:
                    if f.slot == sid and f.direction in ("min", "max") and tp.overpromise(val, f.value, f.direction):
                        sim = float(u.vec @ f.vec) if f.vec is not None else 0.45
                        if sim >= 0.4 and (best is None or sim > best[0]):
                            best = (sim, f)
                if best:
                    f = best[1]
                    out.candidates.append(Candidate(
                        key=f"G8:{f.id}", gap_type="G8", kind="caution",
                        text=f"Caution: {tp.short(f.text, 12)} [{f.ref}]",
                        why=f"You said {q.text}; your documents say otherwise", importance=5, confidence=0.92,
                        t=now, evidence=[u.id], source_ref=f"docfact:{f.id}", ttl=45))
                self.session_slots.setdefault(sid, (val, "you", u.id))
                self._concession(u, s, sid, slot, q, val, now, out)
                self.my_vals[sid] = (val, u.id)

    def _concession(self, u: Utt, s: str, sid: str, slot: Slot, q: tp.Quantity, val: dict, now: float,
                    out: Output) -> None:
        """G14: you moved your number toward theirs without asking for anything back."""
        mine, theirs = self.my_vals.get(sid), self.their_vals.get(sid)
        if mine is None or theirs is None or val.get("kind") not in ("money", "percent", "duration"):
            return
        old = mine[0]
        mid = lambda v: (v["lo"] + v["hi"]) / 2  # noqa: E731
        moved = abs(mid(old) - mid(val)) > 0.01 * max(abs(mid(old)), abs(mid(val)), 1e-9)  # any real move (not 10% tol.)
        if old.get("kind") != val.get("kind") or not moved or _GIVE_GET.search(s):
            return
        if abs(mid(val) - mid(theirs)) >= abs(mid(old) - mid(theirs)):
            return  # not a move toward them
        out.candidates.append(Candidate(
            key=f"G14:{sid}:{u.id}", gap_type="G14", kind="caution",
            text=f"Caution: you moved from {old['text']} to {q.text} — ask for something in return",
            why="Giving ground with nothing back (e.g. a longer term, faster payment, a case study)", importance=5,
            confidence=0.85, t=now, evidence=[mine[1], u.id], source_ref=f"utt:{mine[1]}", ttl=60))

    def _conditions(self, u: Utt, s: str, who: str, now: float, out: Output) -> None:
        m = _CONDITION.search(s)
        if not m or not _OFFER_WORDS.search(s) or s.strip().endswith("?"):
            return
        cond = tp.short(m.group(2), 8)
        if len(cond.split()) < 2:
            return
        out.drafts.append(Draft(key=f"cond:{u.id}", type="proposal", text=tp.short(s, 25), status="conditional",
                                speaker=who, condition=cond, evidence=[u.id], quote=s, confidence=0.8))
        if u.channel == "them":
            out.candidates.append(Candidate(
                key=f"G6:{u.id}", gap_type="G6", kind="confirm",
                text=f"Confirm the condition: “{m.group(1).lower()} {cond}”",
                why="An offer with a condition attached", importance=4, confidence=0.85, t=now, evidence=[u.id], ttl=120,
                meta={"context": s}))

    def _decisions(self, u: Utt, s: str, who: str, out: Output) -> None:
        if _DECISION.search(s) and not s.strip().endswith("?"):
            out.drafts.append(Draft(key=f"dec:{u.id}", type="decision", text=tp.short(s, 25), status="agreed",
                                    speaker=who, evidence=[u.id], quote=s, confidence=0.65))

    def _vague(self, u: Utt, s: str, now: float, out: Output) -> None:
        low = s.lower()
        if tp.quantities(s) or tp.due_phrase(s) or low.rstrip().endswith("?"):
            return
        for term in sorted(self.guide.vague_terms, key=len, reverse=True):
            m = tp.term_re(term).search(low)
            if not m:
                continue
            cat = self.guide.vague_terms[term]
            if cat in self.vague_seen:
                return
            if cat == "time":
                text, imp = "Ask: what date exactly — and what's driving it?", 4
            elif cat == "quantity":
                noun = _COUNT_NOUN.search(low[m.end():m.end() + 50])
                if not noun:
                    continue
                text, imp = f"Ask: roughly how many {noun.group(1)}?", 3
            elif cat == "quality":
                if not _REQUIREMENT_VERB.search(low[max(0, m.start() - 60):m.start()]):
                    continue
                text, imp = f"Ask: what does “{term}” mean for you, concretely?", 3
            else:  # cost
                text, imp = "Ask: what budget range works for you?", 4
            self.vague_seen.add(cat)
            out.candidates.append(Candidate(
                key=f"G2:{cat}", gap_type="G2", kind="ask", text=text,
                why=f"“{term}” is vague — pin it down", importance=imp, confidence=0.8, t=now, evidence=[u.id], ttl=90,
                meta={"context": s}))
            return

    def _commitments(self, u: Utt, sents: list[str], who: str, now: float, out: Output) -> None:
        if tp.due_phrase(u.text) or tp.has_clock_time(u.text):
            for o, v, d, uid in self.commitments[-3:]:
                if o == who and not d:
                    out.answered.append(f"G5:{uid}")

        # Request ("can you send X?") answered with yes by the other side.
        if self.pending_request is not None:
            req_u, actions, req_channel = self.pending_request
            if u.channel != req_channel and _AFFIRM.match(u.text) and u.seq - req_u.seq <= 2:
                self._answer_request(u, req_u, actions, who, now, out)
                self.pending_request = None
            elif u.seq - req_u.seq > 2:
                self.pending_request = None
        asked: list[tuple[str, str]] = []
        for s in sents:
            m = _REQUEST.search(tp.strip_filler(s)) or _OFFERED_REQUEST.search(s)
            if m:
                asked += _actions(m.group(1), m.group(2))
                continue
            m = _COMMIT.search(s)
            future = re.search(r"\b(later|after|tomorrow|tonight|by|next|this (?:afternoon|evening|week)|end of|once|when)\b", s, re.I)
            if m and m.group(1).lower() == "let me" and not future:
                continue  # "let me share my screen": something done right now, not a promise
            if m and not s.strip().endswith("?") and not _CONDITION.search(s):  # conditional offers aren't promises
                action = _clean_action(m.group(2), m.group(3))
                self._add_commitment(u, who, action, tp.due_phrase(s), now, out, evidence=[u.id], verb=m.group(2).lower())

        if asked:
            self.pending_request = (u, asked, u.channel)

    def _answer_request(self, u: Utt, req_u: Utt, actions: list[tuple[str, str]], who: str, now: float,
                        out: Output) -> None:
        """A yes to "can you send X, Y and Z?" If the reply names only some of them, the rest were dropped."""
        reply = _content_words(u.text)
        named = [a for a in actions if _content_words(a[0]) & reply]
        confirmed = named if named else actions
        for action, due in actions:
            if (action, due) in confirmed:
                self._add_commitment(u, who, action, tp.due_phrase(u.text) if named and len(named) == 1 else due,
                                     now, out, evidence=[req_u.id, u.id])
            else:
                requester = _owner_label(req_u.channel, req_u.speaker, self.names)
                out.drafts.append(Draft(key=f"req:{req_u.id}:{action[:20]}", type="commitment",
                                        text=f"{requester} asked you to {action}" + (f" ({due})" if due else ""),
                                        status="open", speaker=requester, owner=who, due=due,
                                        evidence=[req_u.id, u.id], quote=req_u.text, confidence=0.75))
        missing = [a for a, _ in actions if (a, _) not in confirmed]
        if missing:
            self.asked_for += missing
            out.candidates.append(Candidate(
                key=f"G1:req{req_u.id}", gap_type="G1", kind="confirm",
                text="They also asked you to: " + "; ".join(missing),
                why="You confirmed only part of what they asked for", importance=4, confidence=0.85, t=now,
                evidence=[req_u.id, u.id], ttl=120, meta={"context": req_u.text}))

    def _add_commitment(self, u: Utt, who: str, action: str, due: str, now: float, out: Output,
                        evidence: list[int], verb: str = "") -> None:
        owner_disp = "You" if who == "me" else who
        text = f"{owner_disp} will {action}" + (f" ({due})" if due else "")
        out.drafts.append(Draft(key=f"com:{u.id}:{action[:20]}", type="commitment", text=text, status="agreed",
                                speaker=who, owner=who, due=due, evidence=evidence, quote=u.text, confidence=0.8))
        if not verb and action:
            verb = action.split()[0].lower()
        third_party = who != "me" and re.match(r"(?:email|call|contact|message|ping|ask|talk to|speak to|check with)\s+"
                                                r"(?!you\b|us\b|me\b)\w+", action, re.I)  # "email billing"
        worth_a_date = not third_party and (who != "me" or verb in _DELIVERABLE
                                            or any(action.lower().startswith(d) for d in _DELIVERABLE))
        already_dated = any(o == who and d and float(v @ u.vec) >= 0.75 for o, v, d, uid in self.commitments)
        self.commitments.append((who, u.vec, due, u.id))
        if not due and worth_a_date and not already_dated:
            subject = "you" if who == "me" else who
            out.candidates.append(Candidate(
                key=f"G5:{u.id}", gap_type="G5", kind="confirm", text=f"Confirm: by when will {subject} {action}?",
                why="A commitment without a date", importance=4, confidence=0.8, t=now, evidence=evidence, ttl=120,
                meta={"context": u.text}))

    def _schedule(self, u: Utt, sents: list[str], now: float, out: Output) -> None:
        """G11: a meeting or call was fixed for a day, but nobody said what time."""
        open_plans = [p for p in self.plans if not p.reported and not p.timed]
        if tp.has_clock_time(u.text):
            for p in open_plans:  # a real time settles every open plan (people rarely repeat the day)
                p.timed = True
        else:
            for p in open_plans:
                if p.agreed:
                    p.since += 1
        for sent in sents:
            day = tp.day_phrase(sent)
            question = sent.strip().endswith("?")
            event = _EVENT.search(sent)
            key = day.lower().split()[-1] if day else ""
            deadline = bool(day) and re.search(r"\b(?:by|before|until|till)\s+(?:the\s+)?$",
                                               sent[:sent.lower().find(day.lower())], re.I) is not None
            plan = next((p for p in self.plans if not p.reported and key and p.day.lower().split()[-1] == key), None)
            if day and event and not deadline:
                if plan is None:
                    plan = _Plan(utt=u, day=day, event=event.group(1).lower(), agreed=not question)
                    self.plans.append(plan)
                else:
                    plan.agreed = plan.agreed or not question
                    plan.day = max(plan.day, day, key=len)  # "next Thursday" beats "Thursday"
                plan.timed = plan.timed or tp.has_clock_time(sent)
            elif plan is not None and day and not question and _ACCEPT.search(sent):
                plan.agreed = True  # "Yes, Thursday works for us."
                plan.timed = plan.timed or tp.has_clock_time(sent)
            elif not day and not question and _ACCEPT.search(sent):
                recent = next((p for p in reversed(self.plans) if not p.agreed and not p.reported
                               and p.utt.channel != u.channel and u.seq - p.utt.seq <= 3), None)
                if recent is not None and len(sent.split()) <= 8:
                    recent.agreed = True  # a short "Yes, that works." in reply to a proposal
        for p in self.plans:
            if p.agreed and not p.timed and not p.reported and (p.since >= 2 or self.closing_t is not None):
                p.reported = True
                when = p.day if p.day.lower().startswith(("next", "this", "coming", "tomorrow")) else f"on {p.day}"
                out.candidates.append(Candidate(
                    key=f"G11:{p.day.lower()}", gap_type="G11", kind="confirm", text=f"Confirm: what time {when}?",
                    why=f"A {p.event} was agreed for {p.day}, but no time was set", importance=4, confidence=0.85,
                    t=now, evidence=[p.utt.id, u.id], ttl=240))

    def _acknowledge(self, u: Utt, sents: list[str], now: float, out: Output) -> None:
        """G13: they said they're upset or up against the clock, and your reply went straight past it."""
        if u.channel == "them":
            emotion = _EMOTION.search(u.text)
            urgent = None
            for s in sents:
                m = _URGENCY.search(s)
                if m and _CONSTRAINT.search(s) and not s.strip().endswith("?") and not _COMMIT.search(s):
                    urgent = _clause(s, m.start(), m.end())
                    break
            if urgent:  # they told you their deadline: the "timeline" goal is covered
                for c in self.criteria:
                    if c.id == "timeline" and c.status != "covered":
                        c.status = "covered"
                        c.evidence.append(u.id)
                        out.criteria_changed = True
                        out.answered.append("G4:timeline")
            if emotion or urgent:
                if emotion and urgent:
                    text = f"Acknowledge first: they're {emotion.group(1).lower()} — {urgent}"
                elif emotion:
                    text = f"Acknowledge first: they're {emotion.group(1).lower()}"
                else:
                    text = f"Acknowledge their timeline: “{urgent}”"
                self.pending_ack = (u, text)
            return
        if self.pending_ack is None:
            return
        them_u, text = self.pending_ack
        self.pending_ack = None
        if u.seq - them_u.seq > 2:
            return
        echoes = _content_words(them_u.text) & _content_words(u.text) & {
            w for w in _content_words(them_u.text) if w in ("budget", "board", "deadline", "thursday", "friday", "monday",
                                                           "tuesday", "wednesday", "presentation", "hours", "today")}
        if _ACKNOWLEDGE.search(u.text) or echoes:
            return
        out.candidates.append(Candidate(
            key=f"G13:{them_u.id}", gap_type="G13", kind="tip", text=text,
            why="They said this matters now; your reply didn't acknowledge it", importance=5, confidence=0.8, t=now,
            evidence=[them_u.id, u.id], ttl=60, meta={"context": them_u.text}))

    def _identifiers(self, u: Utt, now: float, out: Output) -> None:
        """Account, order and ticket numbers: remembered when they say them; shown if you ask again (G9)."""
        if u.channel == "them":
            for m in _ID.finditer(u.text):
                value = m.group(2)
                if not re.search(r"\d", value):
                    continue
                label = m.group(1).lower()
                who = _owner_label(u.channel, u.speaker, self.names)
                self.ids[label] = (value, who, u.id)
                out.drafts.append(Draft(key=f"id:{label}:{value}", type="fact", text=f"{label.capitalize()} number: {value}",
                                        status="open", speaker=who, evidence=[u.id], quote=u.text, confidence=0.95))
            return
        m = _ASK_ID.search(u.text)
        if m and m.group(1).lower() in self.ids and (tp.questions(u.text) or re.search(r"\b(repeat|again|confirm)\b", u.text, re.I)):
            value, who, uid = self.ids[m.group(1).lower()]
            out.candidates.append(Candidate(
                key=f"G9:id:{m.group(1).lower()}", gap_type="G9", kind="fact",
                text=f"Already said: {m.group(1).lower()} number {value} — {who}", why="They already told you",
                importance=5, confidence=0.95, t=now, evidence=[u.id], source_ref=f"utt:{uid}", ttl=30))

    def _tasks(self, u: Utt, sents: list[str], now: float, out: Output) -> None:
        """G15: "someone needs to update the sandbox keys" — and nobody says "I'll do it"."""
        claimed = bool(_COMMIT.search(u.text)) or bool(_REQUEST.search(tp.strip_filler(u.text)))
        for t in self.tasks:
            if not t.owned and claimed and (t.words & _content_words(u.text) or u.seq - t.utt.seq <= 1):
                t.owned = True
        for s in sents:
            m = _UNOWNED.search(s)
            action = _clean_action(m.group(1), m.group(2)) if m else ""
            if not m:
                m2 = _LETS_MAKE_SURE.search(s)
                if m2:
                    action = f"get the {m2.group(1)} {m2.group(2)}"
            if action:
                words = _content_words(action)
                if not any(t.words & words for t in self.tasks):
                    self.tasks.append(_Task(utt=u, action=action, words=words))
        moving_on = bool(_MOVE_ON.search(u.text))
        for t in self.tasks:
            if t.owned or t.reported:
                continue
            if u.seq - t.utt.seq >= 2 or moving_on or self.closing_t is not None:
                t.reported = True
                out.candidates.append(Candidate(
                    key=f"G15:{t.utt.id}", gap_type="G15", kind="ask", text=f"Ask: who will {t.action} — and by when?",
                    why="A task came up but nobody took it on", importance=5, confidence=0.85, t=now,
                    evidence=[t.utt.id], ttl=120, meta={"context": t.utt.text}))

    def unowned(self) -> list[str]:
        return [t.action for t in self.tasks if not t.owned]

    def _timeline(self, u: Utt, sents: list[str], now: float, out: Output) -> None:
        """G7: something goes live before the thing it depends on is ready (campaign Nov 15, build Nov 18)."""
        for s in sents:
            d = _date_of(s)
            if d is None or s.strip().endswith("?"):
                continue
            ready, launch = _READY.search(s), _LAUNCH.search(s)
            if not (ready or launch) or (ready and launch):
                continue
            kind, m = ("ready", ready) if ready else ("launch", launch)
            entry = (kind, d[0], d[1], d[2], m.group(1).lower(), u.id)
            self.dates.append(entry)
            for other in self.dates[:-1]:
                if other[0] == kind:
                    continue
                r, lch = (entry, other) if kind == "ready" else (other, entry)
                same_month = not r[1] or not lch[1] or r[1] == lch[1]
                earlier = (lch[1], lch[2]) < (r[1], r[2]) if r[1] and lch[1] and r[1] != lch[1] else lch[2] < r[2]
                if (same_month or r[1] != lch[1]) and earlier:
                    out.candidates.append(Candidate(
                        key=f"G7:tl:{lch[5]}:{r[5]}", gap_type="G7", kind="confirm",
                        text=f"Check the timeline: {lch[4]} {lch[3]}, but {r[4]} only by {r[3]}",
                        why="Something launches before what it depends on is ready", importance=5, confidence=0.85,
                        t=now, evidence=[lch[5], r[5]], ttl=180))

    def _drift(self, u: Utt, now: float, out: Output) -> None:
        """G16: a debate about something for later (a next version, a redesign) in a meeting about something else."""
        if self.drift_reported or len(self.utts) < 2:
            return
        prev = self.utts[-2]
        scope = _LATER_SCOPE.search(prev.text) or _LATER_SCOPE.search(u.text)
        if not scope or scope.group(1).lower() in self.agenda:
            return
        if not (_DEBATE.search(u.text) and (_DEBATE.search(prev.text) or prev.speaker != u.speaker or prev.channel != u.channel)):
            return
        src = prev.text if _LATER_SCOPE.search(prev.text) else u.text
        tm = re.search(r"\b(?:about|discuss|talk about|on)\s+(?:the\s+)?([\w .-]{3,40}?)(?:\?|,|\.|$)", src, re.I)
        topic = tp.short(tm.group(1), 6) if tm and _LATER_SCOPE.search(tm.group(1)) else scope.group(1)
        self.drift_reported = True
        out.candidates.append(Candidate(
            key=f"G16:{u.id}", gap_type="G16", kind="ask", text=f"Off the agenda: “{topic}” — park it for another meeting?",
            why="The discussion moved to a later topic", importance=4, confidence=0.75, t=now,
            evidence=[prev.id, u.id], ttl=90))

    def unscheduled(self) -> list[str]:
        """Agreed plans that still have no time (for the wrap-up card)."""
        return [f"{p.event} {p.day}" for p in self.plans if p.agreed and not p.timed]

    def _concerns(self, u: Utt, sents: list[str], now: float, out: Output) -> None:
        if u.channel == "you":
            qs = tp.questions(u.text)
            for c in self.concerns:
                if c.addressed:
                    continue
                c.you_turns_after += 1
                # Exploring a concern means asking about it (a statement in reply doesn't count).
                if qs and (c.you_turns_after == 1 or float(u.vec @ c.vec) >= self.T_ADDRESSED):
                    c.addressed = True
                    out.answered.append(f"G3:{c.key}")
            return
        for s in sents:
            strong = _CONCERN_STRONG.search(s)
            weak = _CONCERN_WEAK.search(s)
            if not (strong or weak) or s.strip().endswith("?"):
                continue
            key = f"{u.id}"
            vec = self.embed([s])[0] if len(sents) > 1 else u.vec
            out.drafts.append(Draft(key=f"con:{key}", type="concern", text=tp.short(s, 25), status="open",
                                    speaker=_owner_label(u.channel, u.speaker, self.names), evidence=[u.id], quote=s))
            if strong:
                self.concerns.append(_Concern(key=key, utt=u, text=s, vec=vec))
                out.concern_sharpen.append((f"G3:{key}", s))
            return

    def _parked(self, u: Utt, now: float, out: Output) -> None:
        m = _PARKED.search(u.text)
        if not m:
            return
        topic = next((g for g in m.groups() if g), "").strip()
        topic = re.sub(r"^(?:the|that|this|our|your)\s+(?=\w+\s+\w)", "", topic, flags=re.I)
        if topic.lower() in _VAGUE_TOPIC:  # "let's park that": the topic is what was said just before
            if self.parked and u.id - self.parked[-1][1] <= 2:
                return  # agreeing to park the topic someone just named
            prev = next((x for x in reversed(self.utts[:-1]) if len(x.text.split()) >= 4 and not _PARKED.search(x.text)), None)
            if prev is None:
                return
            topic = prev.text
        topic = tp.short(topic, 8)
        if all(topic != t for t, _ in self.parked):
            self.parked.append((topic, u.id))

    def _unanswered(self, u: Utt, now: float, out: Output) -> None:
        for q in self.open_qs:
            if q.answered or q.reported:
                continue
            later = [x for x in self.utts if x.seq > q.utt.seq]
            other_turns = sum(1 for x in later if x.channel != q.utt.channel)
            if len(later) >= 5 and other_turns >= 2:
                q.reported = True
                quoted = tp.short(q.text, 9)
                if q.utt.channel == "them":
                    out.candidates.append(Candidate(
                        key=f"G1:{q.key}", gap_type="G1", kind="confirm", text=f"They asked: “{quoted}” — not answered yet",
                        why="A question from the other side went unanswered", importance=4, confidence=0.8, t=now,
                        evidence=[q.utt.id], ttl=300, meta={"context": q.text}))
                else:
                    out.candidates.append(Candidate(
                        key=f"G1:{q.key}", gap_type="G1", kind="ask", text=f"Still unanswered: “{quoted}”",
                        why="Your question didn't get an answer", importance=3, confidence=0.8, t=now,
                        evidence=[q.utt.id], ttl=300, meta={"context": q.text}))
        for c in self.concerns:
            if c.addressed or c.reported:
                continue
            if c.you_turns_after >= 2 or (out.topic_shift and c.you_turns_after >= 1):
                c.reported = True
                topic = self._concern_topic(c.text)
                out.candidates.append(Candidate(
                    key=f"G3:{c.key}", gap_type="G3", kind="ask",
                    text=f"Ask: what worries you most about {topic}?" if topic else "Ask: what's your biggest worry here?",
                    why=f"They raised a concern that wasn't explored: “{tp.short(c.text, 10)}”", importance=4,
                    confidence=0.78, t=now, evidence=[c.utt.id], ttl=180,
                    meta={"context": c.text}))

    @staticmethod
    def _concern_topic(s: str) -> str:
        m = re.search(r"\b(?:about|with|around|regarding|of)\s+((?:the |our |your |this |that )?[\w-]+(?:\s+[\w-]+){0,3})", s, re.I)
        if m:
            return tp.short(m.group(1), 5)
        m = re.search(r"((?:the |our |your )?[\w-]+(?:\s+[\w-]+)?)\s+(?:is|are|was|were|seems?)\s+(?:a |our |the )?(?:big |real |main )?(?:concern|problem|issue|risk|worry)", s, re.I)
        return tp.short(m.group(1), 5) if m else ""

    def _closing(self, u: Utt, now: float, out: Output) -> None:
        if self.closing_t is None and _CLOSING.search(u.text):
            self.closing_t = now
            out.closing = True
            for topic, uid in self.parked:
                out.candidates.append(Candidate(
                    key=f"G10:{uid}", gap_type="G10", kind="ask", text=f"Ask: revisit “{topic}” before we close?",
                    why="You parked this earlier", importance=3, confidence=0.8, t=now, evidence=[uid], ttl=600))

    def open_goals(self) -> list[_Criterion]:
        """Goals of the conversation that are not covered yet."""
        return [c for c in self.criteria if c.status != "covered"]

    def _untouched(self, now: float, out: Output) -> None:
        """Offer the most important untouched criterion once the conversation is far enough along."""
        late = now >= max(240.0, 0.35 * self.expected) or len(self.utts) >= 40 or self.closing_t is not None
        if not (late or (out.topic_shift and now >= 120)):
            return
        open_c = [c for c in self.open_goals() if c.status == "untouched"]
        if not open_c:
            return
        c = max(open_c, key=lambda c: c.importance)
        out.candidates.append(Candidate(
            key=f"G4:{c.id}", gap_type="G4", kind="ask" if c.question.lower().startswith("ask") else "confirm",
            text=c.question, why=f"Not covered yet: {c.text}", importance=c.importance, confidence=0.8, t=now,
            evidence=[], ttl=240, meta={"criterion": c.id}))
