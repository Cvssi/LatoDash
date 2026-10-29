"""Demo mode: a simulated LatoChan server, so the UI can be tried or developed without the real one.

It feeds the real Monitor with synthetic journal entries in the real formats, which also makes it a
handy end-to-end exercise of the parsers.  Run with:  python -m latodash --demo
"""
from __future__ import annotations

import asyncio
import math
import os
import random
import tempfile
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from .app import Runtime
from .config import AppConfig
from .monitor import Monitor
from .tools import ToolError, Tools
from .workers import state_broadcaster

_cursor = 0
GB = 1024 ** 3

CHATS = [
    ("owner", "lato what time is it in tokyo"),
    ("owner", "did you remember to remind me about the form"),
    ("alice", "lato you're mid"),
    ("owner", "check this gif lol [sent gif]"),
    ("bob", "latochan whats your fav minecraft block"),
    ("owner", "ok ok im going to bed"),
]
REPLIES = ["its like 3am there. go to sleep -_-", "yeah yeah, 9pm. dont make me nag", "says you lol",
           "lmao that cat has no thoughts behind its eyes", "copper. obviously", "night. dont doomscroll"]


def _entry(ts: float, unit: str, message: str, priority: int = 6, ident: str | None = None) -> dict[str, Any]:
    global _cursor
    _cursor += 1
    e = {"__CURSOR": f"demo;{_cursor}", "__REALTIME_TIMESTAMP": str(int(ts * 1e6)), "MESSAGE": message,
         "PRIORITY": str(priority), "_SYSTEMD_UNIT": unit, "SYSLOG_IDENTIFIER": ident or unit.split(".")[0]}
    return e


def _py(ts: float, level: str, msg: str) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(ts)) + f",{int(ts * 1000) % 1000:03d} {level} {msg}"


def _gin(ts: float, status: int, latency: str, method: str, path: str) -> str:
    return (time.strftime("[GIN] %Y/%m/%d - %H:%M:%S", time.gmtime(ts))
            + f' | {status} | {latency:>12} |       127.0.0.1 | {method:<8} "{path}"')


def conversation(ts: float, task: int, who: str, text: str, reply: str, *, prompt_tok: int, gen_tok: int,
                 gen_tps: float, prompt_tps: float) -> list[dict[str, Any]]:
    """The journal lines one bot reply produces, starting at ts."""
    bot, oll = "latochan.service", "ollama.service"
    p_ms = prompt_tok / prompt_tps * 1000
    g_ms = gen_tok / gen_tps * 1000
    total = (p_ms + g_ms) / 1000
    out = [
        _entry(ts, bot, _py(ts, "INFO", f"IN   #general | {who}: {text}")),
        _entry(ts + 0.05, oll, f"slot launch_slot_: id  0 | task {task} | processing task, is_child = 0"),
        _entry(ts + 0.06, oll, f"slot   operator(): id  0 | task {task} | new prompt, n_ctx_slot = 8192, n_keep = 4, task.n_tokens = {prompt_tok + 2700}"),
    ]
    if p_ms > 4000:
        out.append(_entry(ts + p_ms / 2000, oll, f"slot print_timing: id  0 | task {task} | prompt processing, n_tokens = {prompt_tok // 2:5d}, progress = 0.50, t = {p_ms / 2000:6.2f} s / {prompt_tps:.2f} tokens per second"))
    out.append(_entry(ts + p_ms / 1000, oll, f"slot print_timing: id  0 | task {task} | prompt processing, n_tokens = {prompt_tok:5d}, progress = 1.00, t = {p_ms / 1000:6.2f} s / {prompt_tps:.2f} tokens per second"))
    end = ts + total
    out += [
        _entry(end, oll, f"slot print_timing: id  0 | task {task} | prompt eval time = {p_ms:10.2f} ms / {prompt_tok:5d} tokens ({p_ms / prompt_tok:8.2f} ms per token, {prompt_tps:8.2f} tokens per second)"),
        _entry(end, oll, f"slot print_timing: id  0 | task {task} |        eval time = {g_ms:10.2f} ms / {gen_tok:5d} tokens ({g_ms / gen_tok:8.2f} ms per token, {gen_tps:8.2f} tokens per second)"),
        _entry(end, oll, f"slot print_timing: id  0 | task {task} |       total time = {p_ms + g_ms:10.2f} ms / {prompt_tok + gen_tok:5d} tokens"),
        _entry(end, oll, f"slot      release: id  0 | task {task} | stop processing: n_tokens = {prompt_tok + gen_tok + 2700}, truncated = 0"),
        _entry(end + 0.01, oll, _gin(end, 200, f"{total + 0.03:.2f}s", "POST", "/api/chat")),
        _entry(end + 0.02, bot, _py(end, "INFO", f"RAW  ({total + 0.1:.1f}s) {reply}")),
        _entry(end + 0.4, bot, _py(end + 0.4, "INFO", f"replied to {who} in #general")),
    ]
    return out


