"""The HTTP API: a FastAPI app over the service.

- ``GET /healthz``: up, and the version.
- ``GET /stats``: the live user count, which is public (D14).
- ``GET /admin/stats``: D14's counters and live values, for operators, with
  ``Authorization: Bearer <token>`` (the service's ``admin_token``; without one the route is off).
  The admin document and stored history are M2's.
"""

from __future__ import annotations

import hmac

from fastapi import FastAPI, Header, HTTPException

from . import __version__
from .service import Service


def create_app(service: Service) -> FastAPI:
    app = FastAPI(title="sublevels", version=__version__)

    @app.get("/healthz")
    def healthz() -> dict:
        return service.health()

    @app.get("/stats")
    def stats() -> dict:
        return service.public_stats()

    @app.get("/admin/stats")
    def admin_stats(authorization: str = Header(default="")) -> dict:
        token = service.admin_token
        if not token:
            raise HTTPException(404, "the admin API is off (no admin token)")
        scheme, _, given = authorization.partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(given.encode(), token.encode()):
            raise HTTPException(401, "a bearer token is required",
                                headers={"WWW-Authenticate": "Bearer"})
        return service.admin_stats()

    return app
