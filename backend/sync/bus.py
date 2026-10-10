"""App-wide event bus for open app pages and desktop agents. Each event goes only to the users it concerns:
nudges to the person on the call, project progress to the project's members."""
from __future__ import annotations

import asyncio
from collections.abc import Iterable

_subs: dict[asyncio.Queue, int] = {}  # queue -> user id
agents: dict[int, int] = {}  # user id -> desktop agents connected right now (they draw the on-screen nudges)


def subscribe(user_id: int) -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=500)
    _subs[q] = user_id
    return q


def unsubscribe(q: asyncio.Queue) -> None:
    _subs.pop(q, None)


def publish(event: dict, to: Iterable[int]) -> None:
    users = set(to)
    for q, uid in list(_subs.items()):
        if uid in users:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass
