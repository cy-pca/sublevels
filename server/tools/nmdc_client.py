"""A small asyncio NMDC client for tests, the arisrvx smoke run and the soak: it logs in as P2 says
a client does, and reads and writes messages. It is a test tool written from spec/nmdc/, not a
user's client.

    python tools/nmdc_client.py HOST PORT [--tls] [--nick N]   # log in, chat once, print, leave
"""

from __future__ import annotations

import argparse
import asyncio
import ssl
import sys
import time

DCN = (0, 5, 36, 96, 124, 126)


def lock_to_key(lock: bytes) -> bytes:
    """P1 §5, as a client computes it."""
    n = len(lock)
    key = bytearray(n)
    for i in range(1, n):
        key[i] = lock[i] ^ lock[i - 1]
    key[0] = lock[0] ^ lock[-1] ^ lock[-2] ^ 5
    out = bytearray()
    for b in key:
        b = ((b << 4) & 0xF0) | (b >> 4)
        out += b"/%%DCN%03d%%/" % b if b in DCN else bytes([b])
    return bytes(out)


class Client:
    def __init__(self, nick: str, *, supports: str = "NoHello NoGetINFO UserIP2 BotList HubTopic",
                 mode: str = "A", share: int = 0, encoding: str = "cp1252"):
        self.nick = nick
        self.supports = supports
        self.mode = mode
        self.share = share
        self.encoding = encoding
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None
        self.received: list[str] = []  # every message read, decoded, with its '|'

    def myinfo(self) -> str:
        return (f"$MyINFO $ALL {self.nick} <test V:1,M:{self.mode},H:1/0/0,S:1>$ $LAN(T3)\x01$$"
                f"{self.share}$|")

    async def connect(self, host: str, port: int, *, tls: bool = False, timeout: float = 10.0):
        ctx = None
        if tls:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE  # the tests' certificates are self-signed
        self.reader, self.writer = await asyncio.wait_for(
            asyncio.open_connection(host, port, ssl=ctx, limit=1 << 22), timeout)

    async def read(self, timeout: float = 10.0) -> str:
        data = await asyncio.wait_for(self.reader.readuntil(b"|"), timeout)
        text = data.decode(self.encoding, errors="replace")
        self.received.append(text)
        return text

    async def read_until(self, predicate, timeout: float = 10.0) -> str:
        end = time.monotonic() + timeout
        while True:
            m = await self.read(max(0.01, end - time.monotonic()))
            if predicate(m):
                return m

    def send(self, *messages: str | bytes) -> None:
        for m in messages:
            self.writer.write(m if isinstance(m, bytes) else m.encode(self.encoding))

    async def login(self, timeout: float = 10.0) -> None:
        """$Lock → $Supports, $Key, $ValidateNick → $Hello → $Version, $GetNickList, $MyINFO, then
        wait for our own $MyINFO back (the hub lists it to NoHello clients)."""
        lock_msg = await self.read(timeout)
        if not lock_msg.startswith("$Lock "):
            raise RuntimeError(f"expected $Lock, got {lock_msg!r}")
        raw = lock_msg.encode(self.encoding)[len(b"$Lock "):].split(b" Pk=")[0]
        self.send(f"$Supports {self.supports}|".encode(), b"$Key " + lock_to_key(raw) + b"|",
                  f"$ValidateNick {self.nick}|".encode(self.encoding))
        m = await self.read_until(lambda m: m.startswith(("$Hello ", "$ValidateDenide",
                                                          "$GetPass", "$HubIsFull")), timeout)
        if not m.startswith("$Hello "):
            raise RuntimeError(f"login refused: {m!r}")
        self.send("$Version 1,0091|", "$GetNickList|", self.myinfo())
        mine = self.myinfo()
        await self.read_until(lambda m: m == mine, timeout)
        await self.writer.drain()

    async def close(self) -> None:
        if self.writer is not None:
            self.writer.close()
            try:
                await self.writer.wait_closed()
            except (ConnectionError, ssl.SSLError):
                pass


async def _main(argv) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("host")
    p.add_argument("port", type=int)
    p.add_argument("--tls", action="store_true")
    p.add_argument("--nick", default="probe")
    a = p.parse_args(argv)
    c = Client(a.nick)
    await c.connect(a.host, a.port, tls=a.tls)
    await c.login()
    c.send(f"<{a.nick}> hello from {a.nick}|")
    echoed = await c.read_until(lambda m: m.startswith(f"<{a.nick}> "))
    print(f"logged in as {a.nick}; chat echoed: {echoed}")
    await c.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main(sys.argv[1:])))
