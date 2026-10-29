"""Settings + gifs endpoints, end to end through the demo runtime (real botsettings/gifstore rules, temp storage)."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from latodash import __version__
from latodash.app import create_app
from latodash.config import AppConfig, SSHConfig
from latodash.demo import demo_runtime

LATOCHAN = Path(__file__).resolve().parents[1] / "latodash" / "vendor"
H = {"X-LatoDash": "1"}

pytestmark = pytest.mark.skipif(not (LATOCHAN / "botsettings.py").exists(), reason="vendored bot files missing")


@pytest.fixture()
def client():
    cfg = AppConfig(ssh=SSHConfig(host="h", user="u", key_file=Path("k"), known_hosts=Path("kh")),
                    listen_port=8765, latochan_dir=LATOCHAN)
    with TestClient(create_app(cfg, demo_runtime(cfg)), base_url="http://127.0.0.1:8765") as c:
        yield c


def test_version_is_1_1_0():
    assert __version__ == "1.1.0"


def test_settings_roundtrip(client):
    doc = client.get("/api/bot-settings", headers=H).json()
    assert doc["values"]["thinking"] is False and any(s["key"] == "gif_chance" for s in doc["schema"])
    r = client.put("/api/bot-settings", headers=H, json={"values": {"thinking": True, "gif_chance": 40, "swearing": "off"}})
    assert r.status_code == 200 and r.json()["values"]["thinking"] is True
    assert client.get("/api/bot-settings", headers=H).json()["values"]["gif_chance"] == 40
    snap = client.get("/api/snapshot", headers=H).json()
    assert snap["state"]["settings"]["thinking"] is True
    r = client.post("/api/bot-settings/reset", headers=H, json={"keys": ["gif_chance"]})
    assert r.json()["values"]["gif_chance"] == 100 and r.json()["values"]["thinking"] is True


@pytest.mark.parametrize("values", [{"gif_chance": 101}, {"swearing": "loud"}, {"thinking": "maybe"}, {"nope": 1}])
def test_settings_validation_is_422(client, values):
    r = client.put("/api/bot-settings", headers=H, json={"values": values})
    assert r.status_code == 422 and r.json()["error"]


def test_thinking_button_and_settings_agree(client):
    assert client.post("/api/tools/thinking", headers=H, json={"mode": "on"}).json()["thinking"] is True
    assert client.get("/api/bot-settings", headers=H).json()["values"]["thinking"] is True


def test_gifs_add_remove_replace(client):
    link = "https://media.example.com/new/one.gif"
    r = client.post("/api/gifs", headers=H, json={"op": "add", "moods": {"love": [link, link, "http://nope"]}})
    body = r.json()
    assert r.status_code == 200 and body["added"] == 1 and body["moods"]["love"] == [link] and body["skipped"] == ["http://nope"]
    r = client.post("/api/gifs", headers=H, json={"op": "remove", "moods": {"love": [link]}})
    assert r.json()["moods"]["love"] == [] and r.json()["removed"] == 1
    r = client.post("/api/gifs", headers=H, json={"op": "replace", "moods": {"sleepy": ["https://a.example.com/z.gif"]}})
    assert r.json()["moods"]["sleepy"] == ["https://a.example.com/z.gif"]
    assert client.get("/api/gifs", headers=H).json()["moods"]["sleepy"] == ["https://a.example.com/z.gif"]


def test_gifs_bad_requests(client):
    for body in [{"op": "add", "moods": {"../x": ["https://a.example.com/z.gif"]}}, {"op": "boom", "moods": {"happy": []}},
                 {"op": "add", "moods": {}}]:
        assert client.post("/api/gifs", headers=H, json=body).status_code == 422


def test_requires_header(client):
    assert client.put("/api/bot-settings", json={"values": {"thinking": True}}).status_code == 403
    assert client.post("/api/gifs", json={"op": "add", "moods": {"happy": []}}).status_code == 403
