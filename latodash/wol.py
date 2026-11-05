"""Wake-on-LAN: build and send a magic packet so the dashboard can power the server on.

Needs nothing from the server: the packet goes out from this PC as a UDP broadcast and the server's network card
(which stays powered while the machine is off) wakes the machine. It only works on the same LAN.
"""
from __future__ import annotations

import ipaddress
import re
import socket

LIMITED_BROADCAST = str(ipaddress.IPv4Address((1 << 32) - 1))

_MAC = re.compile(r"^[0-9a-f]{12}$")


def parse_mac(text: str) -> bytes:
    """Decode colon-separated, hyphenated or compact hex into six bytes. Raises ValueError if it isn't a MAC."""
    clean = re.sub(r"[\s:.-]", "", str(text)).lower()
    if not _MAC.match(clean):
        raise ValueError(f"not a MAC address: {text!r} (expected six hexadecimal octets)")
    if clean in ("0" * 12, "f" * 12):
        raise ValueError(f"not a usable MAC address: {text!r}")
    return bytes.fromhex(clean)


def magic_packet(mac: str) -> bytes:
    """6 x 0xFF followed by the MAC repeated 16 times (102 bytes)."""
    return b"\xff" * 6 + parse_mac(mac) * 16


def default_broadcast(host: str) -> str:
    """Subnet broadcast guess for a /24 LAN from the server's IPv4 address."""
    parts = host.split(".")
    if len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts):
        return ".".join(parts[:3] + ["255"])
    return LIMITED_BROADCAST


def send_magic_packet(mac: str, targets: list[str], port: int = 9, repeat: int = 3) -> list[str]:
    """Send the packet to each broadcast address (a few times, UDP can drop). Returns the addresses that accepted it."""
    packet = magic_packet(mac)
    sent: list[str] = []
    last_err: OSError | None = None
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for addr in targets:
            try:
                for _ in range(repeat):
                    sock.sendto(packet, (addr, port))
                sent.append(addr)
            except OSError as exc:
                last_err = exc
    if not sent:
        raise OSError(f"could not send the wake packet: {last_err}")
    return sent