class DemoBot:
    """Bot settings + gif library for the demo. Runs the real rules (botsettings.py / gifstore.py, vendored
    from the bot repo) against a temp directory, so validation and limits behave exactly like the server."""

    SAMPLE = {"happy": ["https://media.example.com/demo/happy-1.gif", "https://media.example.com/demo/happy-2.gif"],
              "sleepy": ["https://media.example.com/demo/sleepy-1.gif"],
              "smug": ["https://media.example.com/demo/smug-1.gif", "https://media.example.com/demo/smug-2.gif",
                       "https://media.example.com/demo/smug-3.gif"]}

    def __init__(self, cfg: AppConfig) -> None:
        self.ok = False
        self.bs: Any = None
        self.gs: Any = None
        try:
            from .vendor import botsettings, gifstore
            self.bs, self.gs = botsettings, gifstore
        except ImportError:
            return
        self.tmp = tempfile.TemporaryDirectory(prefix="latodash-demo-")
        self.settings_path = str(Path(self.tmp.name) / "settings.json")
        os.environ["GIF_DIR"] = str(Path(self.tmp.name) / "gifs")   # gifstore reads this on every call
        self.gs.apply("add", self.SAMPLE)
        self.ok = True

    def need(self) -> None:
        if not self.ok:
            raise ToolError(503, "Demo needs botsettings.py and gifstore.py (latodash/vendor or app.latochan_dir)")

    def values(self) -> dict[str, Any]:
        return self.bs.clean(self.bs.read_raw(self.settings_path)) if self.ok else {}

    def doc(self) -> dict[str, Any]:
        self.need()
        bs = self.bs
        return {"version": bs.VERSION, "values": self.values(), "defaults": bs.DEFAULTS, "schema": bs.SCHEMA,
                "groups": [{"id": g[0], "label": g[1], "help": g[2]} for g in bs.GROUPS]}

    def guard(self, fn: Any, *args: Any) -> Any:
        self.need()
        try:
            return fn(*args)
        except (ValueError, self.bs.SettingError) as exc:
            raise ToolError(422, str(exc)) from exc


