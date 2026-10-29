"""Entry point:  python -m latodash [--config config.toml] [--demo] [--no-browser] [--port N]"""
from __future__ import annotations

import argparse
import asyncio
import logging
import logging.handlers
import socket
import sys
import webbrowser
from dataclasses import replace
from pathlib import Path

import uvicorn

from . import __version__
from .app import create_app, real_runtime
from .config import AppConfig, ConfigError, SSHConfig, load_config

ROOT = Path(__file__).resolve().parent.parent


def _setup_logging(log_dir: Path, verbose: bool) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    file_handler = logging.handlers.RotatingFileHandler(log_dir / "latodash.log", maxBytes=2_000_000,
                                                        backupCount=3, encoding="utf-8")
    file_handler.setFormatter(fmt)
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    root.handlers[:] = [file_handler, console]
    logging.getLogger("asyncssh").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def _port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="latodash", description="LatoChan server dashboard")
    p.add_argument("--config", type=Path, default=ROOT / "config.toml")
    p.add_argument("--demo", action="store_true", help="simulate the server (no SSH)")
    p.add_argument("--port", type=int, help="override app.listen_port")
    p.add_argument("--no-browser", action="store_true")
    p.add_argument("--verbose", action="store_true")
    p.add_argument("--version", action="version", version=f"latodash {__version__}")
    args = p.parse_args(argv)

    _setup_logging(ROOT / "logs", args.verbose)
    log = logging.getLogger("latodash")

    try:
        if args.demo and not args.config.is_file():
            cfg = AppConfig(ssh=SSHConfig(host="demo", user="demo", key_file=Path("-"), known_hosts=Path("-")))
        else:
            cfg = load_config(args.config)
    except ConfigError as exc:
        log.error("config problem: %s", exc)
        return 2
    if args.port:
        cfg = replace(cfg, listen_port=args.port)

    url = f"http://127.0.0.1:{cfg.listen_port}/"
    if _port_in_use("127.0.0.1", cfg.listen_port):
        log.info("something is already listening on %s - probably LatoDash itself; opening it", url)
        if cfg.open_browser and not args.no_browser:
            webbrowser.open(url)
        return 0

    if args.demo:
        from .demo import demo_runtime
        runtime = demo_runtime(cfg)
    else:
        if not cfg.ssh.key_file.is_file():
            log.error("SSH key not found: %s (see README → first-time setup)", cfg.ssh.key_file)
            return 2
        runtime = real_runtime(cfg)

    app = create_app(cfg, runtime)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=cfg.listen_port, log_config=None,
                                           ws_ping_interval=20, ws_ping_timeout=20, access_log=False))

    async def serve() -> None:
        async def open_when_ready() -> None:
            while not server.started:
                await asyncio.sleep(0.1)
            log.info("LatoDash %s running at %s%s", __version__, url, " (demo mode)" if args.demo else "")
            if cfg.open_browser and not args.no_browser:
                webbrowser.open(url)

        opener = asyncio.create_task(open_when_ready())
        await server.serve()
        opener.cancel()

    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
