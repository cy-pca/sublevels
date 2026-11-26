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


def _service_with_hub(token=""):
    from nmdc_fakes import World

    w = World()
    w.login("alice")
    service = Service()
    service.hub = w.hub
    service.admin_token = token
    return service


def test_public_stats_is_the_user_count():
    client = TestClient(create_app(_service_with_hub()))
    assert client.get("/stats").json() == {"users": 1}


def test_admin_stats_off_without_a_token():
    assert TestClient(create_app(_service_with_hub())).get("/admin/stats").status_code == 404


def test_admin_stats_needs_the_token():
    client = TestClient(create_app(_service_with_hub("test-admin-token")))
    assert client.get("/admin/stats").status_code == 401
    bad = client.get("/admin/stats", headers={"Authorization": "Bearer nope"})
    assert bad.status_code == 401
    r = client.get("/admin/stats", headers={"Authorization": "Bearer test-admin-token"})
    assert r.status_code == 200
    hub = r.json()["hub"]
    assert hub["users"] == 1 and hub["totals"]["logins"] == 1
    assert hub["clients"] == {"test": 1} and hub["connections"] == {"nmdc": 1}


def test_public_stats_shows_the_nmdcs_keyprint():
    service = _service_with_hub()
    service.hub.set_keyprint("SHA256/" + "A" * 52)
    body = TestClient(create_app(service)).get("/stats").json()
    assert body["nmdcs"]["keyprint"] == "SHA256/" + "A" * 52


def test_admin_hublists_and_register_now():
    import asyncio

    from hublist_stub import StubList
    from nmdc_fakes import World
    from sublevels.nmdc import HubConfig
    from sublevels.nmdc.hublists import Publisher

    async def go():
        stub = await StubList().start()
        w = World(HubConfig(public_address="hub.example.org",
                            hublists=(f"127.0.0.1:{stub.port}", "adchub.com;method=pinger")))
        w.login("alice")
        service = Service()
        service.hub = w.hub
        service.hublists = Publisher(w.hub)
        service.admin_token = "t0ken"
        before = service.admin_hublists()
        after = await service.register_hublists()
        await asyncio.sleep(0.1)
        await stub.close()
        return service, before, after, stub.got

    service, before, after, got = asyncio.run(go())
    assert before["enabled"] and before["address"] == "hub.example.org"
    assert [t["method"] for t in before["targets"]] == ["push", "pinger"]
    assert [a["outcome"] for a in after["registered"]] == ["sent"]
    assert after["targets"][0]["last"]["outcome"] == "sent"
    assert got[0]["users"] == "1"
    client = TestClient(create_app(service))
    assert client.get("/admin/hublists").status_code == 401
    r = client.get("/admin/hublists", headers={"Authorization": "Bearer t0ken"})
    assert r.status_code == 200 and r.json()["targets"][0]["failures"] == 0
