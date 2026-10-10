"""Chat with Sync any time, about everything or about one project (Server-Sent Events stream)."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import access
from ..agent import chat as agent
from ..auth import current_user
from ..db import get_db
from ..models import User

router = APIRouter(tags=["chat"])


class Msg(BaseModel):
    role: str
    content: str


class ChatIn(BaseModel):
    messages: list[Msg]
    project_id: int | None = None


@router.post("/chat")
async def chat(body: ChatIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if body.project_id is not None:
        access.require_project(db, user, body.project_id)
    uid = user.id

    async def stream():
        async for ev in agent.chat([m.model_dump() for m in body.messages], uid, body.project_id):
            yield f"data: {json.dumps(ev, default=str)}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


@router.post("/chat/confirm/{cid}")
def confirm(cid: str, user: User = Depends(current_user)):
    return agent.confirm(cid, user.id)
