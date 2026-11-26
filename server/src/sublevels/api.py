"""The HTTP API: a FastAPI app over the service.

- ``GET /healthz``: up, and the version.
- ``GET /stats``: the live user count, which is public (D14).
- ``GET /admin/stats``: D14's counters and live values, for operators.
- ``GET /documents/motd`` and ``GET /documents/rules``: the hub's message of the day and rules
  (D9), which anyone may read; ``PUT /admin/documents/<name>`` with ``{"text": ..., "by": ...}``
  replaces one.
- ``GET /admin/connections``: the connection log (D13), live and stored, filtered by ``nick``,
  ``address``, ``client_id`` or ``country``, newest first (``limit``, default 100).
- ``GET /admin/bans``, ``POST /admin/bans`` (``{"target", "duration", "reason", "by"}``),
  ``DELETE /admin/bans/<id>``; ``POST /admin/kick`` (``{"nick", "reason", "by"}``); and
  ``GET /admin/oplog``, the operator log (P6 §4, §7). The admin API acts as an admin.
- ``GET /admin/hublists``: the hub-list targets, each registration's result and the pingers'
  visits (P9, D15); ``POST /admin/hublists/register`` registers with every push target now.

The ``/admin`` routes take ``Authorization: Bearer <token>`` (the service's ``admin_token``;
without one they are off). The admin document and stored history are M2's.
"""

from __future__ import annotations

import hmac

from fastapi import Body, Depends, FastAPI, Header, HTTPException

from . import __version__
from .nmdc.documents import NAMES, DocumentError
from .service import Service


def create_app(service: Service) -> FastAPI:
    app = FastAPI(title="sublevels", version=__version__)

    @app.get("/healthz")
    def healthz() -> dict:
        return service.health()

    @app.get("/stats")
    def stats() -> dict:
        return service.public_stats()

    def operator(authorization: str = Header(default="")) -> None:
        token = service.admin_token
        if not token:
            raise HTTPException(404, "the admin API is off (no admin token)")
        scheme, _, given = authorization.partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(given.encode(), token.encode()):
            raise HTTPException(401, "a bearer token is required",
                                headers={"WWW-Authenticate": "Bearer"})

    @app.get("/admin/stats", dependencies=[Depends(operator)])
    def admin_stats() -> dict:
        return service.admin_stats()

    def _name(name: str) -> str:
        if service.hub is None or name not in NAMES:
            raise HTTPException(404, f"no document {name!r}")
        return name

    @app.get("/documents/{name}")
    def document(name: str) -> dict:
        return service.hub.documents.get(_name(name))

    @app.put("/admin/documents/{name}", dependencies=[Depends(operator)])
    def set_document(name: str, text: str = Body(...), by: str = Body(default="")) -> dict:
        try:
            return service.hub.set_document(_name(name), text, by)
        except DocumentError as e:
            raise HTTPException(422, f"{name}: {e}")

    @app.get("/admin/connections", dependencies=[Depends(operator)])
    def admin_connections(nick: str = "", address: str = "", client_id: str = "",
                          country: str = "", limit: int = 100) -> dict:
        if service.hub is None:
            raise HTTPException(404, "no hub")
        return service.hub.connections(nick=nick, address=address, client_id=client_id,
                                       country=country, limit=limit)

    from .nmdc.operators import OpError

    def _ops():
        if service.hub is None:
            raise HTTPException(404, "no hub")
        return service.hub.ops

    @app.get("/admin/bans", dependencies=[Depends(operator)])
    def admin_bans(filter: str = "") -> dict:
        return {"bans": _ops().bans(filter)}

    @app.post("/admin/bans", dependencies=[Depends(operator)])
    def admin_ban(target: str = Body(...), duration: str = Body(...), reason: str = Body(""),
                  by: str = Body("admin API")) -> dict:
        try:
            return _ops().ban(by, "admin", target, duration, reason)
        except OpError as e:
            raise HTTPException(422, str(e))

    @app.delete("/admin/bans/{ban_id}", dependencies=[Depends(operator)])
    def admin_unban(ban_id: int, by: str = "admin API") -> dict:
        try:
            return _ops().unban(by, "admin", f"#{ban_id}")
        except OpError as e:
            raise HTTPException(404, str(e))

    @app.post("/admin/kick", dependencies=[Depends(operator)])
    def admin_kick(nick: str = Body(...), reason: str = Body(""),
                   by: str = Body("admin API")) -> dict:
        try:
            return _ops().kick(by, "admin", nick, reason)
        except OpError as e:
            raise HTTPException(422, str(e))

    @app.get("/admin/oplog", dependencies=[Depends(operator)])
    def admin_oplog(actor: str = "", target: str = "", action: str = "",
                    limit: int = 100) -> dict:
        hub = _ops().hub
        live = [a for a in hub.actions if (not actor or a["actor"] == actor)
                and (not target or a["target"] == target)
                and (not action or a["action"] == action)][-limit:][::-1]
        stored = hub.connlog.query_actions(actor=actor, target=target, action=action,
                                           limit=limit) if hub.connlog is not None else []
        return {"recent": live, "stored": stored}

    @app.get("/admin/hublists", dependencies=[Depends(operator)])
    def admin_hublists() -> dict:
        return service.admin_hublists()

    @app.post("/admin/hublists/register", dependencies=[Depends(operator)])
    async def register_hublists() -> dict:
        return await service.register_hublists()

    return app
