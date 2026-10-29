"""Parse systemd journal entries (journalctl -o json) into typed dashboard events.

Pure functions only - no I/O - so every rule here is unit-tested.
"""
from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field
from typing import Any

# --- event model -----------------------------------------------------------------------------

LEVELS = ("debug", "info", "warn", "error")
_ids = itertools.count(1)


@dataclass
class LogEvent:
    ts: float
    source: str          # bot | ollama | kernel | system | dashboard
    unit: str
    level: str           # debug | info | warn | error
    kind: str            # in, raw, think, replied, quiet, gin, timing, progress, systemd, tool, ...
    text: str
    data: dict[str, Any] = field(default_factory=dict)
    id: int = field(default_factory=lambda: next(_ids))

    def to_dict(self) -> dict[str, Any]:
        d = {"id": self.id, "ts": self.ts, "source": self.source, "unit": self.unit,
             "level": self.level, "kind": self.kind, "text": self.text}
        if self.data:
            d["data"] = self.data
        return d


# --- helpers ---------------------------------------------------------------------------------

_GO_DURATION = re.compile(r"(\d+(?:\.\d+)?)(h|ms|µs|us|ns|m|s)")
_UNIT_MS = {"h": 3_600_000, "m": 60_000, "s": 1000, "ms": 1, "µs": 1e-3, "us": 1e-3, "ns": 1e-6}


def parse_go_duration_ms(text: str) -> float | None:
    """'13.78s' -> 13780.0, '1m2.5s' -> 62500.0, '91.876µs' -> 0.091876"""
    text = text.strip()
    parts = _GO_DURATION.findall(text)
    if not parts or "".join(a + b for a, b in parts) != text:
        return None
    return sum(float(v) * _UNIT_MS[u] for v, u in parts)


def _message(entry: dict[str, Any]) -> str:
    msg = entry.get("MESSAGE", "")
    if isinstance(msg, list):  # journald sends binary-ish messages as a byte array
        try:
            msg = bytes(msg).decode("utf-8", "replace")
        except (TypeError, ValueError):
            msg = ""
    return str(msg)


def _priority_level(entry: dict[str, Any]) -> str:
    try:
        p = int(entry.get("PRIORITY", 6))
    except (TypeError, ValueError):
        p = 6
    return "error" if p <= 3 else "warn" if p == 4 else "debug" if p >= 7 else "info"


def entry_unit(entry: dict[str, Any]) -> str:
    if entry.get("_TRANSPORT") == "kernel":
        return "kernel"
    unit = entry.get("_SYSTEMD_UNIT") or ""
    if not unit or unit == "init.scope":  # systemd's own lines about a unit carry UNIT=
        unit = entry.get("UNIT") or entry.get("SYSLOG_IDENTIFIER") or "system"
    return unit


# --- LatoChan gateway (python logging: "2026-10-28 16:19:09,123 INFO message") -----------------

