"""FastAPI application: REST for tools, NDJSON for chat streaming, one WebSocket for live updates."""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__
from .config import AppConfig
from .monitor import Monitor
from .ollama import MODEL_NAME
from .security import LocalOnlyMiddleware
from .sshlink import SSHLink
from .tools import ChatService, ToolError, Tools
from .workers import collector_worker, journal_worker, state_broadcaster

log = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).resolve().parent / "static"


# --- runtime wiring ---------------------------------------------------------------------------

@dataclass
class Runtime:
    monitor: Monitor
    tools: Any            # Tools or demo.DemoTools
    chat: Any             # ChatService or demo.DemoChat
    start: Callable[[], Awaitable[None]]
    stop: Callable[[], Awaitable[None]]
    demo: bool = False
    tasks: list[asyncio.Task[Any]] = field(default_factory=list)


def real_runtime(cfg: AppConfig) -> Runtime:
    mon = Monitor(cfg)
    link = SSHLink(cfg.ssh, forward_host=cfg.server.ollama_host, forward_port=cfg.server.ollama_port)
    link.on_state(lambda state, err: mon.set_link(state.value, err))
    tools = Tools(link, mon, cfg)
    rt = Runtime(monitor=mon, tools=tools, chat=ChatService(tools, mon, cfg),
                 start=lambda: _noop(), stop=lambda: _noop())

    async def start() -> None:
        await link.start()
        rt.tasks += [
            asyncio.create_task(collector_worker(link, mon, cfg), name="collector"),
            asyncio.create_task(journal_worker(link, mon, cfg), name="journal"),
            asyncio.create_task(journal_worker(link, mon, cfg, kernel=True), name="journal-kernel"),
            asyncio.create_task(state_broadcaster(mon, cfg.sample_interval), name="broadcast"),
        ]

    async def stop() -> None:
        for t in rt.tasks:
            t.cancel()
        await asyncio.gather(*rt.tasks, return_exceptions=True)
        await link.stop()

    rt.start, rt.stop = start, stop
    return rt


async def _noop() -> None:
    return None


# --- request bodies ---------------------------------------------------------------------------

class TextBody(BaseModel):
    content: str = Field(max_length=600_000)


class ThinkingBody(BaseModel):
    mode: Literal["on", "off", "toggle"]


class SettingsBody(BaseModel):
    values: dict[str, bool | int | float | str] = Field(min_length=1, max_length=60)


class ResetBody(BaseModel):
    keys: list[str] = Field(default_factory=list, max_length=60)


class GifBody(BaseModel):
    op: Literal["add", "remove", "replace"]
    moods: dict[str, list[str]] = Field(min_length=1, max_length=40)


class GifImportBody(BaseModel):
    mode: Literal["add", "replace"] = "add"


class SayBody(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    channel: str | None = Field(default=None, pattern=r"^\d{15,22}$")


class ModelBody(BaseModel):
    model: str = Field(pattern=MODEL_NAME.pattern)


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=20_000)


class ChatBody(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=80)
    model: str | None = Field(default=None, pattern=MODEL_NAME.pattern)
    persona: bool = True
    think: bool = False
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_tokens: int = Field(default=1024, ge=16, le=8192)


# --- app factory --------------------------------------------------------------------------------

