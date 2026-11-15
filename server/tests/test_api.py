"""The HTTP API through FastAPI's TestClient: no pycvc, no network."""

from fastapi.testclient import TestClient

from sublevels import __version__
from sublevels.api import create_app
from sublevels.service import Service


def test_healthz():
    client = TestClient(create_app(Service()))
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["version"] == __version__
    assert body["uptime"] >= 0


def test_unknown_route():
    assert TestClient(create_app(Service())).get("/nope").status_code == 404
