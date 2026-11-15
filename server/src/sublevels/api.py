"""The HTTP API: a FastAPI app over the service. M0 has the health check only; the admin API and
webhooks come in M2."""

from __future__ import annotations

from fastapi import FastAPI

from . import __version__
from .service import Service


def create_app(service: Service) -> FastAPI:
    app = FastAPI(title="sublevels", version=__version__)

    @app.get("/healthz")
    def healthz() -> dict:
        return service.health()

    return app
