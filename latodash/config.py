"""Configuration: a TOML file next to the launcher, with safe defaults for everything.

Relative paths in the file are resolved against the folder that holds the config file,
so the dashboard folder can be moved or copied without editing paths.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised only on Python 3.10
    import tomli as tomllib


class ConfigError(ValueError):
    """Raised when the config file is missing a required value or has a bad one."""


@dataclass(frozen=True)
class SSHConfig:
    host: str
    user: str
    key_file: Path
    known_hosts: Path
    port: int = 22
    connect_timeout: float = 8.0
    keepalive_interval: float = 15.0


@dataclass(frozen=True)
class ServerConfig:
    bot_unit: str = "latochan.service"
    ollama_unit: str = "ollama.service"
    extra_units: tuple[str, ...] = (
        "gpu-watchdog.service",
        "gpu-fan.service",
        "gpu-tune.service",
        "lato-reminders.timer",
        "lato-memory-reminder.timer",
    )
    ollama_host: str = "127.0.0.1"
    ollama_port: int = 11434
    persona_path: str = "/etc/latochan/persona.md"
    default_model: str = "latochan"
    num_ctx: int = 8192
    main_channel_id: str = ""
    backfill_hours: int = 24

    @property
    def units(self) -> tuple[str, ...]:
        return (self.bot_unit, self.ollama_unit, *self.extra_units)


@dataclass(frozen=True)
class Thresholds:
    gpu_junction_warn: float = 95.0
    gpu_junction_crit: float = 100.0
    reply_pending_warn_s: float = 120.0
    reply_pending_crit_s: float = 300.0
    generation_stall_s: float = 180.0
    telemetry_stale_s: float = 12.0
    ollama_slow_ms: float = 3000.0
    disk_warn_pct: float = 90.0
    mem_avail_warn_pct: float = 8.0


@dataclass(frozen=True)
class AppConfig:
    ssh: SSHConfig
    server: ServerConfig = field(default_factory=ServerConfig)
    thresholds: Thresholds = field(default_factory=Thresholds)
    listen_host: str = "127.0.0.1"
    listen_port: int = 8765
    open_browser: bool = True
    latochan_dir: Path | None = None  # PC folder that keeps local copies of persona/memory
    sample_interval: float = 2.0


def _section(data: dict[str, Any], name: str) -> dict[str, Any]:
    value = data.get(name, {})
    if not isinstance(value, dict):
        raise ConfigError(f"[{name}] must be a table")
    return value


def _path(base: Path, value: Any, name: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{name} must be a non-empty path string")
    p = Path(value).expanduser()
    return p if p.is_absolute() else (base / p).resolve()


def _pick(src: dict[str, Any], cls: type, skip: tuple[str, ...] = ()) -> dict[str, Any]:
    """Keep only keys the dataclass knows about, and complain about unknown ones."""
    known = set(cls.__dataclass_fields__) - set(skip)
    unknown = set(src) - known - set(skip)
    if unknown:
        raise ConfigError(f"unknown setting(s) for {cls.__name__}: {', '.join(sorted(unknown))}")
    return {k: v for k, v in src.items() if k in known}


def load_config(path: Path) -> AppConfig:
    path = Path(path)
    if not path.is_file():
        raise ConfigError(f"config file not found: {path}")
    base = path.resolve().parent
    with path.open("rb") as fh:
        data = tomllib.load(fh)

    ssh_raw = _section(data, "ssh")
    for key in ("host", "user", "key_file", "known_hosts"):
        if key not in ssh_raw:
            raise ConfigError(f"[ssh] {key} is required")
    ssh_kwargs = _pick(ssh_raw, SSHConfig, skip=("key_file", "known_hosts"))
    ssh = SSHConfig(
        key_file=_path(base, ssh_raw["key_file"], "ssh.key_file"),
        known_hosts=_path(base, ssh_raw["known_hosts"], "ssh.known_hosts"),
        **ssh_kwargs,
    )

    server_raw = _section(data, "server")
    if "extra_units" in server_raw:
        server_raw = {**server_raw, "extra_units": tuple(server_raw["extra_units"])}
    server = ServerConfig(**_pick(server_raw, ServerConfig))
    thresholds = Thresholds(**_pick(_section(data, "thresholds"), Thresholds))

    app_raw = _section(data, "app")
    latochan_dir = app_raw.get("latochan_dir")
    app_kwargs = _pick(app_raw, AppConfig, skip=("ssh", "server", "thresholds", "latochan_dir"))
    cfg = AppConfig(
        ssh=ssh,
        server=server,
        thresholds=thresholds,
        latochan_dir=_path(base, latochan_dir, "app.latochan_dir") if latochan_dir else None,
        **app_kwargs,
    )
    if cfg.listen_host not in ("127.0.0.1", "localhost", "::1"):
        raise ConfigError("app.listen_host must be a loopback address; the dashboard can restart services")
    return cfg