class DemoServer:
    def __init__(self, mon: Monitor, cfg: AppConfig) -> None:
        self.mon, self.cfg = mon, cfg
        self.task = 40000
        self.busy_until = 0.0
        self.rng = random.Random(7)
        self.thinking = False
        self.bot = DemoBot(cfg)
        self.persona = ("# LatoChan persona (demo)\n\nyou are latochan. all lowercase, short texts, playful, a bit sassy,"
                        " never cruel.\n")
        self.memory = "# LatoChan memory export (demo)\n20\tbob\tlikes tea\n11\talice\tlikes chess\n"
        self.cli_jobs: list[tuple[float, list[dict[str, Any]]]] = []
        self.live_model = "latochan"

    def next_task(self) -> int:
        self.task += 3
        return self.task

    def backfill(self) -> None:
        now = time.time()
        entries: list[dict[str, Any]] = [
            _entry(now - 20000, "latochan.service", _py(now - 20000, "INFO", "Logged in as LatoChan#8876, model=latochan, retrieval=False")),
            _entry(now - 9000, "latochan.service", _py(now - 9000, "WARNING", "action PIN failed: Forbidden (missing Manage Messages)")),
            _entry(now - 5400, "kernel", "amdgpu 0000:03:00.0: amdgpu: SMU: response:0xFFFFFFFF for index:18 param:0x00000005", 4, "kernel"),
        ]
        entries[-1]["_TRANSPORT"] = "kernel"
        t = now - 23.5 * 3600
        while t < now - 120:
            who, text = self.rng.choice(CHATS)
            reply = self.rng.choice(REPLIES)
            entries += conversation(t, self.next_task(), who, text, reply,
                                    prompt_tok=self.rng.randint(300, 1400), gen_tok=self.rng.randint(15, 90),
                                    gen_tps=self.rng.uniform(17.5, 20.5), prompt_tps=self.rng.uniform(65, 82))
            hour = time.gmtime(t).tm_hour
            t += self.rng.uniform(300, 1500) * (4 if 1 <= hour <= 8 else 1)
        entries.sort(key=lambda e: int(e["__REALTIME_TIMESTAMP"]))
        self.mon.ingest_entries(entries, stream="backfill", live=False)
        self.mon.backfilled = True

    def sample(self, tick: int) -> dict[str, Any]:
        now = time.time()
        busy = now < self.busy_until
        r = self.rng
        cpu = (38 if busy else 3.5) + r.uniform(-2, 4) + 2 * math.sin(tick / 9)
        gpu_busy = r.randint(88, 100) if busy else r.choice([0, 0, 0, 2])
        edge = 46 + (22 if busy else 0) + r.uniform(-1, 1)
        s: dict[str, Any] = {
            "t": now, "host": "server", "uptime": 60500 + tick * 2,
            "cpu": {"pct": round(cpu, 1), "cores": [round(max(0, cpu + r.uniform(-4, 9)), 1) for _ in range(28)],
                    "count": 28, "mhz": 2400 + (900 if busy else 0) + r.randint(-60, 60), "temp": 41 + (12 if busy else 0) + r.uniform(-1, 1),
                    "load": [round(0.05 + (1.8 if busy else 0) + r.uniform(0, .2), 2), 0.4, 0.3]},
            "mem": {"total": 31 * GB, "avail": 22 * GB + r.randint(0, 5) * 2**20, "used": 9 * GB, "cached": 19 * GB,
                    "swap_total": 8 * GB, "swap_used": 78 * 2**20, "disk_total": 295 * GB, "disk_used": 81 * GB},
            "gpu": {"busy": gpu_busy, "mem_busy": gpu_busy // 2, "vram_used": int(7.21 * GB), "vram_total": int(7.98 * GB),
                    "temp_edge": round(edge, 1), "temp_junction": round(edge + (10 if busy else 2), 1), "temp_mem": round(edge + 1, 1),
                    "power_w": round((82 if busy else 10) + r.uniform(-3, 3), 1), "power_cap_w": 90,
                    "fan_rpm": 1400 + (900 if busy else 0) + r.randint(-40, 40), "fan_max": 4900, "fan_pct": 30 + (20 if busy else 0),
                    "sclk_mhz": 1500 if busy else 26, "mclk_mhz": 800, "vddgfx_mv": 1050 if busy else 750},
            "net": {"iface": "enp0s25", "rx_bps": r.uniform(800, 4000) + (60000 if busy else 0), "tx_bps": r.uniform(600, 3000)},
            "cgroups": {"latochan.service": {"cores": round(r.uniform(0, 0.04), 2), "rss": 58 * 2**20, "tasks": 6},
                        "ollama.service": {"cores": round((3.1 if busy else 0.02) + r.uniform(0, .1), 2), "rss": int(1.4 * GB), "tasks": 41}},
        }
        if tick % 5 == 0:
            since = int(now - 4300)
            s["services"] = {u: {"active": "active", "sub": "running" if u.endswith(".service") else "waiting",
                                 "result": "success", "restarts": 0, "since": since} for u in self.cfg.server.units}
            s["services"]["gpu-tune.service"] = {"active": "inactive", "sub": "dead", "result": "success", "restarts": 0, "since": None}
            s["ollama"] = {"ok": True, "latency_ms": round(r.uniform(0.6, 2.5), 1), "loaded": [
                {"name": "latochan:latest", "size": 6864314038, "vram": 6537619700, "ctx": 8192,
                 "expires": "2026-10-29T16:19:09Z", "params": "25.2B", "quant": "Q4_K_M", "family": "gemma4"}]}
            if tick % 60 == 0:
                s["ollama"]["installed"] = [
                    {"name": n, "size": int(sz * GB), "params": p, "quant": q, "family": f, "modified": "2026-10-28T03:00:00Z"}
                    for n, sz, p, q, f in [("latochan:latest", 15.9, "25.2B", "Q4_K_M", "gemma4"),
                                           ("latochan-alt:latest", 17.9, "25.2B", "Q4_K_M", "gemma4"),
                                           ("nomic-embed-text:latest", 0.27, "137M", "F16", "nomic-bert")]]
            s["settings"] = {**self.bot.values(), "thinking": self.thinking, "live_model": self.live_model}
        return s

    async def run(self) -> None:
        tick = 0
        next_chat = time.time() + 6
        while True:
            now = time.time()
            self.mon.ingest_metrics(self.sample(tick))
            if tick % 5 == 0:
                self.mon.ingest_entry(_entry(now, "ollama.service", _gin(now, 200, "71.2µs", "GET", "/api/ps")))
            if now >= next_chat and now >= self.busy_until:
                who, text = self.rng.choice(CHATS)
                pt, gt = self.rng.randint(400, 1100), self.rng.randint(20, 80)
                lines = conversation(now, self.next_task(), who, text, self.rng.choice(REPLIES),
                                     prompt_tok=pt, gen_tok=gt, gen_tps=self.rng.uniform(18, 20.5), prompt_tps=self.rng.uniform(70, 80))
                self.busy_until = now + pt / 75 + gt / 19
                asyncio.create_task(self._play(lines))
                next_chat = now + self.rng.uniform(25, 60)
            tick += 1
            await asyncio.sleep(self.cfg.sample_interval)

    async def _play(self, entries: list[dict[str, Any]]) -> None:
        start = int(entries[0]["__REALTIME_TIMESTAMP"]) / 1e6
        for e in entries:
            delay = int(e["__REALTIME_TIMESTAMP"]) / 1e6 - start - (time.time() - start)
            if delay > 0:
                await asyncio.sleep(delay)
            self.mon.ingest_entry(e)


