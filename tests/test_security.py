from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from latodash.app import create_app
from latodash.config import AppConfig, SSHConfig
from latodash.demo import demo_runtime

CFG = AppConfig(ssh=SSHConfig(host="h", user="u", key_file=Path("k"), known_hosts=Path("kh")), listen_port=8765)


@pytest.fixture()
def client():
    with TestClient(create_app(CFG, demo_runtime(CFG)), base_url="http://127.0.0.1:8765") as c:
        yield c


def test_page_and_headers(client):
    r = client.get("/")
    assert r.status_code == 200 and "LatoDash" in r.text
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "DENY"


def test_dns_rebinding_blocked(client):
    assert client.get("/api/health", headers={"host": "evil.example:8765"}).status_code == 403


def test_cross_origin_blocked(client):
    r = client.post("/api/tools/restart-bot", headers={"x-latodash": "1", "origin": "http://evil.example"})
    assert r.status_code == 403


def test_csrf_header_required(client):
    assert client.post("/api/tools/restart-bot").status_code == 403
    assert client.post("/api/tools/restart-bot", headers={"x-latodash": "1"}).status_code == 200


def test_websocket_origin(client):
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("ws://127.0.0.1:8765/ws", headers={"origin": "http://evil.example"}) as ws:
            ws.receive_json()
    with client.websocket_connect("ws://127.0.0.1:8765/ws", headers={"origin": "http://127.0.0.1:8765"}) as ws:
        msg = ws.receive_json()
        assert msg["type"] == "snapshot" and msg["data"]["config"]["demo"] is True


def test_validation(client):
    h = {"x-latodash": "1"}
    assert client.post("/api/tools/thinking", json={"mode": "rm -rf"}, headers=h).status_code == 422
    assert client.post("/api/tools/say", json={"text": "hi", "channel": "1; reboot"}, headers=h).status_code == 422
    assert client.post("/api/models/load", json={"model": "$(reboot)"}, headers=h).status_code == 422
    assert client.post("/api/chat", json={"messages": [{"role": "system", "content": "x"}]}, headers=h).status_code == 422


def test_chat_streams_ndjson(client):
    r = client.post("/api/chat", json={"messages": [{"role": "user", "content": "hi"}]}, headers={"x-latodash": "1"})
    kinds = [__import__("json").loads(l)["type"] for l in r.text.strip().splitlines()]
    assert kinds[0] == "start" and kinds[-1] == "done" and "token" in kinds
