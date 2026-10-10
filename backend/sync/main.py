"""Sync backend: FastAPI app. Run with ``python -m sync.main`` (or ``sync-server``). The app is at http://127.0.0.1:8765/"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select

from .api import chat, me, projects, samples, sessions, ws
from .config import settings
from .db import init_db, session_scope
from .embed import embedder
from .events import record
from .models import Session as SessionRow
from .models import Utterance

# The app lives in Sync/frontend (plain HTML/CSS/JS, no build step). SYNC_FRONTEND points elsewhere if needed.
WEB = Path(settings.frontend_dir) if settings.frontend_dir else Path(__file__).resolve().parents[2] / "frontend"


def _load_speech_model() -> None:
    try:
        from .stt.engine import recognizer

        recognizer()
    except Exception:  # noqa: BLE001  (speech is optional until a call needs it)
        logging.getLogger("sync").warning("speech model not preloaded", exc_info=True)


def _close_interrupted() -> None:
    """Conversations still marked live/detected belong to a previous run that stopped mid-call: close them."""
    with session_scope() as db:
        for row in db.scalars(select(SessionRow).where(SessionRow.status.in_(("live", "detected")))).all():
            heard = db.scalar(select(func.count()).select_from(Utterance).where(Utterance.session_id == row.id))
            if row.status == "detected" or not heard:
                db.delete(row)  # never really started: nothing to keep
                continue
            row.status, row.ended_at = "ended", datetime.now(UTC)
            record(db, "session.ended", actor="system", user_id=row.user_id, entity="session", entity_id=row.id,
                   project_id=row.project_id, session_id=row.id, reason="Sync was restarted during this conversation")


def _resume_recaps() -> None:
    """Recaps that were being refined when Sync stopped: refine them again (they'd otherwise stay "refining")."""
    from .brain.consolidate import schedule_consolidation

    with session_scope() as db:
        ids = list(db.scalars(select(SessionRow.id).where(SessionRow.status == "ended", SessionRow.recap_status == "refining")))
    for sid in ids:
        schedule_consolidation(sid)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    _close_interrupted()
    # Load the embedding and speech models in the background so the first call is fast.
    loop = asyncio.get_running_loop()
    loop.run_in_executor(None, embedder.encode1, "warm up")
    loop.run_in_executor(None, _load_speech_model)
    _resume_recaps()
    yield


app = FastAPI(title="Sync", version="0.1.0", lifespan=lifespan,
              description="Your personal conversation twin: local, private, open source.")
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_methods=["*"],
    allow_headers=["*"],
)
for r in (me.router, projects.router, sessions.router, chat.router, samples.router, ws.router):
    app.include_router(r)
app.mount("/app", StaticFiles(directory=WEB, html=True), name="web")


@app.middleware("http")
async def _fresh_app(request, call_next):
    """The app's files are small and local: always revalidate, so an updated Sync never runs a stale mix of modules."""
    response = await call_next(request)
    if request.url.path.startswith("/app"):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/", include_in_schema=False)
def home():
    return RedirectResponse("/app/")


def run() -> None:
    import uvicorn

    from logging.handlers import RotatingFileHandler

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(settings.data_dir / "server.log", maxBytes=2_000_000, backupCount=1, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.setLevel(logging.INFO)
    logging.getLogger().addHandler(handler)  # kept next to the data for troubleshooting
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    run()