_PY_LOG = re.compile(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d+ (?P<lvl>[A-Z]+) (?P<msg>.*)$", re.S)
_RAW_SECS = re.compile(r"^RAW  \((?P<secs>[\d.]+)s(?P<think>, thinking)?\) ?(?P<reply>.*)$", re.S)
_IN = re.compile(r"^IN   #(?P<chan>[^|]*?) \| (?P<who>[^:]+):(?P<text>.*)$", re.S)
_PY_LEVEL = {"DEBUG": "debug", "INFO": "info", "WARNING": "warn", "ERROR": "error", "CRITICAL": "error"}

_BOT_PREFIXES: tuple[tuple[str, str, str | None], ...] = (
    # (startswith, kind, forced level)
    ("IN   ", "in", None),
    ("THINK", "think", None),
    ("RAW  ", "raw", None),
    ("replied to ", "replied", None),
    ("stayed quiet", "quiet", None),
    ("MEMORY", "memory", None),
    ("LLM call failed", "llm_fail", "error"),
    ("respond failed", "error", "error"),
    ("Logged in as", "login", None),
    ("catching up", "catchup", None),
    ("dropped unknown tags", "tags", None),
    ("image fetch failed", "image", "warn"),
)


def _parse_bot(msg: str, entry: dict[str, Any]) -> tuple[str, str, str, dict[str, Any]]:
    """Returns (level, kind, text, data)."""
    m = _PY_LOG.match(msg)
    if not m:
        # continuation lines (tracebacks) or library output without our prefix
        lvl = _priority_level(entry)
        if msg.startswith("Traceback"):
            return "error", "trace", msg, {"head": True}
        if msg.startswith("  File ") or re.match(r"^[\w.]+(Error|Exception)\b", msg):
            return "error", "trace", msg, {}
        return lvl, "other", msg, {}
    level = _PY_LEVEL.get(m["lvl"], "info")
    text = m["msg"]
    kind = "info"
    for prefix, k, forced in _BOT_PREFIXES:
        if text.startswith(prefix):
            kind = k
            if forced:
                level = forced
            break
    else:
        if text.startswith("action "):
            kind = "action"
            if "failed" in text or "blocked" in text:
                level = "warn"
    data: dict[str, Any] = {}
    if kind == "raw":
        r = _RAW_SECS.match(text)
        if r:
            data = {"secs": float(r["secs"]), "thinking": bool(r["think"]), "reply": r["reply"]}
    elif kind == "in":
        i = _IN.match(text)
        if i:
            data = {"channel": i["chan"].strip(), "who": i["who"].strip(), "text": i["text"].strip()}
    return level, kind, text, data


# --- Ollama / llama.cpp runner ------------------------------------------------------------------

_GIN = re.compile(
    r'^\[GIN\] (?P<date>\S+ - \S+) \| (?P<status>\d{3}) \| +(?P<lat>\S+) \| +(?P<client>\S+) \| '
    r'(?P<method>[A-Z]+) +"(?P<path>[^"]*)"'
)
_TASK = r"task (?P<task>-?\d+) \| +"
_T_PROMPT = re.compile(_TASK + r"prompt eval time = +(?P<ms>[\d.]+) ms / +(?P<tok>\d+) tokens \( *[\d.]+ ms per token, +(?P<tps>[\d.]+) tokens per second\)")
_T_EVAL = re.compile(_TASK + r"eval time = +(?P<ms>[\d.]+) ms / +(?P<tok>\d+) tokens \( *[\d.]+ ms per token, +(?P<tps>[\d.]+) tokens per second\)")
_T_TOTAL = re.compile(_TASK + r"total time = +(?P<ms>[\d.]+) ms / +(?P<tok>\d+) tokens")
_PROGRESS = re.compile(_TASK + r"prompt processing, n_tokens = +(?P<n>\d+), progress = (?P<p>[\d.]+), t = +(?P<t>[\d.]+) s / (?P<tps>[\d.]+) tokens per second")
_NEW_PROMPT = re.compile(_TASK + r"new prompt, n_ctx_slot = (?P<ctx>\d+), n_keep = \d+, task\.n_tokens = (?P<n>\d+)")
_STOP = re.compile(_TASK + r"stop processing: n_tokens = (?P<n>\d+), truncated = (?P<trunc>\d+)")
_START = re.compile(_TASK + r"processing task")
_SLOG = re.compile(r'level=(?P<lvl>[A-Z]+)\b.*?msg=(?P<msg>"(?:[^"\\]|\\.)*"|\S+)')
HEALTH_PATHS = frozenset({"/", "/api/ps", "/api/version", "/api/tags"})


def _parse_ollama(msg: str, entry: dict[str, Any]) -> tuple[str, str, str, dict[str, Any]]:
    g = _GIN.match(msg)
    if g:
        status = int(g["status"])
        path = g["path"]
        data = {"status": status, "latency_ms": parse_go_duration_ms(g["lat"]), "client": g["client"],
                "method": g["method"], "path": path, "health": path in HEALTH_PATHS}
        level = "error" if status >= 500 else "warn" if status >= 400 else ("debug" if data["health"] else "info")
        return level, "gin", msg, data
    for rx, kind in ((_T_PROMPT, "t_prompt"), (_T_EVAL, "t_eval"), (_T_TOTAL, "t_total")):
        t = rx.search(msg)
        if t:
            d = {"task": int(t["task"]), "ms": float(t["ms"]), "tokens": int(t["tok"])}
            if "tps" in t.groupdict():
                d["tps"] = float(t["tps"])
            return "info", kind, msg, d
    p = _PROGRESS.search(msg)
    if p:
        return "debug", "progress", msg, {"task": int(p["task"]), "n": int(p["n"]), "progress": float(p["p"]),
                                          "t": float(p["t"]), "tps": float(p["tps"])}
    n = _NEW_PROMPT.search(msg)
    if n:
        return "debug", "new_prompt", msg, {"task": int(n["task"]), "ctx": int(n["ctx"]), "n": int(n["n"])}
    s = _STOP.search(msg)
    if s:
        return "debug", "stop", msg, {"task": int(s["task"]), "n": int(s["n"]), "truncated": int(s["trunc"])}
    st = _START.search(msg)
    if st:
        return "debug", "task_start", msg, {"task": int(st["task"])}
    sl = _SLOG.search(msg)
    if sl:
        level = {"ERROR": "error", "WARN": "warn", "INFO": "info", "DEBUG": "debug"}.get(sl["lvl"], "info")
        return level, "slog", msg, {}
    low = msg.lower()
    if "error" in low or "panic" in low or "fatal" in low or "out of memory" in low:
        return "error", "other", msg, {}
    return "debug", "other", msg, {}


# --- systemd / kernel ------------------------------------------------------------------------

_SYSTEMD_BAD = re.compile(r"\b(Failed|failed|Main process exited, code=(?:killed|dumped)|status=[1-9]|Watchdog timeout|Start request repeated too quickly)")


def _parse_systemd(msg: str) -> tuple[str, str]:
    if _SYSTEMD_BAD.search(msg):
        return "error", "systemd"
    if msg.startswith(("Stopping", "Stopped", "Scheduled restart")):
        return "warn", "systemd"
    return "info", "systemd"


def source_for_unit(unit: str, bot_unit: str, ollama_unit: str) -> str:
    if unit == bot_unit:
        return "bot"
    if unit == ollama_unit:
        return "ollama"
    if unit == "kernel":
        return "kernel"
    return "system"


def parse_entry(entry: dict[str, Any], *, bot_unit: str = "latochan.service",
                ollama_unit: str = "ollama.service") -> LogEvent | None:
    msg = _message(entry).rstrip()
    if not msg:
        return None
    try:
        ts = int(entry["__REALTIME_TIMESTAMP"]) / 1e6
    except (KeyError, TypeError, ValueError):
        return None
    unit = entry_unit(entry)
    source = source_for_unit(unit, bot_unit, ollama_unit)
    ident = entry.get("SYSLOG_IDENTIFIER", "")
    data: dict[str, Any] = {}

    if ident == "systemd" and source != "kernel":
        level, kind = _parse_systemd(msg)
    elif source == "bot":
        level, kind, msg, data = _parse_bot(msg, entry)
    elif source == "ollama":
        level, kind, msg, data = _parse_ollama(msg, entry)
    elif source == "kernel":
        level = _priority_level(entry)
        kind = "gpu" if "amdgpu" in msg or "drm" in msg.lower() else "kernel"
    else:
        level, kind = _priority_level(entry), "service"
    return LogEvent(ts=ts, source=source, unit=unit, level=level, kind=kind, text=msg, data=data)
