"""One long-lived SSH connection to the server, reconnected automatically.

Everything the dashboard does on the server goes through this link:
  * run()    - a short command, optional stdin, with a timeout
  * stream() - a long-running command whose stdout is read line by line
  * a local port forward to Ollama (127.0.0.1 on the server), used for chat and model control

User-supplied text is only ever passed on stdin, never pasted into a command line.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from enum import Enum

import asyncssh

from .config import SSHConfig

log = logging.getLogger(__name__)


class LinkState(str, Enum):
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"
    STOPPED = "stopped"


class LinkDown(RuntimeError):
    """The server is not reachable right now."""


@dataclass(frozen=True)
class RunResult:
    exit_status: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.exit_status == 0


def describe_error(exc: BaseException) -> str:
    """Turn connection errors into something a person can act on."""
    if isinstance(exc, asyncssh.HostKeyNotVerifiable):
        return "server host key not in known_hosts (or it changed) - check keys/known_hosts"
    if isinstance(exc, asyncssh.PermissionDenied):
        return "SSH key was rejected - is keys/latodash_ed25519.pub in ~/.ssh/authorized_keys on the server?"
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return "timed out - server off, asleep or not on the LAN?"
    if isinstance(exc, FileNotFoundError):
        return f"file missing: {exc.filename}"
    if isinstance(exc, OSError):
        return f"network error: {exc.strerror or exc}"
    return f"{type(exc).__name__}: {exc}"


class SSHLink:
    def __init__(self, cfg: SSHConfig, *, forward_host: str = "127.0.0.1", forward_port: int = 11434) -> None:
        self._cfg = cfg
        self._forward = (forward_host, forward_port)
        self._conn: asyncssh.SSHClientConnection | None = None
        self._ready = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._listeners: list[Callable[[LinkState, str | None], None]] = []
        self.state = LinkState.STOPPED
        self.error: str | None = None
        self.local_ollama_port: int | None = None
        self.connected_since: float | None = None

    # -- lifecycle ---------------------------------------------------------------------------
    def on_state(self, fn: Callable[[LinkState, str | None], None]) -> None:
        self._listeners.append(fn)

    def _set_state(self, state: LinkState, error: str | None = None) -> None:
        self.state, self.error = state, error
        for fn in self._listeners:
            try:
                fn(state, error)
            except Exception:  # a listener must never break the link
                log.exception("link listener failed")

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._maintain(), name="ssh-link")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._conn:
            self._conn.close()
        self._set_state(LinkState.STOPPED)

    async def _connect(self) -> asyncssh.SSHClientConnection:
        c = self._cfg
        return await asyncssh.connect(
            c.host,
            port=c.port,
            username=c.user,
            client_keys=[str(c.key_file)],
            known_hosts=str(c.known_hosts),
            agent_path=None,          # only the dashboard key, never whatever is in an agent
            config=[],                # ignore ~/.ssh/config so behaviour is the same on every PC
            connect_timeout=c.connect_timeout,
            keepalive_interval=c.keepalive_interval,
            keepalive_count_max=3,
        )

    async def _maintain(self) -> None:
        loop = asyncio.get_running_loop()
        backoff = 1.0
        while True:
            self._set_state(LinkState.CONNECTING)
            err: str
            try:
                conn = await asyncio.wait_for(self._connect(), timeout=self._cfg.connect_timeout + 5)
                listener = await conn.forward_local_port("127.0.0.1", 0, *self._forward)
                self.local_ollama_port = listener.get_port()
                self._conn = conn
                self.connected_since = loop.time()
                self._ready.set()
                backoff = 1.0
                self._set_state(LinkState.CONNECTED)
                log.info("connected to %s@%s", self._cfg.user, self._cfg.host)
                await conn.wait_closed()
                err = "connection closed"
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                err = describe_error(exc)
            finally:
                self._ready.clear()
                self._conn = None
                self.local_ollama_port = None
                self.connected_since = None
            log.warning("ssh link down: %s (retry in %.0fs)", err, backoff)
            self._set_state(LinkState.ERROR, err)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)

    # -- use ---------------------------------------------------------------------------------
    async def wait_connected(self, timeout: float | None = None) -> asyncssh.SSHClientConnection:
        try:
            await asyncio.wait_for(self._ready.wait(), timeout)
        except asyncio.TimeoutError as exc:
            raise LinkDown(self.error or "not connected to the server") from exc
        if self._conn is None:
            raise LinkDown(self.error or "not connected to the server")
        return self._conn

    @property
    def ollama_base_url(self) -> str:
        if self.local_ollama_port is None:
            raise LinkDown(self.error or "not connected to the server")
        return f"http://127.0.0.1:{self.local_ollama_port}"

    async def run(self, command: str, *, input: str | None = None, timeout: float = 30.0) -> RunResult:
        conn = await self.wait_connected(timeout=5)
        try:
            res = await asyncio.wait_for(conn.run(command, input=input, check=False), timeout)
        except asyncio.TimeoutError as exc:
            raise TimeoutError(f"server command timed out after {timeout:.0f}s") from exc
        except (asyncssh.Error, OSError) as exc:
            raise LinkDown(describe_error(exc)) from exc
        return RunResult(
            exit_status=res.exit_status if res.exit_status is not None else -1,
            stdout=str(res.stdout or ""),
            stderr=str(res.stderr or ""),
        )

    async def stream(self, command: str, *, input: str | None = None) -> AsyncIterator[str]:
        """Yield stdout lines of a long-running command until it ends or the link drops."""
        conn = await self.wait_connected()
        proc = await conn.create_process(command, encoding="utf-8", errors="replace")
        try:
            if input is not None:
                proc.stdin.write(input)
            proc.stdin.write_eof()
            async for line in proc.stdout:
                yield line.rstrip("\r\n")
        finally:
            proc.close()
