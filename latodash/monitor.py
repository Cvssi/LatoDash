"""Central in-memory state: telemetry history, logs, LLM generations, API calls, alerts and stats.

The Monitor does no I/O. Workers feed it journal entries and telemetry samples; the web layer
reads snapshots from it and subscribes to live updates. That split keeps it fully unit-testable.
"""
from __future__ import annotations

import asyncio
import re
import time
from collections import OrderedDict, deque
from collections.abc import Callable, Iterable
from typing import Any

from .config import AppConfig
from .journal import LogEvent, parse_entry

WINDOW_S = 24 * 3600
_REPLIED_CHAN = re.compile(r" in #(\S+)")


class _CursorSet:
    """Remembers recent journal cursors so overlapping reads never double-count an entry."""

    def __init__(self, size: int) -> None:
        self._size = size
        self._seen: OrderedDict[str, None] = OrderedDict()

    def add(self, cursor: str) -> bool:
        if cursor in self._seen:
            return False
        self._seen[cursor] = None
        if len(self._seen) > self._size:
            self._seen.popitem(last=False)
        return True


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    k = (len(s) - 1) * pct / 100
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def _is_error(call: dict[str, Any]) -> bool:
    """HEAD 404s are normal existence checks (e.g. `ollama create` probing blobs)."""
    s = call["status"]
    return s == 0 or s >= 500 or (s >= 400 and call["method"] != "HEAD")


class Subscriber:
    """A bounded queue per browser connection. A slow client gets a resync instead of blocking us."""

    def __init__(self, maxsize: int = 2000) -> None:
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=maxsize)
        self.overflowed = False

    def push(self, msg: dict[str, Any]) -> None:
        try:
            self.queue.put_nowait(msg)
        except asyncio.QueueFull:
            self.overflowed = True