class DemoTools:
    def __init__(self, server: DemoServer, mon: Monitor) -> None:
        self.s, self.mon = server, mon

    async def _pause(self, label: str, secs: float = 0.6) -> None:
        self.mon.add_event("info", "tool", f"[demo] {label}")
        await asyncio.sleep(secs)

    async def restart_bot(self) -> dict[str, Any]:
        await self._pause("restart LatoChan", 2)
        return {"ok": True, "output": "active\nLogged in as LatoChan#8876, model=latochan, retrieval=False"}

    async def restart_ollama(self) -> dict[str, Any]:
        await self._pause("restart Ollama", 2)
        return {"ok": True, "output": "active"}

    async def thinking(self, mode: str) -> dict[str, Any]:
        self.s.thinking = (not self.s.thinking) if mode == "toggle" else mode == "on"
        self.mon.settings = {**self.s.bot.values(), "thinking": self.s.thinking}
        if self.s.bot.ok:
            self.s.bot.guard(self.s.bot.bs.write_patch, {"thinking": self.s.thinking}, self.s.bot.settings_path)
        await self._pause(f"thinking {'on' if self.s.thinking else 'off'}", 0.2)
        return {"thinking": self.s.thinking, "output": f"thinking: {'[ON]' if self.s.thinking else '[OFF]'}"}

    # -- bot settings + gifs (real rules, temp storage)
    async def bot_settings(self) -> dict[str, Any]:
        return self.s.bot.doc()

    async def bot_settings_apply(self, values: dict[str, Any]) -> dict[str, Any]:
        b = self.s.bot
        b.need()
        b.guard(b.bs.write_patch, values, b.settings_path)
        if "thinking" in values:
            self.s.thinking = bool(b.values()["thinking"])
        self.mon.settings = {**self.mon.settings, **b.values()}
        await self._pause(f"settings saved: {', '.join(sorted(values))[:120]}", 0.2)
        return {"values": b.values()}

    async def bot_settings_reset(self, keys: list[str] | None = None) -> dict[str, Any]:
        b = self.s.bot
        b.need()
        keys = keys or list(b.bs.DEFAULTS)
        unknown = [k for k in keys if k not in b.bs.DEFAULTS]
        if unknown:
            raise ToolError(422, "unknown setting: " + ", ".join(unknown))
        b.guard(b.bs.write_patch, {k: b.bs.DEFAULTS[k] for k in keys}, b.settings_path)
        self.s.thinking = bool(b.values()["thinking"])
        self.mon.settings = {**self.mon.settings, **b.values()}
        await self._pause("settings reset", 0.2)
        return {"values": b.values()}

    async def gifs(self) -> dict[str, Any]:
        b = self.s.bot
        b.need()
        return {"moods": b.gs.all_moods(), "known": b.gs.KNOWN_MOODS,
                "limits": {"per_mood": b.gs.MAX_PER_MOOD, "moods": b.gs.MAX_MOODS}}

    async def gifs_apply(self, op: str, moods: dict[str, list[str]]) -> dict[str, Any]:
        moods = Tools.check_gif_request(op, moods)
        res = self.s.bot.guard(self.s.bot.gs.apply, op, moods)
        await self._pause(f"gifs {op}", 0.2)
        return res

    async def gifs_pc(self) -> dict[str, Any]:
        raise ToolError(409, "Importing from the PC folder isn't available in demo mode")

    async def gifs_import_pc(self, mode: str) -> dict[str, Any]:
        raise ToolError(409, "Importing from the PC folder isn't available in demo mode")

    async def gifs_export_pc(self) -> dict[str, Any]:
        raise ToolError(409, "Exporting to the PC folder isn't available in demo mode")

    async def persona_get(self) -> dict[str, Any]:
        await self._pause("download persona")
        return {"content": self.s.persona, "saved_to": None}

    async def persona_put(self, content: str) -> dict[str, Any]:
        self.s.persona = content
        await self._pause("upload persona")
        return {"backup": "/etc/latochan/persona.md.bak-demo", "saved_to": None}

    async def persona_text(self) -> str:
        return self.s.persona

    async def memory_get(self) -> dict[str, Any]:
        await self._pause("download memory")
        return {"content": self.s.memory, "saved_to": None}

    async def memory_put(self, content: str) -> dict[str, Any]:
        self.s.memory = content
        await self._pause("upload memory")
        return {"output": "imported 2 notes (backup: memory.db.bak-demo)", "saved_to": None}

    async def memory_list(self, who: str | None = None) -> dict[str, Any]:
        await self._pause("list memory", 0.2)
        return {"output": "  20  bob           2026-10-28 16:06:35  likes tea\n  11  alice            2026-10-28 16:06:35  likes chess"}

    async def say(self, text: str, channel: str | None = None) -> dict[str, Any]:
        await self._pause(f"post as LatoChan: {text[:80]}")
        return {"output": "sent 100000000000000000"}

    async def models(self) -> list[dict[str, Any]]:
        return self.mon.ollama.get("installed", [])

    async def model_load(self, name: str) -> dict[str, Any]:
        await self._pause(f"load {name}", 1.5)
        return {"ok": True, "seconds": 1.5}

    async def set_live_model(self, name: str) -> dict[str, Any]:
        self.s.live_model = name
        self.mon.settings = {**self.mon.settings, "live_model": name}
        await self._pause(f"switch LatoChan to {name}", 1.5)
        return {"ok": True, "output": f"latochan now on {name}"}

    async def model_unload(self, name: str) -> dict[str, Any]:
        await self._pause(f"unload {name}", 0.4)
        return {"ok": True}


