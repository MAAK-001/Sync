"""What Sync listens for in every conversation. There is one general guide, with no roles.

* goals: what a good conversation leaves you with (the goal, the timeline, who decides, the next step);
* slots: numbers worth tracking across calls (budget, price, go-live, response time…);
* vague terms: words worth pinning down ("soon", "a lot", "secure").
The content lives in ``guide.yaml`` next to this file.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

FILE = Path(__file__).parent / "guide.yaml"


class Slot(BaseModel):
    label: str
    triggers: list[str]
    kind: str  # money | duration | percent | count | date
    direction: str = "exact"  # for document facts: min | max | exact
    importance: int = 4


class Goal(BaseModel):
    id: str
    text: str
    question: str
    keywords: list[str] = Field(default_factory=list)
    examples: list[str] = Field(default_factory=list)
    importance: int = 4
    # True for things that stay true across calls (e.g. "who decides"): if project memory already covers them,
    # Sync won't suggest asking again. False for per-call outcomes like "next step agreed".
    persist: bool = False


class Guide(BaseModel):
    description: str
    listens_for: list[str] = Field(default_factory=list)
    goals: list[Goal]
    slots: dict[str, Slot]
    vague_terms: dict[str, str]  # term -> time | quantity | quality | cost

    def profile(self) -> str:
        """Compact description used in model prompts."""
        return f"{self.description}\nListens for: {', '.join(self.listens_for)}.\nNudge style: short, plain language."


@lru_cache(maxsize=1)
def guide() -> Guide:
    return Guide.model_validate(yaml.safe_load(FILE.read_text(encoding="utf-8")))
