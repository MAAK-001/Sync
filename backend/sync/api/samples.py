"""Sample calls: try Sync without a real call. A scripted conversation is played through the live engine in real
time as your conversation, so its nudges appear on your on-screen overlay and in the app exactly as on a call
(including the "which project?" choice). Sample calls never change what Sync learns about you."""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from ..auth import current_user
from ..models import User

router = APIRouter(tags=["samples"])
log = logging.getLogger("sync.samples")
SCRIPTS = Path(__file__).resolve().parents[2] / "demo" / "scripts"
_running: set[asyncio.Task] = set()


def _scripts() -> dict[str, dict]:
    out = {}
    for p in sorted(SCRIPTS.glob("*.json")) if SCRIPTS.exists() else []:
        data = json.loads(p.read_text(encoding="utf-8"))
        if data.get("gaps"):  # scripts with planted gaps are the interesting ones to watch
            out[p.stem] = data
    return out


@router.get("/samples")
def list_samples(user: User = Depends(current_user)):
    return [{"name": name, "title": s.get("title", name), "caller": s.get("caller", ""), "lines": len(s["utterances"])}
            for name, s in _scripts().items()]


@router.post("/samples/{name}/play")
async def play_sample(name: str, user: User = Depends(current_user)):
    if name not in _scripts():
        raise HTTPException(404, "no such sample")
    from demo import harness  # the scripted-call player lives with the demo tools

    script = harness.load_script(harness.SCRIPTS / f"{name}.json")
    uid = user.id

    async def run() -> None:
        try:
            await harness.replay(script, user_id=uid, speed=1.0)
        except Exception:  # noqa: BLE001
            log.exception("sample call %s failed", name)

    task = asyncio.create_task(run())
    _running.add(task)
    task.add_done_callback(_running.discard)
    return {"playing": name, "title": script.get("title", name), "seconds": script["duration"]}
