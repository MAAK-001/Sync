"""Thin async client for Ollama: schema-constrained JSON, streaming chat and tool calls.

Design notes (CPU-only laptops):
* ``num_ctx``/``num_thread`` are fixed per model so Ollama never reloads a model between calls.
* Callers keep prompts "stable prefix first, append-only after", so Ollama reuses its KV cache and
  only the new tokens are prefilled (measured: ~25 s cold vs ~1.5 s cached on a 600-token prompt).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from .config import settings

log = logging.getLogger("sync.llm")
T = TypeVar("T", bound=BaseModel)


@dataclass
class LLMStats:
    model: str = ""
    prompt_tokens: int = 0
    prompt_s: float = 0.0
    gen_tokens: int = 0
    gen_s: float = 0.0
    wall_s: float = 0.0

    @classmethod
    def from_response(cls, model: str, r: dict, wall: float) -> LLMStats:
        return cls(
            model=model,
            prompt_tokens=r.get("prompt_eval_count", 0),
            prompt_s=r.get("prompt_eval_duration", 0) / 1e9,
            gen_tokens=r.get("eval_count", 0),
            gen_s=r.get("eval_duration", 0) / 1e9,
            wall_s=wall,
        )


@dataclass
class ChatChunk:
    content: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    done: bool = False


def json_schema(model: type[BaseModel]) -> dict:
    """Pydantic JSON schema with ``$ref``s inlined (grammar converters handle flat schemas best)."""
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return walk(defs[node["$ref"].split("/")[-1]])
            return {k: walk(v) for k, v in node.items() if k != "title"}
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(schema)


class Ollama:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self._client: httpx.AsyncClient | None = None
        self._no_think: set[str] = set()  # models that reject the "think" flag
        self.live_sessions = 0  # >0 while a call is live; deep jobs wait for it to drop

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(base_url=self.base_url, timeout=httpx.Timeout(900, connect=5))
        return self._client

    def _options(self, model: str, num_predict: int, temperature: float) -> dict:
        ctx = settings.live_ctx if model == settings.live_model else settings.deep_ctx
        return {
            "temperature": temperature,
            "num_predict": num_predict,
            "num_ctx": ctx,
            "num_thread": settings.llm_threads,
        }

    async def models(self) -> list[str]:
        r = await self.client.get("/api/tags")
        r.raise_for_status()
        return [m["name"] for m in r.json().get("models", [])]

    async def _post_chat(self, body: dict) -> dict:
        if body["model"] in self._no_think:
            body.pop("think", None)
        r = await self.client.post("/api/chat", json=body)
        if r.status_code == 400 and "think" in r.text and "think" in body:
            self._no_think.add(body["model"])
            body.pop("think")
            r = await self.client.post("/api/chat", json=body)
        if r.status_code >= 400:
            raise RuntimeError(f"Ollama {r.status_code}: {r.text[:300]}")
        return r.json()

    async def structured(
        self,
        model: str,
        messages: list[dict],
        schema: type[T],
        *,
        num_predict: int = 256,
        temperature: float = 0.0,
        retries: int = 1,
    ) -> tuple[T | None, LLMStats]:
        """Ask for JSON matching ``schema``. Returns (None, stats) if it still fails after retries."""
        body = {
            "model": model,
            "messages": messages,
            "stream": False,
            "think": False,
            "format": json_schema(schema),
            "options": self._options(model, num_predict, temperature),
            "keep_alive": "30m",
        }
        stats = LLMStats(model=model)
        for attempt in range(retries + 1):
            t0 = time.perf_counter()
            try:
                r = await self._post_chat(dict(body))
            except (httpx.HTTPError, RuntimeError) as e:
                log.warning("LLM call failed (%s): %s", model, e)
                return None, stats
            stats = LLMStats.from_response(model, r, time.perf_counter() - t0)
            content = r.get("message", {}).get("content", "")
            try:
                return schema.model_validate(json.loads(content)), stats
            except (json.JSONDecodeError, ValidationError) as e:
                log.info("LLM output invalid for %s (attempt %d): %s", schema.__name__, attempt + 1, e)
                body["options"] = {**body["options"], "temperature": 0.3}
        return None, stats

    async def warm(self, model: str, messages: list[dict]) -> LLMStats:
        """Prefill a prompt so later calls sharing its prefix hit the KV cache."""
        body = {
            "model": model,
            "messages": messages,
            "stream": False,
            "think": False,
            "options": self._options(model, 1, 0.0),
            "keep_alive": "30m",
        }
        t0 = time.perf_counter()
        try:
            r = await self._post_chat(body)
        except (httpx.HTTPError, RuntimeError) as e:
            log.warning("warm-up failed: %s", e)
            return LLMStats(model=model)
        return LLMStats.from_response(model, r, time.perf_counter() - t0)

    async def chat_stream(
        self,
        model: str,
        messages: list[dict],
        *,
        tools: list[dict] | None = None,
        num_predict: int = 700,
        temperature: float = 0.3,
    ) -> AsyncIterator[ChatChunk]:
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": True,
            "think": False,
            "options": self._options(model, num_predict, temperature),
            "keep_alive": "30m",
        }
        if tools:
            body["tools"] = tools
        if model in self._no_think:
            body.pop("think")
        async with self.client.stream("POST", "/api/chat", json=body) as r:
            if r.status_code >= 400:
                text = (await r.aread()).decode(errors="ignore")
                if "think" in text and "think" in body:
                    self._no_think.add(model)
                    async for c in self.chat_stream(model, messages, tools=tools, num_predict=num_predict,
                                                    temperature=temperature):
                        yield c
                    return
                raise RuntimeError(f"Ollama {r.status_code}: {text[:300]}")
            async for line in r.aiter_lines():
                if not line.strip():
                    continue
                data = json.loads(line)
                msg = data.get("message", {})
                yield ChatChunk(
                    content=msg.get("content", ""),
                    tool_calls=msg.get("tool_calls") or [],
                    done=bool(data.get("done")),
                )

    async def wait_until_idle(self, poll: float = 2.0) -> None:
        """Deep (T2) jobs call this so they never compete with a live call for the CPU."""
        while self.live_sessions > 0:
            await asyncio.sleep(poll)


llm = Ollama(settings.ollama_url)
