"""Auto-listen rules: should Sync join this call or meeting? (auto | ask | never)

Sync joins calls automatically, but the user is always in control: a master switch, per-app rules,
contacts and title keywords to never listen to, working hours, and "pause until".
"""
from __future__ import annotations

from datetime import datetime

DEFAULTS: dict = {
    "mode": "auto",  # auto | ask | off
    "countdown": 5,  # seconds to skip before Sync starts
    "apps": {"phone link": "auto", "zoom": "auto", "teams": "auto", "meet": "auto", "webex": "auto",
             "whatsapp": "auto", "slack": "auto", "discord": "ask", "other": "ask"},
    "never_contacts": [],  # names or numbers
    "never_title_keywords": ["personal", "private", "doctor", "therapy"],
    "work_hours": None,  # e.g. {"days": [0, 1, 2, 3, 4], "start": "09:00", "end": "19:00"}
    "paused_until": None,  # ISO timestamp
    "log_skipped": True,
}


def merged(cfg: dict | None) -> dict:
    out = {**DEFAULTS, **(cfg or {})}
    out["apps"] = {**DEFAULTS["apps"], **((cfg or {}).get("apps") or {})}
    return out


def decide(cfg: dict | None, app: str, title: str = "", caller: str = "", now: datetime | None = None) -> tuple[str, str]:
    c = merged(cfg)
    now = now or datetime.now()
    if c["mode"] == "off":
        return "never", "auto-listen is off"
    if c["paused_until"]:
        try:
            if now < datetime.fromisoformat(c["paused_until"]):
                return "never", "auto-listen is paused"
        except ValueError:
            pass
    who = f"{caller} {title}".lower()
    for contact in c["never_contacts"]:
        if contact and contact.lower() in who:
            return "never", f"never listen to {contact}"
    for kw in c["never_title_keywords"]:
        if kw and kw.lower() in title.lower():
            return "never", f"title contains “{kw}”"
    wh = c["work_hours"]
    if wh:
        hm = now.strftime("%H:%M")
        if now.weekday() not in wh.get("days", range(7)) or not (wh.get("start", "00:00") <= hm <= wh.get("end", "23:59")):
            return "never", "outside working hours"
    app_rule = c["apps"].get(app.lower(), c["apps"].get("other", "ask"))
    if app_rule == "never":
        return "never", f"never listen in {app}"
    if c["mode"] == "ask" or app_rule == "ask":
        return "ask", "ask before listening"
    return "auto", "auto-listen"
