"""Runtime configuration.

Every field can be overridden with an environment variable (``SYNC_<FIELD>``) or ``backend/.env``.
"""
from __future__ import annotations

import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


def _default_data_dir() -> Path:
    # Keep the database and model weights out of the repo (it lives in OneDrive) and out of AppData
    # (packaged apps on Windows get a virtualised AppData, so files written there can be invisible to others).
    return Path(os.environ.get("SYNC_HOME", Path.home() / ".sync"))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SYNC_", env_file=BACKEND_DIR / ".env", extra="ignore")

    data_dir: Path = _default_data_dir()
    host: str = "127.0.0.1"
    port: int = 8765
    frontend_dir: str = ""  # default: Sync/frontend next to the backend folder

    # Language models served by Ollama. "live" runs during calls (T1), "deep" after them (T2).
    ollama_url: str = "http://127.0.0.1:11434"
    llm_enabled: bool = True
    # Chosen by on-device bake-offs (CPU laptop), both Apache 2.0:
    # live: granite4.1:3b judged 5/5 claims correctly with ~7 s cached latency (Qwen3.5 can't reuse the prompt cache).
    # deep: qwen3:4b wrote the most accurate post-call record (correct attribution of who said what).
    # chat: granite4.1:3b makes clean tool calls (qwen3 reasons in plain text instead when tools are on).
    live_model: str = "granite4.1:3b"
    deep_model: str = "qwen3:4b"
    chat_model: str = "granite4.1:3b"
    llm_threads: int = 6
    live_ctx: int = 8192
    deep_ctx: int = 8192

    # Sentence embeddings (local, ONNX).
    embed_model: str = "BAAI/bge-small-en-v1.5"
    embed_threads: int = 2

    # Speech recognition (sherpa-onnx). See sync/stt/models.py for the catalogue.
    # parakeet-0.6b: best accuracy incl. Indian amounts ("25 lakh"), ~11x real time on 3 CPU threads.
    # moonshine-base: lighter and 2.5x faster, slightly less accurate. See sync/stt/engine.py CATALOG.
    stt_model: str = "parakeet-0.6b"
    stt_threads: int = 3

    # Only used by demo/render_audio.py to voice fictional demo scripts.
    elevenlabs_api_key: str = ""
    elevenlabs_max_credits: int = 9000

    @property
    def db_path(self) -> Path:
        return self.data_dir / "sync.db"

    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"


settings = Settings()
