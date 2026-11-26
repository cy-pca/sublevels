"""The registration server (P10 §1): hubs push their entry on TCP 2501, as P9 §1 sends it.

Each connection gets 10 s and at most 2 KiB. The list sends ``$Lock <random> Pk=…|``; the hub
answers ``$Key <key>|`` and its fields (name, address, description, users, share, and the list it
dialled, which is read if present and ignored). The key is accepted with the standard constant or
the legacy one from the hub's source port; a setting turns the check off. Registrations are
limited to 4 an hour per source address and per hub address.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections import Counter

from .. import __version__
from ..nmdc.codec import CodePage, dcn_unescape, legacy_magic, lock_to_key, make_lock, unescape
from ..nmdc.limits import Window
from .store import normalise

log = logging.getLogger("sublevels.hublist")

TIMEOUT = 10.0
MAX_BYTES = 2048
PER_HOUR = 4
_MINSHARE = re.compile(r"\[MINSHARE:(\d+)MB\] ")


def parse_record(fields: list[str]) -> dict:
    """name, address, description, users, share (P9 §1's order) as an entry's fields, with the
    address normalised; ValueError for a record that is not one."""
    if len(fields) < 5:
        raise ValueError("fewer than five fields")
    name, address, desc, users, share = fields[:5]
    if not users.isdigit() or not share.isdigit():
        raise ValueError("users and share must be decimal")
    out = {"name": unescape(name), "users": int(users), "share": int(share)}
    desc = unescape(desc)
    m = _MINSHARE.match(desc)
    if m:
        out["min_share"] = int(m.group(1)) * 1024 * 1024
        desc = desc[m.end():]
    out["description"] = desc
    out["address"] = normalise(address)
    return out


class RegistrationServer:
    def __init__(self, store, *, check_key: bool = True, codepage: str = "cp1252", clock=None,
                 on_registered=None):
        import time

        self.store = store
        self.check_key = check_key
        self.codepage = CodePage(codepage)
        self.clock = clock or time.monotonic
        self.on_registered = on_registered  # fn(entry): e.g. ping it soon
        self.counts: Counter = Counter()
        self.by_source: dict[str, Window] = {}
        self.by_address: dict[str, Window] = {}
        self.server = None

    async def start(self, host: str, port: int) -> int:
        self.server = await asyncio.start_server(self._handle, host, port)
        return self.server.sockets[0].getsockname()[1]

    async def close(self) -> None:
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()

    def _allow(self, table: dict, key: str) -> bool:
        if len(table) > 10000:
            table.clear()
        w = table.setdefault(key, Window(PER_HOUR, 3600.0))
        return w.allow(self.clock())

    async def _handle(self, reader, writer) -> None:
        peer = writer.get_extra_info("peername") or ("?", 0)
        try:
            outcome = await asyncio.wait_for(self._exchange(reader, writer, peer), TIMEOUT)
        except asyncio.TimeoutError:
            outcome = "timeout"
        except (ConnectionError, ValueError) as e:
            outcome = "error"
            log.info("hublist: registration from %s: %s", peer[0], e)
        finally:
            writer.close()
        self.counts[outcome] += 1

    async def _exchange(self, reader, writer, peer) -> str:
        lock = make_lock()
        writer.write(b"$Lock " + lock + f" Pk=sublevels-hublist {__version__}|".encode())
        await writer.drain()
        data = b""
        while data.count(b"|") < 7:  # $Key and six fields, the last optional
            chunk = await reader.read(MAX_BYTES + 1 - len(data))
            if not chunk:
                break
            data += chunk
            if len(data) > MAX_BYTES:
                return "too_long"
        parts = data.split(b"|")
        if not parts[0].startswith(b"$Key ") or len(parts) < 7:
            return "malformed"
        key = dcn_unescape(parts[0][len(b"$Key "):])
        if self.check_key and key not in (dcn_unescape(lock_to_key(lock)),
                                          dcn_unescape(lock_to_key(lock,
                                                                   legacy_magic(peer[1])))):
            return "wrong_key"
        try:
            rec = parse_record([self.codepage.decode(p) for p in parts[1:7]])
        except ValueError as e:
            log.info("hublist: a bad record from %s: %s", peer[0], e)
            return "malformed"
        address = rec.pop("address")
        old = self.store.get(address)
        if old is not None and old["state"] == "banned":
            return "banned"
        if not self._allow(self.by_source, peer[0]) or not self._allow(self.by_address, address):
            return "rate_limited"
        entry = self.store.register(address, peer[0], rec)
        log.info("hublist: %s %s (%s, %d users) from %s", "updated" if old else "new", address,
                 rec["name"], rec["users"], peer[0])
        if self.on_registered is not None:
            self.on_registered(entry)
        return "registered"