class Monitor:
    def __init__(self, cfg: AppConfig, *, clock: Callable[[], float] = time.time) -> None:
        self.cfg = cfg
        self.clock = clock
        history = int(30 * 60 / max(cfg.sample_interval, 0.5))
        self.metrics: deque[dict[str, Any]] = deque(maxlen=history)
        self.logs: deque[LogEvent] = deque(maxlen=6000)
        self.generations: deque[dict[str, Any]] = deque(maxlen=3000)
        self.api_calls: deque[dict[str, Any]] = deque(maxlen=3000)
        self.bot_events: deque[tuple[float, str, dict[str, Any]]] = deque(maxlen=10000)
        self.error_events: deque[LogEvent] = deque(maxlen=500)
        self.health = {"count": 0, "last_latency_ms": None, "last_ts": None}
        self.services: dict[str, Any] = {}
        self.ollama: dict[str, Any] = {}
        self.settings: dict[str, Any] = {}
        self.collector_errors: dict[str, str] = {}
        self.host: str | None = None
        self.server_uptime: float | None = None
        self.link: dict[str, Any] = {"state": "connecting", "error": None}
        self.last_metrics_at: float | None = None
        self.clock_offset = 0.0              # server time - PC time
        self.inflight: dict[str, Any] | None = None
        self.pending_reply: dict[str, Any] | None = None
        self.cursors: dict[str, str] = {}    # stream name -> last journal cursor
        self.backfilled = False
        self._partial: dict[int, dict[str, Any]] = {}
        self._seen = _CursorSet(60000)
        self._cli_recent: deque[dict[str, Any]] = deque(maxlen=20)
        self._subs: set[Subscriber] = set()
        self._last_error_ts: dict[str, float] = {}

    # -- pub/sub ------------------------------------------------------------------------------
    def subscribe(self) -> Subscriber:
        sub = Subscriber()
        self._subs.add(sub)
        return sub

    def unsubscribe(self, sub: Subscriber) -> None:
        self._subs.discard(sub)

    def publish(self, msg: dict[str, Any]) -> None:
        for sub in list(self._subs):
            sub.push(msg)

    def server_now(self) -> float:
        return self.clock() + self.clock_offset

    # -- link ---------------------------------------------------------------------------------
    def set_link(self, state: str, error: str | None) -> None:
        self.link = {"state": state, "error": error}
        self.publish({"type": "link", "data": self.link})

    # -- telemetry ----------------------------------------------------------------------------
    def ingest_metrics(self, sample: dict[str, Any]) -> None:
        now = self.clock()
        if isinstance(sample.get("t"), (int, float)):
            self.clock_offset = float(sample["t"]) - now
        self.last_metrics_at = now
        self.host = sample.get("host", self.host)
        self.server_uptime = sample.get("uptime", self.server_uptime)
        if "services" in sample:
            self.services = sample["services"] or {}
        if "ollama" in sample:
            prev_installed = self.ollama.get("installed")
            self.ollama = sample["ollama"] or {}
            if "installed" not in self.ollama and prev_installed is not None:
                self.ollama["installed"] = prev_installed
            self.ollama["checked_at"] = sample.get("t")
        if "settings" in sample and isinstance(sample["settings"], dict):
            self.settings = sample["settings"]
        self.collector_errors = sample.get("errors", {}) or {}
        light = {k: sample.get(k) for k in ("t", "cpu", "mem", "gpu", "net", "cgroups")}
        self.metrics.append(light)
        self.publish({"type": "metrics", "data": light})

    # -- journal ------------------------------------------------------------------------------
    def ingest_entries(self, entries: Iterable[dict[str, Any]], *, stream: str, live: bool) -> int:
        n = 0
        for entry in entries:
            if self.ingest_entry(entry, stream=stream, live=live):
                n += 1
        return n

    def ingest_entry(self, entry: dict[str, Any], *, stream: str = "main", live: bool = True) -> LogEvent | None:
        cursor = entry.get("__CURSOR")
        if cursor:
            if not self._seen.add(cursor):
                return None
            self.cursors[stream] = cursor
        ev = parse_entry(entry, bot_unit=self.cfg.server.bot_unit, ollama_unit=self.cfg.server.ollama_unit)
        if ev is None:
            return None
        self._process(ev, live=live)
        return ev

    def add_event(self, level: str, kind: str, text: str, data: dict[str, Any] | None = None,
                  *, source: str = "dashboard") -> LogEvent:
        ev = LogEvent(ts=self.server_now(), source=source, unit=source, level=level, kind=kind,
                      text=text, data=data or {})
        self._process(ev, live=True)
        return ev

    def _process(self, ev: LogEvent, *, live: bool) -> None:
        if not (ev.source == "ollama" and ev.kind == "gin" and ev.data.get("health")):
            self.logs.append(ev)
            if live:
                self.publish({"type": "log", "data": ev.to_dict()})
        if ev.level == "error" and self._new_incident(ev):
            self.error_events.append(ev)
            if ev.source == "bot":
                self.bot_events.append((ev.ts, "error", {}))
        if ev.source == "bot":
            self._bot(ev)
        elif ev.source == "ollama":
            self._ollama(ev, live)

    def _new_incident(self, ev: LogEvent) -> bool:
        """One crash prints many error lines (message, traceback, systemd notices). Count it once."""
        last = self._last_error_ts.get(ev.source)
        self._last_error_ts[ev.source] = ev.ts
        return last is None or ev.ts - last > 2.0

    def _bot(self, ev: LogEvent) -> None:
        k = ev.kind
        if k == "in":
            self.pending_reply = {"since": ev.ts, **ev.data}
            self.bot_events.append((ev.ts, "in", ev.data))
        elif k == "raw":
            self.pending_reply = None
            self.bot_events.append((ev.ts, "raw", ev.data))
        elif k in ("llm_fail", "error", "trace"):
            self.pending_reply = None
        elif k == "replied":
            self.bot_events.append((ev.ts, "replied", ev.data))
            chan = _REPLIED_CHAN.search(ev.text)
            self._api_call(ev.ts, "discord", "POST", f"reply #{chan.group(1) if chan else '?'}", 200, None, ev.text)
        elif k == "quiet":
            self.bot_events.append((ev.ts, "quiet", {}))
        elif k == "systemd" and ev.text.startswith(("Started", "Stopped", "Stopping")):
            self.pending_reply = None

    def _api_call(self, ts: float, service: str, method: str, path: str, status: int,
                  latency_ms: float | None, detail: str = "", client: str | None = None) -> None:
        call = {"ts": ts, "service": service, "method": method, "path": path, "status": status,
                "latency_ms": latency_ms, "detail": detail[:300]}
        if client:
            call["client"] = client
        self.api_calls.append(call)
        self.publish({"type": "api", "data": call})

    def _ollama(self, ev: LogEvent, live: bool) -> None:
        d, k = ev.data, ev.kind
        if k == "gin":
            if d.get("health"):
                self.health = {"count": self.health["count"] + 1, "last_latency_ms": d.get("latency_ms"),
                               "last_ts": ev.ts}
                return
            self._api_call(ev.ts, "ollama", d["method"], d["path"], d["status"], d.get("latency_ms"),
                           client=d.get("client"))
            return
        task = d.get("task")
        if task is None or task < 0:
            return
        if k == "task_start":
            self.inflight = {"task": task, "started": ev.ts, "phase": "queued", "updated": ev.ts}
        elif k in ("new_prompt", "progress"):
            inf = self.inflight if self.inflight and self.inflight.get("task") == task else None
            if inf is None:
                inf = self.inflight = {"task": task, "started": ev.ts, "phase": "prompt"}
            inf["updated"] = ev.ts
            if k == "new_prompt":
                inf.update(phase="prompt", ctx=d["ctx"], prompt_tokens=d["n"], progress=0.0)
            else:
                inf.update(progress=d["progress"], prompt_tps=d["tps"],
                           phase="generating" if d["progress"] >= 0.999 else "prompt")
        elif k in ("t_prompt", "t_eval"):
            self._partial.setdefault(task, {})[k] = d
            if self.inflight and self.inflight.get("task") == task:
                self.inflight["phase"] = "finishing"
        elif k == "t_total":
            part = self._partial.pop(task, {})
            pr, ev_ = part.get("t_prompt", {}), part.get("t_eval", {})
            gen = {
                "ts": ev.ts, "task": task, "source": "bot",
                "prompt_tokens": pr.get("tokens", 0), "prompt_ms": pr.get("ms", 0.0), "prompt_tps": pr.get("tps"),
                "gen_tokens": ev_.get("tokens", 0), "gen_ms": ev_.get("ms", 0.0), "gen_tps": ev_.get("tps"),
                "total_ms": d["ms"],
                "ctx": (self.inflight or {}).get("ctx") if (self.inflight or {}).get("task") == task else None,
            }
            if self._match_cli(gen):
                gen["source"] = "cli"
            self.generations.append(gen)
            if self.inflight and self.inflight.get("task") == task:
                self.inflight = None
            if live:
                self.publish({"type": "gen", "data": gen})
        elif k == "stop":
            for g in reversed(self.generations):
                if g["task"] == task:
                    g["ctx_used"] = d["n"]
                    g["truncated"] = bool(d["truncated"])
                    break
            if self.inflight and self.inflight.get("task") == task:
                self.inflight = None

    # -- CLI (dashboard) generations ------------------------------------------------------------
    def record_cli_generation(self, stats: dict[str, Any]) -> None:
        """Called when the CLI's own chat finishes; tags the matching journal generation as 'cli'."""
        self._cli_recent.append(stats)
        for g in list(self.generations)[-6:]:
            if g["source"] != "cli" and self._same(g, stats):
                g["source"] = "cli"
                self.publish({"type": "gen_update", "data": g})
                self._cli_recent.remove(stats)
                return

    @staticmethod
    def _same(g: dict[str, Any], s: dict[str, Any]) -> bool:
        return g["gen_tokens"] == s.get("gen_tokens") and abs(g["prompt_tokens"] - s.get("prompt_tokens", -999)) <= 1

    def _match_cli(self, gen: dict[str, Any]) -> bool:
        for s in list(self._cli_recent):
            if self._same(gen, s):
                self._cli_recent.remove(s)
                return True
        return False

    # -- derived views ------------------------------------------------------------------------
    def stats(self) -> dict[str, Any]:
        now = self.server_now()
        cutoff = now - WINDOW_S
        gens = [g for g in self.generations if g["ts"] >= cutoff]
        gen_tok = sum(g["gen_tokens"] for g in gens)
        gen_ms = sum(g["gen_ms"] for g in gens)
        pr_tok = sum(g["prompt_tokens"] for g in gens)
        pr_ms = sum(g["prompt_ms"] for g in gens)
        hour0 = (int(now) // 3600) * 3600 - 23 * 3600
        hourly = [{"t": hour0 + i * 3600, "prompt": 0, "gen": 0, "calls": 0} for i in range(24)]
        for g in gens:
            i = int((g["ts"] - hour0) // 3600)
            if 0 <= i < 24:
                hourly[i]["prompt"] += g["prompt_tokens"]
                hourly[i]["gen"] += g["gen_tokens"]
                hourly[i]["calls"] += 1
        bot = {"in": 0, "replied": 0, "quiet": 0, "error": 0}
        reply_secs: list[float] = []
        for ts, kind, data in self.bot_events:
            if ts < cutoff:
                continue
            if kind in bot:
                bot[kind] += 1
            if kind == "raw" and "secs" in data:
                reply_secs.append(data["secs"])
        calls = [c for c in self.api_calls if c["ts"] >= cutoff]
        lat = [c["latency_ms"] for c in calls if c["latency_ms"] is not None]
        chat_lat = [c["latency_ms"] for c in calls
                    if c["service"] == "ollama" and c["path"] == "/api/chat" and c["latency_ms"] is not None]
        last = gens[-1] if gens else None
        return {
            "window_s": WINDOW_S,
            "llm": {
                "calls": len(gens), "prompt_tokens": pr_tok, "gen_tokens": gen_tok,
                "gen_tps": round(gen_tok / (gen_ms / 1000), 2) if gen_ms else None,
                "prompt_tps": round(pr_tok / (pr_ms / 1000), 2) if pr_ms else None,
                "p50_ms": _percentile([g["total_ms"] for g in gens], 50),
                "p95_ms": _percentile([g["total_ms"] for g in gens], 95),
                "by_source": {s: sum(1 for g in gens if g["source"] == s) for s in ("bot", "cli")},
                "last": last,
            },
            "hourly": hourly,
            "bot": {**bot, "reply_avg_s": round(sum(reply_secs) / len(reply_secs), 1) if reply_secs else None,
                    "reply_max_s": max(reply_secs) if reply_secs else None},
            "api": {
                "calls": len(calls), "errors": sum(1 for c in calls if _is_error(c)),
                "avg_ms": round(sum(lat) / len(lat), 1) if lat else None,
                "chat_p95_ms": _percentile(chat_lat, 95),
                "by_service": {s: sum(1 for c in calls if c["service"] == s) for s in ("ollama", "discord")},
                "health": self.health,
            },
        }

    def alerts(self) -> list[dict[str, Any]]:
        t, srv = self.cfg.thresholds, self.cfg.server
        now_pc, now = self.clock(), self.server_now()
        out: list[dict[str, Any]] = []

        def add(level: str, key: str, title: str, detail: str = "", since: float | None = None) -> None:
            out.append({"id": key, "level": level, "title": title, "detail": detail, "since": since})

        if self.link["state"] != "connected":
            add("crit", "link", "Server unreachable", self.link.get("error") or "connecting…")
            return out
        if self.last_metrics_at is None or now_pc - self.last_metrics_at > t.telemetry_stale_s:
            add("crit", "telemetry", "No telemetry from the server",
                "The collector stopped reporting. The server may be overloaded or hung.")
        for unit in srv.units:
            s = self.services.get(unit)
            if not s:
                continue
            core = unit in (srv.bot_unit, srv.ollama_unit)
            if s.get("active") == "failed":
                add("crit", f"svc:{unit}", f"{unit} failed", f"result: {s.get('result')}")
            elif core and s.get("active") != "active":
                add("crit", f"svc:{unit}", f"{unit} is {s.get('active')}", s.get("sub") or "")
            elif s.get("active") not in ("active", "activating") and s.get("result") not in ("success", None):
                add("warn", f"svc:{unit}", f"{unit} is {s.get('active')}", f"result: {s.get('result')}")
            if core and (s.get("restarts") or 0) > 0:
                add("warn", f"restart:{unit}", f"{unit} auto-restarted {s['restarts']}×",
                    "systemd restarted it after a crash since it was last started by hand")
        if self.ollama:
            if not self.ollama.get("ok"):
                add("crit", "ollama", "Ollama API not responding", self.ollama.get("error", ""))
            else:
                if (self.ollama.get("latency_ms") or 0) > t.ollama_slow_ms:
                    add("warn", "ollama-slow", "Ollama API is slow", f"{self.ollama['latency_ms']:.0f} ms for /api/ps")
                loaded = [m.get("name", "").split(":")[0] for m in self.ollama.get("loaded", [])]
                live = self.settings.get("live_model") or srv.default_model
                if live.split(":")[0] not in loaded:
                    add("warn", "model", f"{live} is not loaded",
                        "Her next reply will include a cold load (~40 s). Use Tools → Load model to warm it.")
        if self.pending_reply:
            age = now - self.pending_reply["since"]
            if age > t.reply_pending_warn_s:
                lvl = "crit" if age > t.reply_pending_crit_s else "warn"
                add(lvl, "pending", f"Reply pending for {age:.0f}s — possible hang",
                    f"Waiting on the model for {self.pending_reply.get('who', '?')}: "
                    f"{(self.pending_reply.get('text') or '')[:120]}", self.pending_reply["since"])
        if self.inflight:
            age = now - self.inflight.get("started", now)
            if age > t.generation_stall_s:
                add("warn", "stall", f"Generation running for {age:.0f}s",
                    f"task {self.inflight.get('task')} · phase {self.inflight.get('phase')}", self.inflight["started"])
        m = self.metrics[-1] if self.metrics else None
        if m:
            gpu = m.get("gpu") or {}
            junction = gpu.get("temp_junction")
            if junction is not None and junction >= t.gpu_junction_warn:
                lvl = "crit" if junction >= t.gpu_junction_crit else "warn"
                add(lvl, "gpu-temp", f"GPU hotspot {junction:.0f} °C",
                    "The Vega fell off the PCIe bus at ~106 °C before. Check fans / repaste.")
            if gpu.get("vram_total") and gpu.get("vram_used", 0) / gpu["vram_total"] > 0.985:
                add("warn", "vram", "VRAM almost full", "A bigger context or second model may spill to RAM.")
            mem = m.get("mem") or {}
            if mem.get("disk_total") and 100 * mem["disk_used"] / mem["disk_total"] >= t.disk_warn_pct:
                add("warn", "disk", "Disk nearly full", f"{100 * mem['disk_used'] / mem['disk_total']:.0f}% used")
            if mem.get("total") and 100 * mem["avail"] / mem["total"] < t.mem_avail_warn_pct:
                add("warn", "ram", "Low memory", f"{100 * mem['avail'] / mem['total']:.0f}% available")
        recent = [e for e in self.error_events if e.ts >= now - 900 and e.source != "kernel"]
        if recent:
            add("warn", "errors", f"{len(recent)} error{'s' if len(recent) != 1 else ''} logged in the last 15 min",
                recent[-1].text[:200], recent[-1].ts)
        gpu_err = [e for e in self.error_events if e.source == "kernel" and e.ts >= now - WINDOW_S]
        if gpu_err:
            lvl = "crit" if gpu_err[-1].ts >= now - 3600 else "warn"
            add(lvl, "kernel", f"{len(gpu_err)} kernel error{'s' if len(gpu_err) != 1 else ''} (24 h)",
                gpu_err[-1].text[:200], gpu_err[-1].ts)
        if self.collector_errors:
            add("info", "collector", "Some telemetry unavailable",
                "; ".join(f"{k}: {v}" for k, v in self.collector_errors.items())[:200])
        order = {"crit": 0, "warn": 1, "info": 2}
        out.sort(key=lambda a: order[a["level"]])
        return out

    def state(self) -> dict[str, Any]:
        return {
            "link": self.link, "host": self.host, "uptime": self.server_uptime,
            "server_time": self.server_now(), "services": self.services, "ollama": self.ollama,
            "settings": self.settings, "inflight": self.inflight, "pending_reply": self.pending_reply,
            "alerts": self.alerts(), "stats": self.stats(), "backfilled": self.backfilled,
        }

    def snapshot(self, *, logs: int = 2500) -> dict[str, Any]:
        return {
            "state": self.state(),
            "metrics": list(self.metrics),
            "logs": [e.to_dict() for e in list(self.logs)[-logs:]],
            "generations": list(self.generations)[-600:],
            "api_calls": list(self.api_calls)[-1000:],
        }
