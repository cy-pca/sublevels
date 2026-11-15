"""Run the sublevels service: the HTTP API, the room-call exports and the published subtree, on one
``pycvc.ariadne.Host`` (libcvc ARIADNE_SCRIPTING_HOSTS.md 6.5).

Under plain python (a pycvc beside it)::

    python -m sublevels.server [--http-listen ADDR:PORT]

In arisrvx, through the launcher beside this package (arisrvx's ``--python`` takes a script path,
and passes the program no arguments)::

    arisrvx --python server/main.py --doc server/share/sublevels/server.ari [relay flags]

The HTTP address is ``--http-listen``, else ``$SUBLEVELS_HTTP_LISTEN``, else 127.0.0.1:8411; ``off``
serves no HTTP. SIGTERM or SIGINT stops it: uvicorn drains, then the host's loop ends.
"""

from __future__ import annotations

import argparse
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Sequence

from . import NAMESPACE, __version__, api, rpc
from .service import Service

log = logging.getLogger("sublevels.server")

DEFAULT_HTTP = "127.0.0.1:8411"


@dataclass
class Server:
    """One run of the service: its host, the service, the HTTP app and the room-call names."""

    host: Any
    service: Service
    app: Any
    exports: list[str] = field(default_factory=list)


def build(host: Any = None) -> Server:
    """Make (or adopt) the program's Host and put the service on it: the exports registered, the
    HTTP app made. Nothing is served or published until :func:`serve`."""
    if host is None:
        from pycvc.ariadne import Host, current_host

        host = current_host() or Host()
    service = Service(host)
    return Server(host, service, api.create_app(service), rpc.register(service))


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
    p.add_argument("--version", action="version", version=f"sublevels {__version__}")
    args = p.parse_args(argv)
    if args.http_listen != "off":
        try:
            split_address(args.http_listen)
        except ValueError as e:
            p.error(f"--http-listen: {e}")
    return args


def serve(server: Server, http_listen: str = DEFAULT_HTTP) -> int:
    """Serve until the host stops; returns its exit status."""
    if http_listen != "off":
        from pycvc.ariadne import serve_asgi

        h, p = split_address(http_listen)
        serve_asgi(server.app, host=h, port=p)
    server.service.start()
    log.info("sublevels %s: publishing %s.server; room-call: %s", __version__, NAMESPACE,
             ", ".join(server.exports) or "none (this host has no room-call)")
    try:
        return server.host.run()
    finally:
        server.service.stop()


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if not logging.getLogger().handlers:  # arisrvx sets its own; plain python gets one like it
        logging.basicConfig(level=logging.INFO, format="[sublevels] %(name)s: %(levelname)s: "
                            "%(message)s")
    return serve(build(), args.http_listen)


if __name__ == "__main__":
    raise SystemExit(main())
