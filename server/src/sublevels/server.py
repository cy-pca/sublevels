"""Run the sublevels service: the NMDC hub, the HTTP API, the room-call exports and the published
subtree, on one ``pycvc.ariadne.Host`` (libcvc ARIADNE_SCRIPTING_HOSTS.md 6.5).

Under plain python (a pycvc beside it)::

    python -m sublevels.server [--http-listen ADDR:PORT] [--nmdc-listen ADDR:PORT]
                               [--nmdcs-listen ADDR:PORT]

In arisrvx, through the launcher beside this package (arisrvx's ``--python`` takes a script path,
and passes the program no arguments)::

    arisrvx --python server/main.py --doc server/share/sublevels/server.ari [relay flags]

The HTTP address is ``--http-listen``, else ``$SUBLEVELS_HTTP_LISTEN``, else 127.0.0.1:8411; ``off``
serves no HTTP. The hub's settings come from ``SUBLEVELS_*`` variables (``nmdc.HubConfig.from_env``):
it listens for NMDC on ``$SUBLEVELS_NMDC_LISTEN`` (0.0.0.0:411) and for NMDCS on
``$SUBLEVELS_NMDCS_LISTEN`` (0.0.0.0:4111, with ``$SUBLEVELS_TLS_CERT`` and ``$SUBLEVELS_TLS_KEY``).
The admin API's token is read from ``$SUBLEVELS_ADMIN_TOKEN_FILE`` (or ``$SUBLEVELS_ADMIN_TOKEN``).
SIGTERM or SIGINT stops it: uvicorn drains, the hub closes its connections, then the loop ends.
"""

from __future__ import annotations

import argparse
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Sequence

from . import NAMESPACE, __version__, api, rpc
from .nmdc import Hub, HubConfig, start_listeners
from .service import Service

log = logging.getLogger("sublevels.server")

DEFAULT_HTTP = "127.0.0.1:8411"


@dataclass
class Server:
    """One run of the service: its host, the service, the HTTP app and the room-call names."""

    host: Any
    service: Service
    app: Any
    hub: Hub
    exports: list[str] = field(default_factory=list)


def admin_token(env=None) -> str:
    env = os.environ if env is None else env
    path = env.get("SUBLEVELS_ADMIN_TOKEN_FILE")
    if path:
        with open(path, encoding="utf-8") as f:
            return f.readline().strip()
    return env.get("SUBLEVELS_ADMIN_TOKEN", "").strip()


def build(host: Any = None, config: HubConfig | None = None) -> Server:
    """Make (or adopt) the program's Host and put the service on it: the hub made, the exports
    registered, the HTTP app made. Nothing is served or published until :func:`serve`."""
    if host is None:
        from pycvc.ariadne import Host, current_host

        host = current_host() or Host()
    service = Service(host)
    hub = Hub(config or HubConfig.from_env(), tree=host, call_later=host.loop.call_later)
    service.hub = hub
    service.admin_token = admin_token()
    return Server(host, service, api.create_app(service), hub, rpc.register(service))


def split_address(address: str) -> tuple[str, int]:
    """``ADDR:PORT`` (``[v6]:PORT`` too) as (addr, port); port 0 is any free one."""
    addr, sep, port = address.rpartition(":")
    if not sep or not port.isdigit() or not 0 <= int(port) <= 65535:
        raise ValueError(f"{address!r} is not ADDR:PORT")
    return addr.strip("[]") or "127.0.0.1", int(port)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m sublevels.server",
                                description="Run the sublevels service.")
    p.add_argument("--http-listen", default=os.environ.get("SUBLEVELS_HTTP_LISTEN") or DEFAULT_HTTP,
                   metavar="ADDR:PORT",
                   help=f"where the HTTP API listens, or 'off' (default {DEFAULT_HTTP})")
    p.add_argument("--nmdc-listen", metavar="ADDR:PORT",
                   help="where the hub listens for NMDC, or 'off' ($SUBLEVELS_NMDC_LISTEN, "
                        "default 0.0.0.0:411)")
    p.add_argument("--nmdcs-listen", metavar="ADDR:PORT",
                   help="where the hub listens for NMDCS (TLS), or 'off' ($SUBLEVELS_NMDCS_LISTEN, "
                        "default 0.0.0.0:4111; needs $SUBLEVELS_TLS_CERT and $SUBLEVELS_TLS_KEY)")
    p.add_argument("--version", action="version", version=f"sublevels {__version__}")
    args = p.parse_args(argv)
    for flag in ("http_listen", "nmdc_listen", "nmdcs_listen"):
        value = getattr(args, flag)
        if value is not None and value != "off":
            try:
                split_address(value)
            except ValueError as e:
                p.error(f"--{flag.replace('_', '-')}: {e}")
    return args


def hub_config(args: argparse.Namespace) -> HubConfig:
    cfg = HubConfig.from_env()
    if args.nmdc_listen:
        cfg = cfg.with_(nmdc_listen=args.nmdc_listen)
    if args.nmdcs_listen:
        cfg = cfg.with_(nmdcs_listen=args.nmdcs_listen)
    return cfg


def serve(server: Server, http_listen: str = DEFAULT_HTTP) -> int:
    """Serve until the host stops; returns its exit status."""
    if http_listen != "off":
        from pycvc.ariadne import serve_asgi

        h, p = split_address(http_listen)
        serve_asgi(server.app, host=h, port=p)
    host = server.host
    listeners = None
    if not getattr(host, "checking", False):  # arisrvx check binds nothing
        # The loop is not running yet (host.run() runs it), so this binds before the relay listens,
        # and a port that cannot be bound stops the program here, with its traceback.
        listeners = host.loop.run_until_complete(start_listeners(server.hub))
    server.service.start()
    server.hub.publish()
    log.info("sublevels %s: publishing %s.server and %s; room-call: %s", __version__, NAMESPACE,
             server.hub.tree_path,
             ", ".join(server.exports) or "none (this host has no room-call)")
    try:
        return host.run()
    finally:
        if listeners is not None and not host.loop.is_closed():
            host.loop.run_until_complete(listeners.close(server.hub))
        server.service.stop()


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if not logging.getLogger().handlers:  # arisrvx sets its own; plain python gets one like it
        logging.basicConfig(level=logging.INFO, format="[sublevels] %(name)s: %(levelname)s: "
                            "%(message)s")
    return serve(build(config=hub_config(args)), args.http_listen)


if __name__ == "__main__":
    raise SystemExit(main())
