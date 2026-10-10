"""The gate: deterministic rules deciding whether a candidate is worth interrupting the user for.

The models propose, the gate decides: grounded-or-silent, no repeats, an attention budget,
and turn-aware timing (never while the user is speaking).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from rapidfuzz import fuzz

from .signals import Candidate

THRESHOLD = {"quiet": 0.75, "normal": 0.60, "active": 0.45}
# (min seconds between nudges, max nudges per 10 minutes)
BUDGET = {"quiet": (240.0, 2), "normal": (20.0, 8), "active": (15.0, 10)}
URGENT = {"G7", "G8", "G9", "G12", "G13", "G14"}  # contradictions, over-promises, known answers, answer cards,
# unacknowledged urgency and concessions are time-critical
URGENT_GAP = 6.0


@dataclass
class TimingContext:
    now: float
    you_speaking: bool = False
    them_speaking: bool = False
    last_you_end: float = -1e9
    last_utt_end: float = -1e9
    last_topic_shift: float = -1e9
    wrapping: bool = False


@dataclass
class Gate:
    mode: str = "normal"
    weights: dict[str, float] = field(default_factory=dict)
    pending: dict[str, Candidate] = field(default_factory=dict)
    shown_keys: set[str] = field(default_factory=set)
    shown_times: list[float] = field(default_factory=list)
    shown_texts: list[tuple[str, str]] = field(default_factory=list)  # (gap type, text)

    def offer(self, cands: list[Candidate]) -> None:
        for c in cands:
            if c.key in self.shown_keys:
                continue
            if any(g == c.gap_type and fuzz.ratio(c.text, t) >= 95 for g, t in self.shown_texts):
                continue  # the same advice already given (e.g. found by two different checks)
            if c.kind in ("caution", "fact") and not c.source_ref:
                continue  # grounded-or-silent
            old = self.pending.get(c.key)
            if old is None or c.t >= old.t:
                self.pending[c.key] = c

    def resolve(self, keys: list[str]) -> None:
        """Drop candidates that became moot (question answered, criterion covered…)."""
        for k in keys:
            self.pending.pop(k, None)
            # resolve by prefix too: "G1:12.1" resolves "G1:12.1"; "G3:12" resolves "G3:12"
            for pk in [p for p in self.pending if p.startswith(k + ":")]:
                self.pending.pop(pk, None)

    def score(self, c: Candidate, ctx: TimingContext) -> float:
        urgency = 1.0
        if ctx.last_topic_shift > c.t:
            urgency *= 1.5  # the window is closing
        if ctx.wrapping:
            urgency *= 1.5
        age = ctx.now - c.t
        if c.gap_type == "G8" and age < 20:
            urgency *= 2.0
        if c.gap_type == "G9" and age < 15:
            urgency *= 1.5
        return (c.importance / 5) * c.confidence * urgency * self.weights.get(c.gap_type, 1.0)

    def tick(self, ctx: TimingContext) -> tuple[Candidate, float] | None:
        """Return (candidate, score) to show now, or None."""
        for k in [k for k, c in self.pending.items() if ctx.now - c.t > c.ttl]:
            self.pending.pop(k)
        if not self.pending:
            return None
        if ctx.you_speaking or ctx.now - ctx.last_you_end < 1.0:
            return None  # never talk over the user
        if not (ctx.them_speaking or ctx.now - ctx.last_utt_end >= 0.6):
            return None  # wait for a natural beat
        scored = sorted(((self.score(c, ctx), c) for c in self.pending.values()), key=lambda x: -x[0])
        best_score, best = scored[0]
        if best_score < THRESHOLD.get(self.mode, 0.6):
            return None
        last = self.shown_times[-1] if self.shown_times else -1e9
        min_gap, per10 = BUDGET.get(self.mode, BUDGET["normal"])
        recent = [t for t in self.shown_times if ctx.now - t < 600]
        if best.gap_type in URGENT:
            if ctx.now - last < URGENT_GAP:
                return None
        elif ctx.now - last < min_gap or len(recent) >= per10:
            return None
        self.pending.pop(best.key)
        self.shown_keys.add(best.key)
        self.shown_times.append(ctx.now)
        self.shown_texts.append((best.gap_type, best.text))
        return best, best_score


def learned_weight(shown: int, used: int, useful: int, dismissed: int) -> float:
    """Per-user weight for a gap type from past outcomes (0.6 .. 1.4)."""
    n = max(shown, 5)
    return max(0.6, min(1.4, 1.0 + 0.4 * (used + useful - dismissed) / n))
