"""Mask sensitive numbers before anything is stored or shown to a model."""
from __future__ import annotations

import re

_CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")
_OTP = re.compile(r"\b(otp|one[- ]time (?:password|code)|verification code|pin|cvv)\b(\s*(?:is|:|was)?\s*)(\d{3,8})\b", re.I)
_AADHAAR = re.compile(r"\b\d{4}[ -]?\d{4}[ -]?\d{4}\b")
_PAN = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")
_PASSWORD = re.compile(r"\b(password|passcode|pass code)\b(\s*(?:is|:)\s*)(\S+)", re.I)


def _luhn_ok(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
        alt = not alt
    return total % 10 == 0


def _card(m: re.Match) -> str:
    digits = re.sub(r"\D", "", m.group(0))
    return "[card number]" if 13 <= len(digits) <= 19 and _luhn_ok(digits) else m.group(0)


def redact(text: str) -> str:
    text = _CARD.sub(_card, text)
    text = _OTP.sub(lambda m: f"{m.group(1)}{m.group(2)}[code]", text)
    text = _PASSWORD.sub(lambda m: f"{m.group(1)}{m.group(2)}[password]", text)
    text = _AADHAAR.sub("[ID number]", text)
    text = _PAN.sub("[ID number]", text)
    return text