class DemoChat:
    def __init__(self, server: DemoServer, mon: Monitor) -> None:
        self.s, self.mon = server, mon

    async def stream(self, *, messages: list[dict[str, str]], model: str | None, persona: bool, think: bool,
                     temperature: float | None, max_tokens: int) -> AsyncIterator[dict[str, Any]]:
        model = model or "latochan"
        q = messages[-1]["content"]
        self.mon.add_event("info", "cli", f"CLI → {model}: {q[:200]}")
        yield {"type": "start", "model": model}
        await asyncio.sleep(0.8)
        if think:
            for w in "they want a straight answer. keep it short.".split():
                yield {"type": "thinking", "text": w + " "}
                await asyncio.sleep(0.04)
        reply = ("hm. " if persona else "") + "this is demo mode, so i'm not really thinking. " \
            "connect me to server and ask again" + (" -_-" if persona else ".")
        words = reply.split(" ")
        for w in words:
            yield {"type": "token", "text": w + " "}
            await asyncio.sleep(0.05)
        stats = {"gen_tokens": len(words) + 4, "prompt_tokens": 412, "gen_tps": 19.4, "prompt_tps": 76.0,
                 "load_s": 0.01, "total_s": 2.4, "wall_s": 2.5, "done_reason": "stop"}
        self.mon.record_cli_generation(stats)
        self.mon.add_event("info", "cli", f"CLI ← {stats['gen_tokens']} tok @ 19.4 tok/s (2.5s)", stats)
        yield {"type": "done", "stats": stats}


def demo_runtime(cfg: AppConfig) -> Runtime:
    mon = Monitor(cfg)
    server = DemoServer(mon, cfg)
    rt = Runtime(monitor=mon, tools=DemoTools(server, mon), chat=DemoChat(server, mon),
                 start=lambda: asyncio.sleep(0), stop=lambda: asyncio.sleep(0), demo=True)

    async def start() -> None:
        mon.set_link("connected", None)
        server.backfill()
        rt.tasks += [asyncio.create_task(server.run(), name="demo"),
                     asyncio.create_task(state_broadcaster(mon, cfg.sample_interval), name="broadcast")]

    async def stop() -> None:
        for t in rt.tasks:
            t.cancel()
        await asyncio.gather(*rt.tasks, return_exceptions=True)

    rt.start, rt.stop = start, stop
    return rt
