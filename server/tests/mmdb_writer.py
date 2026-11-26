"""A minimal MaxMind DB writer, for tests only, from the public MaxMind DB File Format
Specification 2.0: an IPv6 search tree with 24-bit records, a data section, and the metadata.
It writes small made-up databases, so no GeoLite2 data is ever needed in tests or CI."""

from __future__ import annotations

import ipaddress
import struct
import time

META = b"\xab\xcd\xefMaxMind.com"


def _ctrl(type_: int, size: int) -> bytes:
    if size < 29:
        head, ext = size, b""
    elif size < 285:
        head, ext = 29, bytes([size - 29])
    elif size < 65821:
        head, ext = 30, struct.pack(">H", size - 285)
    else:
        head, ext = 31, struct.pack(">I", size - 65821)[1:]
    if type_ <= 7:
        return bytes([(type_ << 5) | head]) + ext
    return bytes([head, type_ - 7]) + ext


def _uint(type_: int, n: int) -> bytes:
    b = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    return _ctrl(type_, len(b)) + b


def encode(v) -> bytes:
    if isinstance(v, bool):
        return _ctrl(14, int(v))
    if isinstance(v, str):
        b = v.encode("utf-8")
        return _ctrl(2, len(b)) + b
    if isinstance(v, int):
        return _uint(6 if v < 2 ** 32 else 9, v)
    if isinstance(v, dict):
        return _ctrl(7, len(v)) + b"".join(encode(str(k)) + encode(x) for k, x in v.items())
    if isinstance(v, (list, tuple)):
        return _ctrl(11, len(v)) + b"".join(encode(x) for x in v)
    raise TypeError(type(v))


def _u16(n: int) -> bytes:
    return _uint(5, n)


def write(path: str, networks: dict[str, dict]) -> None:
    """Write a database mapping each network (IPv4 or IPv6 CIDR) to its record."""
    data = bytearray()
    root: list = [None, None]
    for net, record in networks.items():
        n = ipaddress.ip_network(net)
        bits, plen = int(n.network_address), n.prefixlen
        if n.version == 4:  # IPv4 lives at ::a.b.c.d in an IPv6 tree
            plen += 96
        offset = len(data)
        data += encode(record)
        node = root
        for i in range(plen):
            bit = (bits >> (127 - i)) & 1 if n.version == 6 else \
                (0 if i < 96 else (bits >> (31 - (i - 96))) & 1)
            if i == plen - 1:
                node[bit] = ("data", offset)
            else:
                if not isinstance(node[bit], list):
                    node[bit] = [None, None]
                node = node[bit]
    nodes: list = []
    order = [root]
    while order:  # number the nodes breadth first
        n = order.pop(0)
        nodes.append(n)
        order.extend(c for c in n if isinstance(c, list))
    index = {id(n): i for i, n in enumerate(nodes)}
    count = len(nodes)

    def rec(c) -> int:
        if c is None:
            return count
        if isinstance(c, list):
            return index[id(c)]
        return count + 16 + c[1]

    tree = b"".join(rec(n[0]).to_bytes(3, "big") + rec(n[1]).to_bytes(3, "big") for n in nodes)
    meta = (_ctrl(7, 9) + encode("node_count") + _uint(6, count) + encode("record_size")
            + _u16(24) + encode("ip_version") + _u16(6) + encode("database_type")
            + encode("Test-City") + encode("languages") + encode(["en"])
            + encode("binary_format_major_version") + _u16(2)
            + encode("binary_format_minor_version") + _u16(0) + encode("build_epoch")
            + _uint(9, int(time.time())) + encode("description") + encode({"en": "test"}))
    with open(path, "wb") as f:
        f.write(tree + b"\x00" * 16 + bytes(data) + META + meta)


def city(country: str, name: str = "", region: str = "") -> dict:
    out = {"country": {"iso_code": country, "names": {"en": country}}}
    if name:
        out["city"] = {"names": {"en": name}}
    if region:
        out["subdivisions"] = [{"names": {"en": region}}]
    return out
