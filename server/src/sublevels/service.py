"""The service layer: one object the HTTP routes, the room-call exports and (from M1) the NMDC
sessions all call, so a hub user is the same record however they connected.

M0 holds no hub state yet. The service knows its version and when it started, answers a ping, and
publishes a small status subtree under ``sublevels.server``.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Callable, Protocol

from . import NAMESPACE, __version__


class Tree(Protocol):
    """What the service needs of a state tree: ``pycvc.ariadne.Host`` has it."""

    def set_tree(self, key: str, value: Any) -> None: ...


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


class Service:
    """The sublevels service. ``tree`` is where it publishes (a ``pycvc.ariadne.Host``, or None to
    publish nothing); ``clock`` is ``time.time`` unless a test fixes it."""

    def __init__(self, tree: Tree | None = None, *, clock: Callable[[], float] = time.time):
        self.tree = tree
        self.clock = clock
        self.version = __version__
        self.started = clock()
        self.status = "starting"
        self.hub = None  # the NMDC hub (sublevels.nmdc.Hub), once the server makes it
        self.admin_token = ""  # the admin API's bearer token; "" turns the admin API off
        self.txwtf = {"mode": "off", "url": ""}  # where TX.WTF is (sublevels.txwtf)

    @property
    def server_path(self) -> str:
        return f"{NAMESPACE}.server"

    def uptime(self) -> float:
        return max(0.0, self.clock() - self.started)

    def server_info(self) -> dict:
        """What ``sublevels.server`` holds: the version, the start time and the status."""
        return {
            "version": self.version,
            "started": _iso(self.started),
            "status": self.status,
            "txwtf": dict(self.txwtf),
        }

    def health(self) -> dict:
        """The answer to ``GET /healthz``."""
        return {"status": "ok", "version": self.version, "uptime": round(self.uptime(), 3),
                "txwtf": self.txwtf["mode"]}

    def public_stats(self) -> dict:
        """What anyone may read: the live user count (D14), and the NMDCS address and keyprint."""
        if self.hub is None:
            return {"users": 0}
        out = {"users": len(self.hub.users)}
        if self.hub.keyprint:  # P7 §1: published beside the address, for clients to pin
            out["nmdcs"] = {"address": self.hub.nmdcs_url(), "keyprint": self.hub.keyprint}
        return out

    def admin_stats(self) -> dict:
        """The D14 counters and live values, for operators."""
        return {"version": self.version, "uptime": round(self.uptime(), 3),
                "hub": self.hub.stats() if self.hub is not None else None}

    def ping(self, who: str = "") -> dict:
        """The answer to the ``sublevels.ping`` room-call: the server's version, and the caller's
        principal id as the relay gave it."""
        return {"pong": True, "version": self.version, "who": who}

    def publish(self) -> None:
        """Write ``sublevels.server`` to the tree, replacing what was there."""
        if self.tree is not None:
            self.tree.set_tree(self.server_path, self.server_info())

    def start(self) -> None:
        self.status = "up"
        self.publish()

    def stop(self) -> None:
        self.status = "stopping"
        self.publish()
