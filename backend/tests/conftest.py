import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("SYNC_LLM_ENABLED", "false")


@pytest.fixture()
def db():
    """Fresh in-memory database per test."""
    from sync.config import settings
    from sync.db import init_db, session_scope

    settings.llm_enabled = False
    init_db("sqlite://")
    yield session_scope


@pytest.fixture()
def uid(db):
    """A user to own conversations and projects."""
    from demo import harness

    return harness.ensure_user("Meera")


@pytest.fixture()
def app_client(tmp_path, monkeypatch):
    """The real app on a throwaway data folder, plus a helper to make signed-in clients."""
    from fastapi.testclient import TestClient

    from sync.config import settings
    from sync.embed import embedder

    embedder._load()  # from the real models folder, before the data folder moves to a throwaway one
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "llm_enabled", False)
    from sync.main import app

    with TestClient(app) as c:
        yield c
