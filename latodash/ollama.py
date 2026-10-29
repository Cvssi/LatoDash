"""Talks to Ollama on the server through the SSH port forward. Used by the CLI chat and model tools."""
from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from typing import Any

import httpx

from .sshlink import SSHLink

MODEL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")


class OllamaError(RuntimeError):
    pass


def valid_model(name: str) -> str:
    if not isinstance(name, str) or not MODEL_NAME.fullmatch(name) or name.endswith(':cloud'):
        raise ValueError("invalid model name")
    return name


class OllamaClient:
    def __init__(self, link: SSHLink) -> None:
        self._link = link

    def _client(self, timeout: float | None) -> httpx.AsyncClient:
        # trust_env=False: never route the forwarded loopback port through a system proxy
        return httpx.AsyncClient(base_url=self._link.ollama_base_url, timeout=timeout, trust_env=False)

    async def _post(self, path: str, body: dict[str, Any], timeout: float) -> dict[str, Any]:
        async with self._client(timeout) as c:
            r = await c.post(path, json=body)
            if r.status_code >= 400:
                raise OllamaError(f"ollama {path} HTTP {r.status_code}: {r.text[:300]}")
            return r.json() if r.content else {}

    async def tags(self) -> list[dict[str, Any]]:
        async with self._client(10) as c:
            r = await c.get("/api/tags")
            r.raise_for_status()
            return r.json().get("models", [])

    async def load(self, model: str, keep_alive: str = "24h", num_ctx: int | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {"model": valid_model(model), "keep_alive": keep_alive}
        if num_ctx:
            body["options"] = {"num_ctx": num_ctx}  # same ctx as the bot so Ollama doesn't reload later
        return await self._post("/api/generate", body, timeout=300)

    async def unload(self, model: str) -> dict[str, Any]:
        return await self._post("/api/generate", {"model": valid_model(model), "keep_alive": 0}, timeout=60)

    async def chat_stream(self, payload: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        """Yield Ollama's streamed chat chunks (dicts). Raises OllamaError on HTTP errors."""
        payload = {**payload, "stream": True}
        async with self._client(httpx.Timeout(connect=10, read=600, write=30, pool=10)) as c:
            async with c.stream("POST", "/api/chat", json=payload) as r:
                if r.status_code >= 400:
                    body = (await r.aread()).decode("utf-8", "replace")
                    raise OllamaError(f"ollama HTTP {r.status_code}: {body[:300]}")
                async for line in r.aiter_lines():
                    if line.strip():
                        chunk = json.loads(line)
                        if "error" in chunk:
                            raise OllamaError(str(chunk["error"]))
                        yield chunk
