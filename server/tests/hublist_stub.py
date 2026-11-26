"""A stand-in hub list's registration server, from P10 §1: it sends ``$Lock``, reads the hub's
``$Key`` and its fields, checks the key with either constant, and keeps what it got. Tests point a
hub's push targets at it, so no test reaches a real list."""

from __future__ import annotations

import asyncio

from sublevels.nmdc.codec import CodePage, dcn_unescape, legacy_magic, lock_to_key

CP = CodePage("cp1252")


class StubList:
    def __init__(self, *, lock: bytes = b"EXTENDEDPROTOCOL_stublist_abcdefgh",
                 greeting: bytes | None = None, silent: bool = False):
        self.lock = lock
        self.greeting = greeting  # sent instead of the $Lock, when set
        self.silent = silent  # accept and say nothing
        self.got: list[dict] = []
        self.server = None
        self.port = 0
        self.writers = []

    async def start(self) -> "StubList":
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def close(self) -> None:
        for w in self.writers:
            w.close()
        self.server.close()
        await self.server.wait_closed()

    async def _handle(self, reader, writer) -> None:
        self.writers.append(writer)
        peer_port = writer.get_extra_info("peername")[1]
        try:
            if self.silent:
                await asyncio.sleep(30)
                return
            writer.write(self.greeting if self.greeting is not None
                         else b"$Lock " + self.lock + b" Pk=stublist|")
            await writer.drain()
            data = b""
            while data.count(b"|") < 7:
                chunk = await asyncio.wait_for(reader.read(2048), 5)
                if not chunk:
                    break
                data += chunk
            parts = data.split(b"|")
            if not parts[0].startswith(b"$Key "):
                self.got.append({"error": "no $Key", "raw": data})
                return
            key = dcn_unescape(parts[0][len(b"$Key "):])
            if key == dcn_unescape(lock_to_key(self.lock)):
                which = "standard"
            elif key == dcn_unescape(lock_to_key(self.lock, legacy_magic(peer_port))):
                which = "legacy"
            else:
                which = "wrong"
            fields = [CP.decode(p) for p in parts[1:7]]
            self.got.append({"key": which, "name": fields[0], "address": fields[1],
                             "description": fields[2], "users": fields[3], "share": fields[4],
                             "list": fields[5]})
        except (asyncio.TimeoutError, ConnectionError, asyncio.CancelledError):
            pass
        finally:
            writer.close()
