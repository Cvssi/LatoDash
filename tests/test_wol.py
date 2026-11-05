import asyncio
import socket
from pathlib import Path
from types import SimpleNamespace

import pytest

from latodash import wol

SERVER_HOST = ".".join(map(str, (192, 0, 2, 42)))
SUBNET_BROADCAST = ".".join(map(str, (192, 0, 2, 255)))

from latodash.config import AppConfig, ConfigError, SSHConfig, WakeConfig, load_config
from latodash.monitor import Monitor
from latodash.tools import ToolError, Tools

MAC = bytes.fromhex("010203040506").hex(":")


def test_parse_mac_accepts_common_spellings():
    want = bytes.fromhex("010203040506")
    for text in (MAC, MAC.replace(":", "-"), "010203040506", ".".join(MAC.replace(":", "")[i:i + 4] for i in (0, 4, 8)), f" {MAC.upper()} "):
        assert wol.parse_mac(text) == want


@pytest.mark.parametrize("bad", ["", "AA:BB:CC", "GG:BB:CC:DD:EE:FF", bytes.fromhex("000000000000").hex(":"), bytes.fromhex("ffffffffffff").hex(":"), "x" * 12])
def test_parse_mac_rejects_garbage(bad):
    with pytest.raises(ValueError):
        wol.parse_mac(bad)


def test_magic_packet_layout():
    pkt = wol.magic_packet(MAC)
    assert len(pkt) == 102
    assert pkt[:6] == b"\xff" * 6
    assert pkt[6:] == bytes.fromhex("010203040506") * 16


def test_default_broadcast():
    assert wol.default_broadcast(SERVER_HOST) == SUBNET_BROADCAST
    assert wol.default_broadcast("server.example.com") == wol.LIMITED_BROADCAST


class FakeSocket:
    sent: list = []
    fail_for: set = set()

    def __init__(self, *a, **k): ...
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def setsockopt(self, *a): ...

    def sendto(self, data, addr):
        if addr[0] in FakeSocket.fail_for:
            raise OSError("network unreachable")
        FakeSocket.sent.append((data, addr))


@pytest.fixture
def fake_socket(monkeypatch):
    FakeSocket.sent, FakeSocket.fail_for = [], set()
    # replace only wol's view of the socket module; patching socket.socket itself would break asyncio
    monkeypatch.setattr(wol, "socket", SimpleNamespace(socket=FakeSocket, AF_INET=socket.AF_INET,
                        SOCK_DGRAM=socket.SOCK_DGRAM, SOL_SOCKET=socket.SOL_SOCKET, SO_BROADCAST=socket.SO_BROADCAST))
    return FakeSocket


def test_send_goes_to_every_target_three_times(fake_socket):
    sent = wol.send_magic_packet(MAC, [SUBNET_BROADCAST, wol.LIMITED_BROADCAST], port=9)
    assert sent == [SUBNET_BROADCAST, wol.LIMITED_BROADCAST]
    assert len(fake_socket.sent) == 6
    assert {addr for _, addr in fake_socket.sent} == {(SUBNET_BROADCAST, 9), (wol.LIMITED_BROADCAST, 9)}
    assert all(data == wol.magic_packet(MAC) for data, _ in fake_socket.sent)


def test_send_survives_one_bad_target_but_not_all(fake_socket):
    fake_socket.fail_for = {SUBNET_BROADCAST}
    assert wol.send_magic_packet(MAC, [SUBNET_BROADCAST, wol.LIMITED_BROADCAST]) == [wol.LIMITED_BROADCAST]
    fake_socket.fail_for = {SUBNET_BROADCAST, wol.LIMITED_BROADCAST}
    with pytest.raises(OSError):
        wol.send_magic_packet(MAC, [SUBNET_BROADCAST, wol.LIMITED_BROADCAST])


def make_tools(tmp_path, wake=WakeConfig()):
    cfg = AppConfig(ssh=SSHConfig(host=SERVER_HOST, user="u", key_file=Path("k"), known_hosts=Path("kh")),
                    wake=wake, latochan_dir=tmp_path)
    return Tools(object(), Monitor(cfg), cfg)


def test_wake_tool_needs_a_mac(tmp_path):
    with pytest.raises(ToolError) as exc:
        asyncio.run(make_tools(tmp_path).wake())
    assert exc.value.status == 422 and "[wake] mac" in exc.value.message


def test_wake_tool_sends_to_subnet_and_global_broadcast(tmp_path, fake_socket):
    res = asyncio.run(make_tools(tmp_path, WakeConfig(mac=MAC)).wake())
    assert res["sent_to"] == [SUBNET_BROADCAST, wol.LIMITED_BROADCAST]
    assert fake_socket.sent[0][0] == wol.magic_packet(MAC)


def test_wake_tool_reports_network_failure(tmp_path, fake_socket):
    fake_socket.fail_for = {SUBNET_BROADCAST, wol.LIMITED_BROADCAST}
    with pytest.raises(ToolError) as exc:
        asyncio.run(make_tools(tmp_path, WakeConfig(mac=MAC)).wake())
    assert exc.value.status == 502


GOOD = """
[ssh]
host = "{SERVER_HOST}"
user = "owner"
key_file = "keys/k"
known_hosts = "keys/kh"
"""


def test_config_reads_wake_section_and_rejects_a_bad_mac(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(GOOD + f'\n[wake]\nmac = "{MAC}"\nport = 7\n')
    cfg = load_config(p)
    assert cfg.wake.mac == MAC and cfg.wake.port == 7
    p.write_text(GOOD + '\n[wake]\nmac = "nope"\n')
    with pytest.raises(ConfigError):
        load_config(p)
    p.write_text(GOOD)
    assert load_config(p).wake.mac == ""
