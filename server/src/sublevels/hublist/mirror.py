"""Mirrors (P10 §3): other public lists' XML, merged into this one.

Each mirror's list is fetched hourly. Its hubs are merged by normalised address, with the mirror
recorded as their source. A new one starts ``pending`` and is pinged like any other entry, so a
mirrored hub is listed here only once this list's own pinger has seen it. An entry an operator
banned or hid stays so.

The XML is someone else's, so it is read with care. At most 8 MiB is downloaded and at most 32 MiB
decompressed, a document with a DTD or an entity is refused, and ``Hub`` elements are taken at any
depth, as clients take them.
"""

from __future__ import annotations

import bz2
import logging
import urllib.request
import xml.etree.ElementTree as ET

from .store import normalise

log = logging.getLogger("sublevels.hublist")

MAX_DOWNLOAD = 8 << 20
MAX_XML = 32 << 20
TIMEOUT = 30.0


class MirrorError(Exception):
    pass


def fetch(url: str, *, timeout: float = TIMEOUT) -> bytes:
    """The list at ``url`` as XML bytes, decompressed if it is bz2 (by its ``.bz2`` name, as
    clients choose, or its magic)."""
    req = urllib.request.Request(url, headers={"User-Agent": "sublevels-hublist"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read(MAX_DOWNLOAD + 1)
    except OSError as e:
        raise MirrorError(f"{url}: {e}") from None
    if len(data) > MAX_DOWNLOAD:
        raise MirrorError(f"{url}: larger than {MAX_DOWNLOAD} bytes")
    if url.split("?")[0].endswith(".bz2") or data[:3] == b"BZh":
        d = bz2.BZ2Decompressor()
        try:
            data = d.decompress(data, max_length=MAX_XML + 1)
        except (OSError, ValueError) as e:
            raise MirrorError(f"{url}: not bz2 ({e})") from None
        if len(data) > MAX_XML or not d.eof:
            raise MirrorError(f"{url}: more than {MAX_XML} bytes of XML")
    return data


def parse(xml: bytes) -> list[dict]:
    """The hubs in a list, as entries' fields with ``address`` normalised; bad ones are skipped."""
    head = xml[:4096].upper()
    if b"<!DOCTYPE" in head or b"<!ENTITY" in xml.upper():
        raise MirrorError("a document with a DTD or entities")
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as e:
        raise MirrorError(f"not XML: {e}") from None
    out = []
    for h in root.iter("Hub"):
        a = {k.lower(): v for k, v in h.attrib.items()}
        try:
            address = normalise(a.get("address", ""))
        except ValueError:
            continue

        def num(k):
            v = (a.get(k) or "").split(".")[0]
            return int(v) if v.isdigit() else 0

        out.append({"address": address, "name": a.get("name", ""),
                    "description": a.get("description", ""), "users": num("users"),
                    "share": num("shared"), "min_share": num("minshare"),
                    "min_slots": num("minslots"), "max_hubs": num("maxhubs"),
                    "max_users": num("maxusers"), "country": (a.get("country") or "")[:2].upper(),
                    "software": a.get("software", ""), "kp": a.get("kp", "")})
    return out


def merge(store, hubs: list[dict], source: str) -> dict:
    """Merge ``hubs`` into ``store``: new ones pending, known ones left to their own pings."""
    added = known = 0
    for h in hubs:
        address = h.pop("address")
        if store.get(address) is not None:
            known += 1
            continue
        store.register(address, source, h)
        added += 1
    return {"added": added, "known": known}
