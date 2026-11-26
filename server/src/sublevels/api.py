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
- ``GET /admin/analytics``: the metrics there are; with ``metric`` (and ``resolution`` 10s, 1m
  or 1h, ``since``, ``until``, Unix seconds; the last hour by default), its points (D14).
- In hublist mode (D16, P10 §4): ``GET /hublist.xml``, ``/hublist.xml.bz2`` and
  ``/hublist-<cc>.xml.bz2``, with ``ETag`` and ``Last-Modified``; and for operators
  ``GET /admin/hublist`` (the entries, by ``state``), ``PUT /admin/hublist/state`` (``{"address",
  "state"}``: listed, hidden, banned...), ``DELETE /admin/hublist/entry?address=`` and
  ``POST /admin/hublist/ping`` (``{"address"}``: ping it now).
- ``GET /admin/hublists``: the hub-list targets, each registration's result and the pingers'
  visits (P9, D15); ``POST /admin/hublists/register`` registers with every push target now.

The ``/admin`` routes take ``Authorization: Bearer <token>`` (the service's ``admin_token``;
without one they are off). The admin document and stored history are M2's.
"""

from __future__ import annotations

import hmac

from email.utils import formatdate

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import Response

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

    @app.get("/admin/analytics", dependencies=[Depends(operator)])
    def admin_analytics(metric: str = "", resolution: str = "10s", since: float | None = None,
                        until: float | None = None) -> dict:
        try:
            return service.analytics(metric, resolution, since, until)
        except ValueError as e:
            raise HTTPException(422, str(e))

    def _hublist():
        if service.hublist is None:
            raise HTTPException(404, "this server is not a hub list (SUBLEVELS_HUBLIST)")
        return service.hublist

    def _list_file(name: str, request: Request) -> Response:
        got = _hublist().lists.get(name)
        if got is None:
            raise HTTPException(404, f"no {name}")
        body, etag, mtime = got
        headers = {"ETag": etag, "Last-Modified": formatdate(mtime, usegmt=True),
                   "Cache-Control": "public, max-age=300"}
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=headers)
        kind = "application/x-bzip2" if name.endswith(".bz2") else "application/xml"
        return Response(body, media_type=kind, headers=headers)

    @app.get("/hublist.xml")
    def hublist_xml(request: Request):
        return _list_file("hublist.xml", request)

    @app.get("/hublist.xml.bz2")
    def hublist_bz2(request: Request):
        return _list_file("hublist.xml.bz2", request)

    @app.get("/hublist-{cc}.xml.bz2")
    def hublist_country(cc: str, request: Request):
        return _list_file(f"hublist-{cc.lower()}.xml.bz2", request)

    @app.get("/admin/hublist", dependencies=[Depends(operator)])
    def admin_hublist(state: str = "") -> dict:
        h = _hublist()
        return {"stats": h.stats(), "entries": h.entries(state)}

    @app.put("/admin/hublist/state", dependencies=[Depends(operator)])
    def admin_hublist_state(address: str = Body(...), state: str = Body(...)) -> dict:
        try:
            e = _hublist().set_state(address, state)
        except ValueError as err:
            raise HTTPException(422, str(err))
        if e is None:
            raise HTTPException(404, f"no entry {address!r}")
        return e

    @app.delete("/admin/hublist/entry", dependencies=[Depends(operator)])
    def admin_hublist_delete(address: str) -> dict:
        try:
            if not _hublist().delete(address):
                raise HTTPException(404, f"no entry {address!r}")
        except ValueError as err:
            raise HTTPException(422, str(err))
        return {"ok": True}

    @app.post("/admin/hublist/ping", dependencies=[Depends(operator)])
    async def admin_hublist_ping(address: str = Body(..., embed=True)) -> dict:
        from .hublist.store import normalise

        h = _hublist()
        try:
            e = h.store.get(normalise(address))
        except ValueError as err:
            raise HTTPException(422, str(err))
        if e is None:
            raise HTTPException(404, f"no entry {address!r}")
        return await h.ping_one(e) or {"busy": True}

    @app.get("/admin/hublists", dependencies=[Depends(operator)])
    def admin_hublists() -> dict:
        return service.admin_hublists()

    @app.post("/admin/hublists/register", dependencies=[Depends(operator)])
    async def register_hublists() -> dict:
        return await service.register_hublists()

    return app
