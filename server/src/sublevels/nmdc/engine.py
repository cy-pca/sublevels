"""The NMDC listener on libcvc's native connection engine (ARIADNE_NET_ENGINE N1/N2:
``pycvc.ariadne.net``, ``host.net`` in arisrvx), behind the same seam as the asyncio listener.

The engine's I/O threads accept, split each connection's input on ``|`` (P1 §1), queue output and
write it, so the hub's loop sees one batch of frames at a time and makes one ``broadcast()`` call
per fan-out, whatever the group's size: the hub's groups (``all``, ``hello``, ``search``,
``search_active``, ``ops``, ``ops_userip``) are the engine's groups. Everything else (login,
chat, search, connection signalling, the limits) is the hub's, unchanged.

TLS on the engine is N3's, so NMDCS stays on the asyncio listener until then; the two transports
serve one hub, and a group send reaches the members of both.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from .hub import Hub, Session
from .listener import split_listen

log = logging.getLogger("sublevels.nmdc")


def available() -> bool:
    """Whether this pycvc has the connection engine (libcvc N2, transfix/libcvc#1434)."""
    try:
        from pycvc.ariadne import net  # noqa: F401
        return hasattr(net, "net_engine")
    except ImportError:
        return False


class EngineFanout:
    """The hub's groups as the engine's: join, leave and one broadcast per group send."""

    def __init__(self, net):
        self.net = net

    def join(self, s: Session, group: str) -> None:
        self.net.join(s.conn.id, group)

    def leave(self, s: Session, group: str) -> None:
        self.net.leave(s.conn.id, group)

    def broadcast(self, group: str, data: bytes, exclude: Session | None) -> None:
        self.net.broadcast(group, data, exclude.conn.id if exclude is not None else 0)

    def stats(self) -> dict:
        """The engine's own counters (net_stats().engine), for the hub's stats()."""
        c = self.net.stats().engine
        names = ("open", "accepted", "bytes_in", "frames_in", "bytes_out", "messages_out",
                 "broadcasts", "broadcast_receivers", "queued_bytes", "max_queued_bytes")
        out = {n: int(getattr(c, n)) for n in names}
        out["closed"] = {k: int(v) for k, v in dict(c.closed).items() if v}
        out["io_threads"] = int(self.net.io_threads())
        return out


class EngineConn:
    """One engine connection, as the hub's Conn: ``write`` queues on the engine, ``close`` sends
    what is queued and then closes."""

    __slots__ = ("net", "id", "fanout", "closing")

    def __init__(self, net, conn_id: int, fanout: EngineFanout):
        self.net = net
        self.id = conn_id
        self.fanout = fanout
        self.closing = False

    def write(self, data: bytes) -> None:
        if not self.closing:
            self.net.send(self.id, data)

    def close(self) -> None:
        if not self.closing:
            self.closing = True
            self.net.close(self.id, True)


# Engine close reasons, as the hub records why a connection ended.
_REASONS = {"peer_closed": "closed by the client", "slow_reader": "slow reader",
            "frame_too_long": "message too long", "bytes_limit": "received too much",
            "engine_stopped": "hub shutting down"}


@dataclass
class EngineListener:
    net: object
    listener: object  # the net_listener
    own_engine: bool  # this module made the engine (plain python), so it stops it
    protocol: str = "nmdc"
    address: str = ""
    sessions: dict = field(default_factory=dict)  # conn id -> Session

    def addresses(self) -> list[tuple[str, str, int]]:
        return [(self.protocol, self.address, self.listener.port)]

    async def close(self, hub: Hub | None = None) -> None:
        self.net.stop_listening(int(self.listener))
        if hub is not None:
            for s in list(self.sessions.values()):
                hub.close(s, "hub shutting down")
        await asyncio.sleep(0.2)  # let the flushing closes go out
        if self.own_engine:
            # Stop it, then let the loop go: an engine left attached to a loop at exit takes the
            # process down (std::terminate, a joinable thread destroyed).
            await asyncio.to_thread(self.net.stop)
            self.net.detach_loop()


def make_engine(io_threads: int = 2):
    """An engine for this loop, under plain python (arisrvx has its own: ``host.net``)."""
    from pycvc.ariadne import net as pnet

    net = pnet.net_engine(pnet.engine_options(io_threads=io_threads))
    net.attach_loop(asyncio.get_running_loop())
    return net


def tls_available(net) -> bool:
    """Whether ``net`` can take TLS connections (libcvc N3, 4.0.10+, built with libssl)."""
    f = getattr(net, "tls_available", None)
    return bool(f is not None and f())


def start_engine_listener(hub: Hub, net, *, own: bool = False, protocol: str = "nmdc",
                          listen: str | None = None, tls: tuple[str, str] | None = None,
                          watch_ms: int = 30000, ours: bool | None = None) -> EngineListener:
    """Listen for NMDC (``protocol`` "nmdc", on ``hub.config.nmdc_listen``) or NMDCS ("nmdcs",
    with ``tls``, a certificate and a key file) on the engine ``net``. ``own``: this listener stops
    the engine when it closes. An NMDCS listener publishes its certificate's keyprint, and the
    engine reloads the files when they change (every ``watch_ms``). ``ours``: the hub made the
    engine (plain python), as against the host's (arisrvx's ``host.net``); ``own`` by default."""
    from pycvc.ariadne import net as pnet

    lim = hub.limits
    address, port = split_listen(listen or hub.config.nmdc_listen)
    kw = dict(address=address, port=port, name=protocol, frames=pnet.framing.delimited(b"|"),
              backlog=hub.config.listen_backlog, max_frame=lim.online_message_max,
              max_queued=lim.output_queue_max)
    if tls is not None:
        # TLS 1.2 and up (P7 §1), the engine's default minimum.
        kw["tls"] = pnet.tls_options(certificate_file=tls[0], key_file=tls[1], watch=watch_ms)
    listener = net.listen(**kw)
    out = EngineListener(net, listener, own, protocol, address)
    fanout = next((f for f in hub.engines if f.net is net), None)
    if fanout is None:  # one per engine: its groups hold every listener's connections
        fanout = EngineFanout(net)
        hub.engines.append(fanout)
    secure = tls is not None
    if secure:
        hub.set_keyprint(listener.keyprint)

        @listener.on_tls_reload
        def reloaded(keyprint):
            log.info("NMDCS: certificate reloaded (%s)", keyprint)
            hub.set_keyprint(keyprint)

    @listener.on_accept
    def accepted(conn, info):
        s = hub.connect(EngineConn(net, conn, fanout), info.peer_address, info.peer_port,
                        protocol, secure)
        if s.state != "closed":
            out.sessions[conn] = s
        return None

    @listener.on_frames
    def frames(batch):
        for conn, fs in batch:
            s = out.sessions.get(conn)
            if s is None or s.state == "closed":
                continue
            if not hub.received(s, sum(len(f) for f in fs)):
                continue
            limit = hub.max_message(s)
            for f in fs:
                if len(f) > limit:
                    hub.too_long(s)
                    break
                hub.message(s, bytes(f))
                if s.state == "closed":
                    break

    @listener.on_close
    def closed(conn, reason, message=""):
        s = out.sessions.pop(conn, None)
        if s is None:
            return
        if reason == "slow_reader":
            hub.counters.refuse("slow_reader")
        elif reason == "frame_too_long":
            hub.counters.drop("too_long")
        if reason == "error":  # a socket error: as the asyncio path says it
            why = f"connection lost ({message})" if message else "connection lost"
        else:
            why = _REASONS.get(reason, f"engine: {reason}")
        hub.disconnect(s, why)

    log.info("NMDC: listening for %s on %s:%d (native engine, %d I/O threads, %s)", protocol,
             address, listener.port, net.io_threads(),
             "its own" if (own if ours is None else ours) else "the host's")
    return out
