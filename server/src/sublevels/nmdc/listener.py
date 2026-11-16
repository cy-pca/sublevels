"""The NMDC listeners on the host's asyncio loop: plain TCP (411) and NMDCS, TLS from the first byte
(4111) (P1 §1, P7 §1).

Writes to a connection are gathered per event-loop turn and written as one buffer, so a fan-out of
a chat line or a search to thousands of users costs one ``transport.write`` per user per turn
(P4 §1). A reader whose unsent output passes ``output_queue_max`` is closed (P1 §6).
"""

from __future__ import annotations

import asyncio
import logging
import os
import ssl
from dataclasses import dataclass, field

from .hub import Hub, Session

log = logging.getLogger("sublevels.nmdc")


class NmdcConnection(asyncio.Protocol):
    def __init__(self, hub: Hub, protocol: str, tls: bool):
        self.hub = hub
        self.protocol = protocol
        self.tls = tls
        self.transport: asyncio.Transport | None = None
        self.session: Session | None = None
        self.buf = bytearray()
        self.out: list[bytes] = []
        self.pending = 0
        self.closing = False
        self.loop = asyncio.get_running_loop()

    # -- the hub's Conn ----------------------------------------------------------------------------

    def write(self, data: bytes) -> None:
        if self.closing or self.transport is None:
            return
        if not self.out:
            self.loop.call_soon(self.flush)
        self.out.append(data)
        self.pending += len(data)
        limit = self.hub.limits.output_queue_max
        if self.pending + self.transport.get_write_buffer_size() > limit:
            self.out.clear()
            self.pending = 0
            self.hub.counters.refuse("slow_reader")
            if self.session is not None:
                self.hub.close(self.session, "slow reader")
            self.abort()

    def flush(self) -> None:
        if self.out and self.transport is not None and not self.transport.is_closing():
            self.transport.write(b"".join(self.out))
        self.out.clear()
        self.pending = 0

    def close(self) -> None:
        """Send what is queued, then close."""
        if self.closing:
            return
        self.closing = True
        if self.transport is not None:
            self.flush()
            self.transport.close()

    def abort(self) -> None:
        self.closing = True
        if self.transport is not None:
            self.transport.abort()

    # -- asyncio -----------------------------------------------------------------------------------

    def connection_made(self, transport) -> None:
        self.transport = transport
        peer = transport.get_extra_info("peername") or ("0.0.0.0", 0)
        self.session = self.hub.connect(self, peer[0], peer[1], self.protocol, self.tls)

    def data_received(self, data: bytes) -> None:
        s = self.session
        if s is None or self.closing:
            return
        if not self.hub.received(s, len(data)):
            return
        buf = self.buf
        buf += data
        start = 0
        while not self.closing:
            end = buf.find(b"|", start)
            if end < 0:
                break
            if end + 1 - start > self.hub.max_message(s):
                self.hub.too_long(s)
                return
            self.hub.message(s, bytes(buf[start:end + 1]))
            start = end + 1
        if start:
            del buf[:start]
        if not self.closing and len(buf) > self.hub.max_message(s):
            self.hub.too_long(s)

    def connection_lost(self, exc) -> None:
        self.closing = True
        if self.session is not None:
            self.hub.disconnect(self.session, f"connection lost ({exc})" if exc else
                                "closed by the client")
        self.out.clear()
        self.transport = None


def split_listen(address: str) -> tuple[str, int]:
    host, sep, port = address.rpartition(":")
    if not sep or not port.isdigit():
        raise ValueError(f"{address!r} is not ADDR:PORT")
    return host.strip("[]") or "0.0.0.0", int(port)


class TlsContext:
    """The NMDCS listener's TLS context: TLS 1.2 and 1.3 (P7 §1). It reloads the certificate when
    its file changes; new handshakes use the new one, and open connections are untouched."""

    def __init__(self, cert: str, key: str):
        self.cert, self.key = cert, key
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.context.minimum_version = ssl.TLSVersion.TLSv1_2
        self.stamp = None
        self.load()

    def _stamp(self):
        return tuple(os.stat(p).st_mtime_ns for p in (self.cert, self.key))

    def load(self) -> None:
        self.context.load_cert_chain(self.cert, self.key)
        self.stamp = self._stamp()

    def reload_if_changed(self) -> bool:
        try:
            stamp = self._stamp()
        except OSError:
            return False
        if stamp == self.stamp:
            return False
        try:
            self.load()
        except (OSError, ssl.SSLError) as e:
            log.warning("NMDCS: the new certificate could not be loaded (%s); keeping the old one",
                        e)
            self.stamp = stamp
            return False
        log.info("NMDCS: reloaded the certificate %s", self.cert)
        return True


@dataclass
class Listeners:
    servers: list = field(default_factory=list)
    tls: TlsContext | None = None
    watcher: asyncio.Task | None = None

    def addresses(self) -> list[tuple[str, str, int]]:
        """(protocol, host, port) of each listening socket."""
        out = []
        for proto, server in self.servers:
            for sock in server.sockets or []:
                name = sock.getsockname()
                out.append((proto, name[0], name[1]))
        return out

    async def close(self, hub: Hub | None = None) -> None:
        if self.watcher is not None:
            self.watcher.cancel()
        for _, server in self.servers:
            server.close()
        if hub is not None:
            for s in list(hub.sessions.values()):
                hub.close(s, "hub shutting down")
        for _, server in self.servers:
            await server.wait_closed()


async def _watch_cert(tls: TlsContext, every: float) -> None:
    while True:
        await asyncio.sleep(every)
        tls.reload_if_changed()


async def start_listeners(hub: Hub, *, cert_check_every: float = 30.0) -> Listeners:
    """Listen as ``hub.config`` says: ``nmdc_listen`` (plain) and ``nmdcs_listen`` (TLS, with
    ``tls_cert`` and ``tls_key``); either may be "off". NMDCS without a certificate is skipped with
    a warning."""
    cfg = hub.config
    loop = asyncio.get_running_loop()
    out = Listeners()
    if cfg.nmdc_listen != "off":
        h, p = split_listen(cfg.nmdc_listen)
        server = await loop.create_server(lambda: NmdcConnection(hub, "nmdc", False), h, p)
        out.servers.append(("nmdc", server))
    if cfg.nmdcs_listen != "off":
        if not (cfg.tls_cert and cfg.tls_key):
            log.warning("NMDCS: no certificate configured (SUBLEVELS_TLS_CERT, SUBLEVELS_TLS_KEY): "
                        "not listening on %s", cfg.nmdcs_listen)
        else:
            out.tls = TlsContext(cfg.tls_cert, cfg.tls_key)
            h, p = split_listen(cfg.nmdcs_listen)
            server = await loop.create_server(lambda: NmdcConnection(hub, "nmdcs", True), h, p,
                                              ssl=out.tls.context)
            out.servers.append(("nmdcs", server))
            out.watcher = loop.create_task(_watch_cert(out.tls, cert_check_every))
    for proto, host, port in out.addresses():
        log.info("NMDC: listening for %s on %s:%d", proto, host, port)
    return out
