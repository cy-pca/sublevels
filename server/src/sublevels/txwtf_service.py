"""TX.WTF beside the hub: in this process (the default), a separate instance, or none.

TX.WTF (the ``txwtf`` package, ``txwtf.api.create_app``) is a FastAPI app, so in-process it is one
more ASGI app on the host's loop (``serve_asgi``), next to the hub's own HTTP API. Its settings are
TX.WTF's own environment variables (its database URL, storage path, secrets), read by TX.WTF.

The mode is ``--txwtf`` (plain python) or ``$SUBLEVELS_TXWTF``:

- ``embedded`` (the default): serve TX.WTF here, on ``--txwtf-listen`` / ``$SUBLEVELS_TXWTF_LISTEN``
  (127.0.0.1:8086). If the package is not installed, the hub warns and runs without it, unless
  ``embedded`` was asked for outright, which then stops the program.
- a URL (``https://tx.wtf``): TX.WTF runs elsewhere; the hub records where.
- ``off``: no TX.WTF.

Accounts and native sign-in through TX.WTF are M2's (roadmap D9); M1 only places the service.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger("sublevels.txwtf")

DEFAULT_LISTEN = "127.0.0.1:8086"


@dataclass
class TxwtfPlan:
    mode: str  # "embedded", "external" or "off"
    url: str = ""  # where it answers ("" when off)
    listen: str = ""  # embedded: the address it is served on
    note: str = ""  # why the plan differs from what was asked, if it does

    def as_tree(self) -> dict:
        return {"mode": self.mode, "url": self.url}


def plan(asked: str | None, listen: str | None) -> TxwtfPlan:
    """What to do for ``asked`` (None: the default, embedded if TX.WTF is installed)."""
    value = (asked or "embedded").strip()
    listen = listen or DEFAULT_LISTEN
    if value == "off":
        return TxwtfPlan("off")
    if value == "embedded":
        return TxwtfPlan("embedded", f"http://{listen}", listen)
    if "://" in value:
        return TxwtfPlan("external", value)
    raise ValueError(f"--txwtf: {value!r} is not 'embedded', 'off' or a URL")


def load_app():
    """TX.WTF's ASGI app. Raises ImportError when the txwtf package is not installed."""
    from txwtf.api import create_app

    return create_app()


def start(asked: str | None, listen: str | None, serve_asgi) -> TxwtfPlan:
    """Carry out the plan: serve TX.WTF here with ``serve_asgi(app, host=, port=)`` if embedded."""
    p = plan(asked, listen)
    if p.mode != "embedded":
        return p
    try:
        app = load_app()
    except ImportError as e:
        if asked:  # asked for outright: a missing package is an error
            raise SystemExit(f"sublevels: --txwtf embedded, but TX.WTF is not installed ({e}); "
                             "install txwtf-cp312, or pass --txwtf off or --txwtf URL") from e
        log.warning("TX.WTF is not installed, so it is not served here (%s). Install txwtf-cp312, "
                    "or set SUBLEVELS_TXWTF=off or a URL to say so.", e)
        return TxwtfPlan("off", note="txwtf not installed")
    host, _, port = p.listen.rpartition(":")
    serve_asgi(app, host=host.strip("[]") or "127.0.0.1", port=int(port))
    log.info("TX.WTF: serving in this process on %s", p.listen)
    return p
