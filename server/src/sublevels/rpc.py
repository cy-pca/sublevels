"""The room-call front end: the functions native clients call with ``(room-call "sublevels.ping")``.

Each export is a thin wrapper over the service, as the HTTP routes are. They are registered with
``pycvc.ariadne.export``, which needs libcvc S2 (transfix/libcvc#1122). A host whose
``pycvc.ariadne`` predates it (arisrvx and pycvc 3.7.13+cvc.2 and older) serves HTTP and the
published subtree but takes no calls; :func:`register` then registers nothing and says so.
"""

from __future__ import annotations

import logging

from .service import Service

log = logging.getLogger("sublevels.rpc")

PING = "sublevels.ping"
# Native operators' functions (P6 §6), each for callers with the admin role.
OPERATOR = ("sublevels.kick", "sublevels.redirect", "sublevels.ban", "sublevels.unban",
            "sublevels.bans", "sublevels.info", "sublevels.analytics")


def available() -> bool:
    """Whether this host's ``pycvc.ariadne`` has room-call exports."""
    try:
        from pycvc.ariadne import export  # noqa: F401
    except ImportError:
        return False
    return True


def register(service: Service) -> list[str]:
    """Export the service's room-call functions; returns their names (none without S2)."""
    try:
        from pycvc.ariadne import export
    except ImportError:
        log.warning("this host's pycvc.ariadne has no export (libcvc S2): %s is not callable",
                    PING)
        return []

    @export(name=PING, clients=True, timeout=5)
    def ping(ctx):
        return service.ping(str(ctx.principal.get("id") or ""))

    def op(name):
        def wrap(fn):
            return export(fn, name=name, clients=True, roles=["admin"], timeout=10)
        return wrap

    @op("sublevels.kick")
    def kick(ctx, target, reason=""):
        return service.operator_call(ctx.principal, "kick", target, reason)

    @op("sublevels.redirect")
    def redirect(ctx, target, address, reason=""):
        return service.operator_call(ctx.principal, "redirect", target, address, reason)

    @op("sublevels.ban")
    def ban(ctx, target, duration, reason=""):
        return service.operator_call(ctx.principal, "ban", target, duration, reason)

    @op("sublevels.unban")
    def unban(ctx, target):
        return service.operator_call(ctx.principal, "unban", target)

    @op("sublevels.bans")
    def bans(ctx, text=""):
        return service.operator_call(ctx.principal, "bans", text)

    @op("sublevels.info")
    def info(ctx, target):
        return service.operator_call(ctx.principal, "info", target)

    @op("sublevels.analytics")
    def analytics(ctx, metric="", resolution="10s", since=""):
        return service.operator_call(ctx.principal, "analytics", metric, resolution, since)

    return [PING, *OPERATOR]
