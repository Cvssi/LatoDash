"""The LatoChan admin tools, as server actions the web UI can call.

    Restart LatoChan                -> restart_bot()
    Thinking toggle                 -> thinking("toggle" | "on" | "off")
    Download persona from server    -> persona_get()   (also refreshes the local copy)
    Upload persona to server        -> persona_put()   (server keeps a timestamped .bak)
    Download memory from server     -> memory_get()    (also refreshes latochan-memory.txt on the PC)
    Upload memory to server         -> memory_put()    (lato-memory import backs up the DB itself)
    Live brain view                 -> the CLI tab (live journal stream)
  plus: restart Ollama, list notes, post as LatoChan (lato-say), load/unload models.

Every command string here is fixed; anything the user typed goes over stdin only.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import tempfile
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from .config import AppConfig
from .monitor import Monitor
from .ollama import OllamaClient, OllamaError, valid_model
from .sshlink import LinkDown, RunResult, SSHLink

MAX_PERSONA_BYTES = 256 * 1024
MAX_MEMORY_BYTES = 512 * 1024
MEMORY_FILE = "latochan-memory.txt"
PERSONA_HELPER = "/usr/local/sbin/lato-persona-install"
MODEL_HELPER = "/usr/local/sbin/lato-set-model"
_SUDO_PW = re.compile(r"a password is required|a terminal is required|no tty present", re.I)
_CHANNEL = re.compile(r"^\d{15,22}$")
_WHO = re.compile(r"^[\w .'-]{1,40}$")
_SETTING_KEY = re.compile(r"^[a-z_]{1,40}$")
_MOOD = re.compile(r"^[a-z_]{1,24}$")
MAX_GIF_LINKS = 300
MAX_GIF_LINK_LEN = 500


class ToolError(Exception):
    def __init__(self, status: int, message: str, output: str = "") -> None:
        super().__init__(message)
        self.status, self.message, self.output = status, message, output


def check_text(content: Any, *, limit: int, what: str, allow_empty: bool = False) -> str:
    if not isinstance(content, str):
        raise ToolError(422, f"{what} must be text")
    if "\x00" in content:
        raise ToolError(422, f"{what} contains a NUL byte")
    if not allow_empty and not content.strip():
        raise ToolError(422, f"{what} is empty - refusing to overwrite the server copy with nothing")
    if len(content.encode("utf-8")) > limit:
        raise ToolError(413, f"{what} is larger than {limit // 1024} KB")
    return content


class Tools:
    def __init__(self, link: SSHLink, mon: Monitor, cfg: AppConfig) -> None:
        self.link, self.mon, self.cfg = link, mon, cfg
        self.ollama = OllamaClient(link)
        self._persona_cache: tuple[float, str] | None = None

    # -- plumbing -----------------------------------------------------------------------------
    async def _run(self, label: str, command: str, *, input: str | None = None, timeout: float = 30,
                   ok_codes: tuple[int, ...] = (0,)) -> RunResult:
        try:
            res = await self.link.run(command, input=input, timeout=timeout)
        except LinkDown as exc:
            raise ToolError(503, f"server not reachable: {exc}") from exc
        except TimeoutError as exc:
            self.mon.add_event("error", "tool", f"{label}: timed out")
            raise ToolError(504, str(exc)) from exc
        if res.exit_status not in ok_codes:
            if _SUDO_PW.search(res.stderr):
                msg = ("sudo on the server asked for a password. Install the narrow sudoers rule from "
                       "server/sudoers-latodash (see README) and try again.")
                self.mon.add_event("error", "tool", f"{label}: sudo needs a password")
                raise ToolError(403, msg, res.stderr)
            detail = (res.stderr or res.stdout).strip()[-600:]
            self.mon.add_event("error", "tool", f"{label} failed (exit {res.exit_status}): {detail[:200]}")
            raise ToolError(502, f"{label} failed (exit {res.exit_status})", detail)
        return res

    def _save_local(self, name: str, content: str) -> str | None:
        """Keep a local copy next to the bot sources, previous one as <name>.pc-backup."""
        d = self.cfg.latochan_dir
        if not d or not d.is_dir():
            return None
        target = d / name
        if target.exists():
            backup = target.with_name(target.name + ".pc-backup")
            backup.write_bytes(target.read_bytes())
        fd, tmp = tempfile.mkstemp(dir=d, prefix=f".{name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
                fh.write(content)
            os.replace(tmp, target)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        return str(target)

    # -- bot / service control -------------------------------------------------------------------
    async def restart_bot(self) -> dict[str, Any]:
        unit = shlex.quote(self.cfg.server.bot_unit)
        self.mon.add_event("warn", "tool", "Restarting LatoChan…")
        res = await self._run(
            "restart LatoChan",
            f"sudo -n systemctl restart {unit} && sleep 5 && systemctl is-active {unit} "
            f"&& journalctl -u {unit} -n 3 --no-pager -o cat",
            timeout=60,
        )
        self.mon.add_event("info", "tool", "LatoChan restarted")
        return {"ok": True, "output": res.stdout.strip()}

    async def restart_ollama(self) -> dict[str, Any]:
        unit = shlex.quote(self.cfg.server.ollama_unit)
        self.mon.add_event("warn", "tool", "Restarting Ollama… (model will reload on next message)")
        res = await self._run("restart Ollama",
                              f"sudo -n systemctl restart {unit} && sleep 3 && systemctl is-active {unit}", timeout=90)
        self.mon.add_event("info", "tool", "Ollama restarted")
        return {"ok": True, "output": res.stdout.strip()}

    async def thinking(self, mode: str) -> dict[str, Any]:
        if mode not in ("on", "off", "toggle"):
            raise ToolError(422, "mode must be on, off or toggle")
        res = await self._run("thinking toggle", f"lato-config thinking {mode}")
        on = "[ON]" in res.stdout
        self.mon.settings = {**self.mon.settings, "thinking": on}
        self.mon.add_event("info", "tool", f"Thinking mode {'ON' if on else 'OFF'}")
        return {"thinking": on, "output": res.stdout.strip()}

    # -- bot settings + gif library (the server owns the rules: botsettings.py / gifstore.py) ------------
    async def _json_tool(self, label: str, command: str, *, input: str | None = None, timeout: float = 30) -> dict[str, Any]:
        res = await self._run(label, command, input=input, timeout=timeout, ok_codes=(0, 1))
        if res.exit_status == 1:
            msg = (res.stderr or res.stdout).strip()
            if msg.startswith('partial update:'):
                raise ToolError(409, 'Some GIF changes were saved before a storage error. Refresh the library before retrying.', msg[-600:])
            if msg.startswith("rejected:"):
                raise ToolError(422, msg[len("rejected:"):].strip() or "rejected")
            self.mon.add_event("error", "tool", f"{label} failed: {msg[:200]}")
            raise ToolError(502, f"{label} failed", msg[-600:])
        try:
            data = json.loads(res.stdout)
        except ValueError:
            raise ToolError(502, f"{label}: the server sent something unexpected", res.stdout[-300:]) from None
        if not isinstance(data, dict):
            raise ToolError(502, f"{label}: the server sent something unexpected")
        return data

    async def bot_settings(self) -> dict[str, Any]:
        data = await self._json_tool("read settings", "lato-config json")
        if isinstance(data.get("values"), dict):
            self.mon.settings = {**self.mon.settings, **data["values"]}
        return data

    async def bot_settings_apply(self, values: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(values, dict) or not values or len(values) > 60:
            raise ToolError(422, "nothing to save")
        for key, val in values.items():
            if not _SETTING_KEY.match(str(key)):
                raise ToolError(422, "unexpected setting name")
            if not (isinstance(val, (bool, int, float)) or (isinstance(val, str) and len(val) <= 20)):
                raise ToolError(422, f"{key}: unexpected value")
        data = await self._json_tool("save settings", "lato-config apply", input=json.dumps(values))
        if isinstance(data.get("values"), dict):
            self.mon.settings = {**self.mon.settings, **data["values"]}
        names = ", ".join(sorted(values))
        self.mon.add_event("info", "tool", f"Bot settings saved: {names[:200]}")
        return data

    async def bot_settings_reset(self, keys: list[str] | None = None) -> dict[str, Any]:
        keys = keys or []
        if len(keys) > 60 or not all(isinstance(k, str) and _SETTING_KEY.match(k) for k in keys):
            raise ToolError(422, "unexpected setting name")
        cmd = "lato-config reset" + "".join(" " + shlex.quote(k) for k in keys)
        data = await self._json_tool("reset settings", cmd)
        if isinstance(data.get("values"), dict):
            self.mon.settings = {**self.mon.settings, **data["values"]}
        self.mon.add_event("warn", "tool", "Bot settings reset to defaults" + (f" ({len(keys)})" if keys else ""))
        return data

    async def gifs(self) -> dict[str, Any]:
        return await self._json_tool("read gifs", "lato-gifs json")

    @staticmethod
    def check_gif_request(op: str, moods: Any) -> dict[str, list[str]]:
        if op not in ("add", "remove", "replace"):
            raise ToolError(422, "op must be add, remove or replace")
        if not isinstance(moods, dict) or not moods or len(moods) > 40:
            raise ToolError(422, "nothing to do")
        out: dict[str, list[str]] = {}
        for mood, links in moods.items():
            if not isinstance(mood, str) or not _MOOD.match(mood):
                raise ToolError(422, f"bad mood name: {str(mood)[:30]!r} (lowercase letters and underscores)")
            if not isinstance(links, list) or len(links) > MAX_GIF_LINKS or not all(
                    isinstance(x, str) and len(x) <= MAX_GIF_LINK_LEN for x in links):
                raise ToolError(422, f"{mood}: too many or oversized links")
            out[mood] = links
        return out

    async def gifs_apply(self, op: str, moods: dict[str, list[str]]) -> dict[str, Any]:
        moods = self.check_gif_request(op, moods)
        data = await self._json_tool("update gifs", "lato-gifs apply", input=json.dumps({"op": op, "moods": moods}), timeout=60)
        self.mon.add_event("info", "tool", f"Gifs {op}: +{data.get('added', 0)} / -{data.get('removed', 0)} "
                           f"({', '.join(sorted(moods))[:120]})")
        return data

    def _pc_gif_root(self) -> Path:
        d = self.cfg.latochan_dir
        if not d or not d.is_dir():
            raise ToolError(409, "No PC folder is set up (app.latochan_dir in config.toml)")
        return d / "gifs"

    def _read_pc_gifs(self) -> dict[str, list[str]]:
        root = self._pc_gif_root()
        found: dict[str, list[str]] = {}
        if not root.is_dir():
            return found
        for sub in sorted(root.iterdir()):
            f = sub / "links.txt"
            if not (sub.is_dir() and _MOOD.match(sub.name) and f.is_file()):
                continue
            seen: list[str] = []
            for line in f.read_text(encoding="utf-8-sig", errors="replace").splitlines():
                line = line.strip()
                if line.startswith("https://") and len(line) <= MAX_GIF_LINK_LEN and line not in seen:
                    seen.append(line)
            found[sub.name] = seen[:MAX_GIF_LINKS]
        return found

    async def gifs_pc(self) -> dict[str, Any]:
        return {"moods": self._read_pc_gifs(), "folder": str(self._pc_gif_root())}

    async def gifs_import_pc(self, mode: str) -> dict[str, Any]:
        if mode not in ("add", "replace"):
            raise ToolError(422, "mode must be add or replace")
        moods = {m: links for m, links in self._read_pc_gifs().items() if links}
        if not moods:
            raise ToolError(404, "No links found in the PC gifs folder")
        return await self.gifs_apply("replace" if mode == "replace" else "add", moods)

    async def gifs_export_pc(self) -> dict[str, Any]:
        root = self._pc_gif_root()
        data = await self.gifs()
        written = 0
        for mood, links in (data.get("moods") or {}).items():
            if not _MOOD.match(mood):
                continue
            folder = root / mood
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / "links.txt"
            text = "".join(link + "\n" for link in links)
            if target.exists():
                old = target.read_text(encoding="utf-8-sig", errors="replace")
                if old == text:
                    continue
                if old.strip():
                    target.with_name("links.txt.pc-backup").write_text(old, encoding="utf-8", newline="")
            target.write_text(text, encoding="utf-8", newline="")
            written += 1
        self.mon.add_event("info", "tool", f"Gif library copied to the PC ({written} folders changed)")
        return {"written": written, "folder": str(root)}

    # -- persona ----------------------------------------------------------------------------------
    async def _persona_info(self) -> dict[str, Any]:
        data = await self._json_tool('check persona helper', f'sudo -n {PERSONA_HELPER} --info', input='', timeout=10)
        if data.get('protocol') != 2 or not isinstance(data.get('path'), str):
            raise ToolError(409, 'Install the reviewed server/lato-persona-install helper before editing persona.')
        return data

    async def persona_get(self) -> dict[str, Any]:
        info = await self._persona_info()
        res = await self._run('download persona', f'sudo -n {PERSONA_HELPER} --read', input='')
        self._persona_cache = (time.monotonic(), res.stdout)
        saved = self._save_local('persona.md', res.stdout)
        self.mon.add_event('info', 'tool', 'Persona downloaded')
        return {'content': res.stdout, 'saved_to': saved, 'path': info['path']}

    async def persona_put(self, content: str) -> dict[str, Any]:
        content = check_text(content, limit=MAX_PERSONA_BYTES, what='persona')
        await self._persona_info()
        data = await self._json_tool('upload persona', f'sudo -n {PERSONA_HELPER}', input=content, timeout=30)
        if data.get('protocol') != 2 or 'backup' not in data:
            raise ToolError(502, 'Unexpected persona helper response; inspect the server before retrying.')
        self._persona_cache = (time.monotonic(), content)
        saved = self._save_local('persona.md', content)
        self.mon.add_event('info', 'tool', 'Persona uploaded; private backup retained on server')
        return {**data, 'saved_to': saved}

    async def persona_text(self, max_age: float = 120) -> str:
        if self._persona_cache and time.monotonic() - self._persona_cache[0] < max_age:
            return self._persona_cache[1]
        return (await self.persona_get())['content']

    # -- memory -----------------------------------------------------------------------------------
    async def memory_get(self) -> dict[str, Any]:
        res = await self._run("download memory", "lato-memory export")
        saved = self._save_local(MEMORY_FILE, res.stdout)
        self.mon.add_event("info", "tool", "Memory downloaded" + (", PC copy saved" if saved else ""))
        return {"content": res.stdout, "saved_to": saved}

    async def memory_put(self, content: str) -> dict[str, Any]:
        content = check_text(content, limit=MAX_MEMORY_BYTES, what="memory file")
        if not re.search(r'^#snapshot [0-9a-f]{64}$', content, re.M):
            raise ToolError(409, 'Pull a fresh memory export before editing and pushing. Preserve your current edits separately.')
        try:
            res = await self._run("upload memory", "lato-memory import", input=content, timeout=60)
        except ToolError as exc:
            if 'Notes changed since export.' in exc.output:
                raise ToolError(409, 'Notes changed on the server. Nothing imported. Pull a fresh export and merge your edits.',
                                exc.output) from exc
            raise
        saved = self._save_local(MEMORY_FILE, content)
        self.mon.add_event("info", "tool", "Memory uploaded (server backed up the old DB)")
        return {"output": (res.stdout + res.stderr).strip(), "saved_to": saved}

    async def memory_list(self, who: str | None = None) -> dict[str, Any]:
        cmd = "lato-memory list"
        if who:
            if not _WHO.match(who):
                raise ToolError(422, "name/id has unexpected characters")
            cmd += " " + shlex.quote(who)
        res = await self._run("list memory", cmd)
        return {"output": res.stdout.rstrip()}

    # -- discord ----------------------------------------------------------------------------------
    async def say(self, text: str, channel: str | None = None) -> dict[str, Any]:
        text = check_text(text, limit=2000, what="message")
        if len(text) > 2000:
            raise ToolError(413, "Discord messages are limited to 2000 characters")
        cmd = "sudo -n lato-say"
        if channel:
            if not _CHANNEL.match(channel):
                raise ToolError(422, "channel must be a numeric Discord channel ID")
            cmd += f" -c {channel}"
        res = await self._run("post as LatoChan", cmd + ' --stdin', input=text, timeout=30)
        self.mon.add_event("info", "tool", "Posted a message as LatoChan")
        return {"output": res.stdout.strip()}

    # -- models -----------------------------------------------------------------------------------
    async def models(self) -> list[dict[str, Any]]:
        try:
            return await self.ollama.tags()
        except LinkDown as exc:
            raise ToolError(503, str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise ToolError(502, f"couldn't list models: {exc}") from exc

    async def model_load(self, name: str) -> dict[str, Any]:
        try:
            valid_model(name)
            self.mon.add_event("info", "tool", f"Loading {name} into VRAM…")
            t0 = time.monotonic()
            await self.ollama.load(name, num_ctx=self.cfg.server.num_ctx)
        except ValueError as exc:
            raise ToolError(422, str(exc)) from exc
        except LinkDown as exc:
            raise ToolError(503, str(exc)) from exc
        except (OllamaError, Exception) as exc:  # noqa: BLE001
            self.mon.add_event("error", "tool", f"load {name} failed: {exc}")
            raise ToolError(502, str(exc)) from exc
        secs = time.monotonic() - t0
        self.mon.add_event("info", "tool", f"{name} loaded in {secs:.1f}s")
        return {"ok": True, "seconds": round(secs, 1)}

    async def set_live_model(self, name: str) -> dict[str, Any]:
        """Switch LatoChan's brain (LLM_MODEL in /etc/latochan.env) and restart her."""
        try:
            valid_model(name)
        except ValueError as exc:
            raise ToolError(422, str(exc)) from exc
        self.mon.add_event("warn", "tool", f"Switching LatoChan to {name} (she restarts)…")
        res = await self._run("switch model", f"sudo -n {MODEL_HELPER} {shlex.quote(name)}", timeout=60)
        self.mon.settings = {**self.mon.settings, "live_model": name}
        self.mon.add_event("info", "tool", f"LatoChan now runs on {name}")
        return {"ok": True, "output": res.stdout.strip()}

    async def model_unload(self, name: str) -> dict[str, Any]:
        try:
            await self.ollama.unload(valid_model(name))
        except ValueError as exc:
            raise ToolError(422, str(exc)) from exc
        except LinkDown as exc:
            raise ToolError(503, str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise ToolError(502, str(exc)) from exc
        self.mon.add_event("warn", "tool", f"{name} unloaded from VRAM")
        return {"ok": True}


class ChatService:
    """Direct model access for the CLI tab: streams tokens and reports speed."""

    def __init__(self, tools: Tools, mon: Monitor, cfg: AppConfig) -> None:
        self.tools, self.mon, self.cfg = tools, mon, cfg

    async def stream(self, *, messages: list[dict[str, str]], model: str | None, persona: bool,
                     think: bool, temperature: float | None, max_tokens: int) -> AsyncIterator[dict[str, Any]]:
        model = model or self.cfg.server.default_model
        try:
            valid_model(model)
        except ValueError:
            yield {"type": "error", "message": "invalid model name"}
            return
        system: list[dict[str, str]] = []
        if persona:
            try:
                system = [{"role": "system", "content": await self.tools.persona_text()}]
            except ToolError as exc:
                yield {"type": "error", "message": f"couldn't read persona: {exc.message}"}
                return
        options: dict[str, Any] = {"num_ctx": self.cfg.server.num_ctx, "num_predict": max_tokens}
        if temperature is not None:
            options["temperature"] = temperature
        payload = {"model": model, "messages": system + messages, "think": think,
                   "keep_alive": "24h", "options": options}
        last_user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        self.mon.add_event("info", "cli", f"CLI → {model}: {last_user[:200]}")
        yield {"type": "start", "model": model}
        t0 = time.monotonic()
        try:
            async for chunk in self.tools.ollama.chat_stream(payload):
                msg = chunk.get("message") or {}
                if msg.get("thinking"):
                    yield {"type": "thinking", "text": msg["thinking"]}
                if msg.get("content"):
                    yield {"type": "token", "text": msg["content"]}
                if chunk.get("done"):
                    stats = _chat_stats(chunk, time.monotonic() - t0)
                    self.mon.record_cli_generation(stats)
                    self.mon.add_event("info", "cli", f"CLI ← {stats['gen_tokens']} tok @ "
                                       f"{stats['gen_tps'] or 0:.1f} tok/s ({stats['wall_s']:.1f}s)", stats)
                    yield {"type": "done", "stats": stats}
        except LinkDown as exc:
            yield {"type": "error", "message": f"server not reachable: {exc}"}
        except OllamaError as exc:
            self.mon.add_event("error", "cli", f"CLI chat failed: {exc}")
            yield {"type": "error", "message": str(exc)}
        except Exception as exc:  # noqa: BLE001
            self.mon.add_event("error", "cli", f"CLI chat failed: {type(exc).__name__}: {exc}")
            yield {"type": "error", "message": f"{type(exc).__name__}: {exc}"}


def _chat_stats(chunk: dict[str, Any], wall_s: float) -> dict[str, Any]:
    def secs(key: str) -> float:
        return (chunk.get(key) or 0) / 1e9

    gen_tok, pr_tok = chunk.get("eval_count") or 0, chunk.get("prompt_eval_count") or 0
    gen_s, pr_s = secs("eval_duration"), secs("prompt_eval_duration")
    return {
        "gen_tokens": gen_tok, "prompt_tokens": pr_tok,
        "gen_tps": round(gen_tok / gen_s, 2) if gen_s else None,
        "prompt_tps": round(pr_tok / pr_s, 2) if pr_s else None,
        "load_s": round(secs("load_duration"), 2), "total_s": round(secs("total_duration"), 2),
        "wall_s": round(wall_s, 2), "done_reason": chunk.get("done_reason"),
    }
