"""Local-only guard for the web server.

The dashboard can restart services and post to Discord, so even on 127.0.0.1 it defends against
other web pages open in the same browser:
  * Host header must be a loopback name   -> blocks DNS-rebinding attacks
  * Origin, when sent, must be this app   -> blocks cross-site WebSocket / form posts
  * state-changing requests need the X-LatoDash header, which cross-site pages can't add
    without a CORS preflight (and we never answer preflights)
  * strict Content-Security-Policy and the usual hardening headers on every response
"""
from __future__ import annotations

import json

from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

LOOPBACK_NAMES = ("127.0.0.1", "localhost", "[::1]")
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
CSRF_HEADER = b"x-latodash"


def _headers(scope: Scope) -> dict[bytes, bytes]:
    return {k.lower(): v for k, v in scope.get("headers", [])}


class LocalOnlyMiddleware:
    def __init__(self, app: ASGIApp, port: int) -> None:
        self.app = app
        self.port = port
        self.hosts = {f"{h}:{port}".encode() for h in LOOPBACK_NAMES} | {h.encode() for h in LOOPBACK_NAMES}
        self.origins = {f"http://{h}:{port}".encode() for h in LOOPBACK_NAMES}
        self.csp = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            f"connect-src 'self' ws://127.0.0.1:{port} ws://localhost:{port}; "
            "frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
        ).encode()

    async def _deny(self, scope: Scope, receive: Receive, send: Send, reason: str) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008, "reason": reason})
            return
        body = json.dumps({"error": reason}).encode()
        await send({"type": "http.response.start", "status": 403,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        h = _headers(scope)
        if h.get(b"host", b"") not in self.hosts:
            await self._deny(scope, receive, send, "host not allowed")
            return
        origin = h.get(b"origin")
        if scope["type"] == "websocket":
            if origin not in self.origins:
                await self._deny(scope, receive, send, "origin not allowed")
                return
        else:
            if origin is not None and origin not in self.origins:
                await self._deny(scope, receive, send, "origin not allowed")
                return
            if scope["method"] not in SAFE_METHODS and h.get(CSRF_HEADER) != b"1":
                await self._deny(scope, receive, send, "missing X-LatoDash header")
                return

        csp = self.csp

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers += [
                    (b"content-security-policy", csp),
                    (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"DENY"),
                    (b"referrer-policy", b"no-referrer"),
                    (b"cross-origin-opener-policy", b"same-origin"),
                    (b"cache-control", b"no-store"),
                ]
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_headers)
