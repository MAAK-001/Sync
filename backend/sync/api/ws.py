"""WebSockets (sign in with ``?token=``): one per live conversation (events out; control JSON and raw audio in,
from its owner only) and one app-wide bus per user."""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .. import access, bus
from ..auth import ws_user_id
from ..db import session_scope
from ..live.engine import get_live
from ..models import Session as SessionRow

router = APIRouter()
log = logging.getLogger("sync.ws")


async def _pump(ws: WebSocket, q: asyncio.Queue) -> None:
    while True:
        await ws.send_json(await q.get())


@router.websocket("/ws/sessions/{sid}")
async def session_ws(ws: WebSocket, sid: int, token: str = ""):
    uid = ws_user_id(token)
    live = get_live(sid)
    with session_scope() as db:
        row = db.get(SessionRow, sid)
        allowed = uid is not None and row is not None and access.can_view_session(db, uid, row)
    if live is None or not allowed:
        await ws.close(code=4404)
        return
    owner = uid == live.user_id  # teammates may watch a shared call; only its owner controls it
    await ws.accept()
    q = live.subscribe()
    await ws.send_json(live.snapshot())
    sender = asyncio.create_task(_pump(ws, q))
    try:
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            if not owner:
                continue
            if msg.get("bytes") is not None:
                if live.audio is not None:
                    live.audio.feed(msg["bytes"])
                continue
            data = json.loads(msg.get("text") or "{}")
            kind = data.get("type")
            if kind == "utterance":
                await live.add_utterance(data.get("channel", "them"), data.get("text", ""), speaker=data.get("speaker", ""),
                                         t_start=data.get("t_start"), t_end=data.get("t_end"))
            elif kind == "speech":
                live.speech(data.get("channel", "them"), bool(data.get("active")))
            elif kind == "feedback":
                await live.feedback(int(data["id"]), data.get("action", "useful"))
            elif kind == "ask":
                await ws.send_json({"type": "answer", **(await live.ask(data.get("question", "")))})
            elif kind == "catchup":
                await ws.send_json({"type": "catchup", "lines": await live.catchup()})
            elif kind == "wrapup":
                live.start_wrapup()
            elif kind == "end":
                await live.end(delete=bool(data.get("delete")))
                break
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("session websocket error")
    finally:
        live.unsubscribe(q)
        sender.cancel()
        with contextlib.suppress(Exception):
            await ws.close()


@router.websocket("/ws/events")
async def events_ws(ws: WebSocket, token: str = "", client: str = ""):
    uid = ws_user_id(token)
    if uid is None:
        await ws.close(code=4401)
        return
    await ws.accept()
    q = bus.subscribe(uid)
    agent = client == "agent"
    if agent:
        bus.agents[uid] = bus.agents.get(uid, 0) + 1
    try:
        while True:
            await ws.send_json(await q.get())
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        if agent:
            bus.agents[uid] = max(0, bus.agents.get(uid, 1) - 1)
        bus.unsubscribe(q)