def create_app(cfg: AppConfig, runtime: Runtime) -> FastAPI:
    mon = runtime.monitor

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await runtime.start()
        try:
            yield
        finally:
            await runtime.stop()

    app = FastAPI(title="LatoDash", version=__version__, lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)

    @app.exception_handler(ToolError)
    async def tool_error(_: Request, exc: ToolError) -> JSONResponse:
        return JSONResponse({"error": exc.message, "output": exc.output}, status_code=exc.status)

    def client_config() -> dict[str, Any]:
        s = cfg.server
        return {
            "version": __version__, "demo": runtime.demo, "server": f"{cfg.ssh.user}@{cfg.ssh.host}",
            "default_model": s.default_model, "bot_unit": s.bot_unit, "ollama_unit": s.ollama_unit,
            "units": list(s.units), "main_channel_id": s.main_channel_id, "num_ctx": s.num_ctx,
            "sample_interval": cfg.sample_interval, "persona_path": s.persona_path,
            "thresholds": {k: getattr(cfg.thresholds, k) for k in cfg.thresholds.__dataclass_fields__},
            "pc_copies": bool(cfg.latochan_dir and cfg.latochan_dir.is_dir()),
        }

    # -- read-only ---------------------------------------------------------------------------
    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {"ok": True, "link": mon.link, "version": __version__}

    @app.get("/api/snapshot")
    async def snapshot() -> dict[str, Any]:
        return {"config": client_config(), **mon.snapshot()}

    @app.get("/api/logs")
    async def logs(source: str | None = None, level: str | None = None,
                   q: str | None = Query(default=None, max_length=200), limit: int = Query(default=1000, le=6000)):
        order = {"debug": 0, "info": 1, "warn": 2, "error": 3}
        min_level = order.get(level or "debug", 0)
        ql = (q or "").lower()
        out = [e.to_dict() for e in mon.logs
               if (not source or e.source == source) and order[e.level] >= min_level
               and (not ql or ql in e.text.lower())]
        return {"logs": out[-limit:]}

    # -- tools -------------------------------------------------------------------------------
    t = runtime.tools

    @app.post("/api/tools/restart-bot")
    async def restart_bot() -> dict[str, Any]:
        return await t.restart_bot()

    @app.post("/api/tools/restart-ollama")
    async def restart_ollama() -> dict[str, Any]:
        return await t.restart_ollama()

    @app.post("/api/tools/thinking")
    async def thinking(body: ThinkingBody) -> dict[str, Any]:
        return await t.thinking(body.mode)

    @app.get("/api/bot-settings")
    async def bot_settings() -> dict[str, Any]:
        return await t.bot_settings()

    @app.put("/api/bot-settings")
    async def bot_settings_save(body: SettingsBody) -> dict[str, Any]:
        return await t.bot_settings_apply(body.values)

    @app.post("/api/bot-settings/reset")
    async def bot_settings_reset(body: ResetBody) -> dict[str, Any]:
        return await t.bot_settings_reset(body.keys)

    @app.get("/api/gifs")
    async def gifs() -> dict[str, Any]:
        return await t.gifs()

    @app.post("/api/gifs")
    async def gifs_apply(body: GifBody) -> dict[str, Any]:
        return await t.gifs_apply(body.op, body.moods)

    @app.get("/api/gifs/pc")
    async def gifs_pc() -> dict[str, Any]:
        return await t.gifs_pc()

    @app.post("/api/gifs/pc/import")
    async def gifs_import_pc(body: GifImportBody) -> dict[str, Any]:
        return await t.gifs_import_pc(body.mode)

    @app.post("/api/gifs/pc/export")
    async def gifs_export_pc() -> dict[str, Any]:
        return await t.gifs_export_pc()

    @app.post("/api/tools/say")
    async def say(body: SayBody) -> dict[str, Any]:
        return await t.say(body.text, body.channel)

    @app.get("/api/persona")
    async def persona_get() -> dict[str, Any]:
        return await t.persona_get()

    @app.put("/api/persona")
    async def persona_put(body: TextBody) -> dict[str, Any]:
        return await t.persona_put(body.content)

    @app.get("/api/memory")
    async def memory_get() -> dict[str, Any]:
        return await t.memory_get()

    @app.put("/api/memory")
    async def memory_put(body: TextBody) -> dict[str, Any]:
        return await t.memory_put(body.content)

    @app.get("/api/memory/list")
    async def memory_list(who: str | None = Query(default=None, max_length=40)) -> dict[str, Any]:
        return await t.memory_list(who)

    @app.get("/api/models")
    async def models() -> dict[str, Any]:
        return {"models": await t.models(), "loaded": mon.ollama.get("loaded", []),
                "live": mon.settings.get("live_model") or cfg.server.default_model}

    @app.post("/api/models/load")
    async def model_load(body: ModelBody) -> dict[str, Any]:
        return await t.model_load(body.model)

    @app.post("/api/models/activate")
    async def model_activate(body: ModelBody) -> dict[str, Any]:
        return await t.set_live_model(body.model)

    @app.post("/api/models/unload")
    async def model_unload(body: ModelBody) -> dict[str, Any]:
        return await t.model_unload(body.model)

    # -- direct model chat (CLI) -------------------------------------------------------------
    @app.post("/api/chat")
    async def chat(body: ChatBody) -> StreamingResponse:
        async def gen() -> AsyncIterator[bytes]:
            async for event in runtime.chat.stream(
                messages=[m.model_dump() for m in body.messages], model=body.model, persona=body.persona,
                think=body.think, temperature=body.temperature, max_tokens=body.max_tokens,
            ):
                yield (json.dumps(event) + "\n").encode()

        return StreamingResponse(gen(), media_type="application/x-ndjson",
                                 headers={"X-Accel-Buffering": "no"})

    # -- live updates ------------------------------------------------------------------------
    @app.websocket("/ws")
    async def ws(socket: WebSocket) -> None:
        await socket.accept()
        sub = mon.subscribe()

        async def reader() -> None:
            with contextlib.suppress(WebSocketDisconnect):
                while True:
                    await socket.receive_text()  # we only listen for the close

        reader_task = asyncio.create_task(reader())
        try:
            await socket.send_json({"type": "snapshot", "data": {"config": client_config(), **mon.snapshot()}})
            while not reader_task.done():
                getter = asyncio.create_task(sub.queue.get())
                done, _ = await asyncio.wait({getter, reader_task}, return_when=asyncio.FIRST_COMPLETED)
                if getter not in done:
                    getter.cancel()
                    break
                items = [getter.result()]
                while not sub.queue.empty() and len(items) < 500:
                    items.append(sub.queue.get_nowait())
                if sub.overflowed:
                    while not sub.queue.empty():
                        sub.queue.get_nowait()
                    sub.overflowed = False
                    items = [{"type": "snapshot", "data": {"config": client_config(), **mon.snapshot()}}]
                if any(i["type"] == "resync" for i in items):
                    items = [{"type": "snapshot", "data": {"config": client_config(), **mon.snapshot()}}]
                await socket.send_json({"type": "batch", "items": items})
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            reader_task.cancel()
            mon.unsubscribe(sub)

    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    app.add_middleware(LocalOnlyMiddleware, port=cfg.listen_port)
    return app
