"""Background tasks that feed the Monitor from the server: telemetry, journal, and state broadcasts.

Each worker survives link drops: it waits for the SSH link, does its job, and on any failure
backs off briefly and starts again (resuming the journal from the last cursor it saw).
"""
from __future__ import annotations

import asyncio
import json
import logging
import shlex
from importlib import resources
from typing import Any

from .config import AppConfig
from .monitor import Monitor
from .sshlink import LinkDown, SSHLink

log = logging.getLogger(__name__)

JOURNAL_FIELDS = "MESSAGE,PRIORITY,_SYSTEMD_UNIT,UNIT,SYSLOG_IDENTIFIER,_TRANSPORT"
# Ollama is very chatty; for the 24 h backfill keep only what feeds stats, API calls and errors.
OLLAMA_BACKFILL_GREP = (
    r'print_timing: .*(eval time|total time)|stop processing|'
    r'\[GIN\](?!.*"(/|/api/ps|/api/version|/api/tags)"$)|level=(ERROR|WARN)|error'
)


def collector_script() -> str:
    return resources.files("latodash.remote").joinpath("collector.py").read_text(encoding="utf-8")


def _units_args(units: tuple[str, ...]) -> str:
    return " ".join(f"-u {shlex.quote(u)}" for u in units)


def _parse_json_lines(text: str) -> list[dict[str, Any]]:
    out = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


async def _retry_pause(name: str, exc: BaseException, delay: float) -> None:
    if not isinstance(exc, LinkDown):
        log.warning("%s: %s: %s (restarting in %.0fs)", name, type(exc).__name__, exc, delay)
    await asyncio.sleep(delay)


async def collector_worker(link: SSHLink, mon: Monitor, cfg: AppConfig) -> None:
    args = json.dumps({
        "interval": cfg.sample_interval,
        "units": list(cfg.server.units),
        "ollama": f"http://{cfg.server.ollama_host}:{cfg.server.ollama_port}",
    })
    command = f"exec python3 -u - {shlex.quote(args)}"
    script = collector_script()
    while True:
        try:
            await link.wait_connected()
            async for line in link.stream(command, input=script):
                if not line.startswith("{"):
                    continue
                try:
                    mon.ingest_metrics(json.loads(line))
                except json.JSONDecodeError:
                    log.debug("collector sent a partial line")
            raise LinkDown("collector exited")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - keep the worker alive whatever happens
            await _retry_pause("collector", exc, 3)


async def _backfill(link: SSHLink, mon: Monitor, cfg: AppConfig, until: int) -> None:
    srv = cfg.server
    since = f"--since @{until - srv.backfill_hours * 3600} --until @{until}"
    base = f"journalctl --no-pager -o json --output-fields={JOURNAL_FIELDS} {since}"
    other_units = tuple(u for u in srv.units if u != srv.ollama_unit)
    cmds = {
        "main": f"{base} {_units_args(other_units)}",
        "ollama": f"{base} -u {shlex.quote(srv.ollama_unit)} -g {shlex.quote(OLLAMA_BACKFILL_GREP)}",
        "kernel": f"{base} -k -p warning",
    }
    results = await asyncio.gather(*(link.run(c, timeout=90) for c in cmds.values()), return_exceptions=True)
    entries: list[dict[str, Any]] = []
    for name, res in zip(cmds, results, strict=True):
        if isinstance(res, BaseException):
            mon.add_event("warn", "backfill", f"history load for {name} failed: {res}")
            continue
        entries += _parse_json_lines(res.stdout)
    entries.sort(key=lambda e: int(e.get("__REALTIME_TIMESTAMP", 0)))
    mon.ingest_entries(entries, stream="backfill", live=False)
    mon.backfilled = True
    mon.publish({"type": "resync"})
    log.info("backfilled %d journal entries", len(entries))


async def _follow(link: SSHLink, mon: Monitor, stream: str, command: str) -> None:
    async for line in link.stream(command):
        if not line.startswith("{"):
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        mon.ingest_entry(entry, stream=stream, live=True)


async def journal_worker(link: SSHLink, mon: Monitor, cfg: AppConfig, *, kernel: bool = False) -> None:
    srv = cfg.server
    stream = "kernel" if kernel else "main"
    target = "-k -p warning" if kernel else _units_args(srv.units)
    while True:
        try:
            await link.wait_connected()
            last = mon.cursors.get(stream)
            if last:
                start = f"--after-cursor={shlex.quote(last)}"
            else:
                now = int((await link.run("date +%s", timeout=10)).stdout.strip())
                if not kernel and not mon.backfilled:
                    await _backfill(link, mon, cfg, now)
                start = f"--since @{now}"
            cmd = f"exec journalctl --no-pager -f -o json --output-fields={JOURNAL_FIELDS} {start} {target}"
            await _follow(link, mon, stream, cmd)
            raise LinkDown("journal stream ended")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            await _retry_pause(f"journal[{stream}]", exc, 3)


async def state_broadcaster(mon: Monitor, interval: float = 2.0) -> None:
    while True:
        await asyncio.sleep(interval)
        try:
            mon.publish({"type": "state", "data": mon.state()})
        except Exception:  # noqa: BLE001
            log.exception("state broadcast failed")
