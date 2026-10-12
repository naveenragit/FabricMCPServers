"""Process-level DNS resilience.

A secondary DNS server on this network answers NXDOMAIN for Azure names much of the time. The process retries the OS
resolver briefly, remembers the last good answer per host, and finally asks a public resolver directly over UDP.
Set EVAL_DNS_FALLBACK to a comma-separated server list to change the fallback (default 8.8.8.8,1.1.1.1).
"""

from __future__ import annotations

import os
import random
import socket
import struct
import threading
import time

_installed = False
_good: dict[tuple, list] = {}
_lock = threading.Lock()


def _skip_name(data: bytes, pos: int) -> int:
    while True:
        length = data[pos]
        if length == 0:
            return pos + 1
        if length & 0xC0 == 0xC0:
            return pos + 2
        pos += 1 + length


def udp_resolve(host: str, server: str, timeout: float = 3.0) -> list[str]:
    """A records for host from one DNS server, following CNAMEs that arrive in the same answer."""
    qid = random.randrange(65536)
    question = b"".join(bytes([len(p)]) + p.encode() for p in host.rstrip(".").split(".")) + b"\x00\x00\x01\x00\x01"
    packet = struct.pack(">HHHHHH", qid, 0x0100, 1, 0, 0, 0) + question
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        sock.sendto(packet, (server, 53))
        data, _ = sock.recvfrom(4096)
    if struct.unpack(">H", data[:2])[0] != qid:
        return []
    answers = struct.unpack(">H", data[6:8])[0]
    pos = _skip_name(data, 12) + 4
    ips: list[str] = []
    for _ in range(answers):
        pos = _skip_name(data, pos)
        rtype, _cls, _ttl, rdlen = struct.unpack(">HHIH", data[pos : pos + 10])
        pos += 10
        if rtype == 1 and rdlen == 4:
            ips.append(socket.inet_ntoa(data[pos : pos + 4]))
        pos += rdlen
    return ips


def install_dns_retry(attempts: int = 4, delay_s: float = 0.5) -> None:
    global _installed
    if _installed:
        return
    original = socket.getaddrinfo
    servers = [s.strip() for s in os.environ.get("EVAL_DNS_FALLBACK", "8.8.8.8,1.1.1.1").split(",") if s.strip()]

    def getaddrinfo(host, port, *args, **kwargs):  # type: ignore[no-untyped-def]
        key = (host, port, args, tuple(sorted(kwargs.items())))
        last: Exception | None = None
        for _ in range(attempts):
            try:
                result = original(host, port, *args, **kwargs)
                with _lock:
                    _good[key] = result
                return result
            except socket.gaierror as exc:
                last = exc
                time.sleep(delay_s)
        with _lock:
            cached = _good.get(key)
        if cached:
            return cached
        if isinstance(host, str) and not host.replace(".", "").isdigit():
            for server in servers:
                try:
                    ips = udp_resolve(host, server)
                except OSError:
                    continue
                if ips:
                    result = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, int(port or 0))) for ip in ips]
                    with _lock:
                        _good[key] = result
                    return result
        raise last  # type: ignore[misc]

    socket.getaddrinfo = getaddrinfo  # type: ignore[assignment]
    _installed = True
