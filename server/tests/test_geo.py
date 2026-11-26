"""D11-D13: geolocation, the location policy, the [CC] prefix and the connection log."""

import os

import pytest

import mmdb_writer
from nmdc_fakes import World, myinfo
from sublevels.nmdc import HubConfig
from sublevels.nmdc.geo import ATTRIBUTION, UNKNOWN, GeoDB, Location, LocationPolicy, lookup

NETS = {"81.2.69.0/24": mmdb_writer.city("GB", "London", "England"),
        "2.16.0.0/16": mmdb_writer.city("DE", "Berlin"),
        "2a00:1450::/32": mmdb_writer.city("US", "Mountain View", "California")}


class FakeReader:
    def __init__(self, table):
        self.table = table

    def get(self, ip):
        return self.table.get(ip)


class FakeGeo:
    """GeoDB's interface over a fixed table: ip -> Location."""

    def __init__(self, table):
        self.table = table
        self.enabled = True

    def lookup(self, ip):
        return self.table.get(ip, UNKNOWN)

    def status(self):
        return {"enabled": True, "attribution": ATTRIBUTION}


def geo_world(table=None, **kw):
    w = World(HubConfig(**kw))
    w.hub.geo = FakeGeo(table or {"81.2.69.1": Location("GB", "England", "London"),
                                  "2.16.0.1": Location("DE", "", "Berlin")})
    return w


def test_lookup_is_a_plain_function():
    r = FakeReader({"81.2.69.1": mmdb_writer.city("GB", "London", "England")})
    assert lookup(r, "81.2.69.1") == Location("GB", "England", "London")
    assert lookup(r, "81.2.69.2") is UNKNOWN
    for private in ("10.0.0.1", "192.168.1.1", "127.0.0.1", "::1", "fe80::1", "not an ip"):
        assert lookup(r, private) is UNKNOWN
    assert lookup(None, "81.2.69.1") is UNKNOWN


def test_geodb_reads_a_real_mmdb_and_reloads_it(tmp_path):
    pytest.importorskip("maxminddb")
    path = str(tmp_path / "GeoLite2-City.mmdb")
    mmdb_writer.write(path, NETS)
    now = [0.0]
    db = GeoDB(path, clock=lambda: now[0])
    assert db.lookup("81.2.69.160") == Location("GB", "England", "London")
    assert db.lookup("2a00:1450:4001::1") == Location("US", "California", "Mountain View")
    assert db.lookup("8.8.8.8") is UNKNOWN
    # Replaced as geoipupdate does: written beside it, then renamed over it.
    mmdb_writer.write(path + ".new", {"81.2.69.0/24": mmdb_writer.city("IE", "Dublin")})
    os.utime(path + ".new", ns=(0, os.stat(path).st_mtime_ns + 10**9))
    os.replace(path + ".new", path)
    assert db.lookup("81.2.69.160").country == "GB"  # not looked at again yet
    now[0] = 31.0
    assert db.lookup("81.2.69.160") == Location("IE", "", "Dublin")
    assert db.status()["loaded"] and db.status()["attribution"] == ATTRIBUTION


def test_geodb_without_a_file_or_with_a_damaged_one(tmp_path):
    db = GeoDB(str(tmp_path / "missing.mmdb"), clock=lambda: 0.0)
    assert db.lookup("81.2.69.160") is UNKNOWN and db.status()["error"]
    bad = tmp_path / "bad.mmdb"
    bad.write_bytes(b"not a database")
    db = GeoDB(str(bad), clock=lambda: 0.0)
    assert db.lookup("81.2.69.160") is UNKNOWN and not db.status()["loaded"]
    assert not GeoDB("", clock=lambda: 0.0).enabled


def test_policy():
    gb, unknown = Location("GB"), UNKNOWN
    deny = LocationPolicy("deny", frozenset({"GB"}))
    assert "GB" in deny.refuses("81.2.69.1", gb) and not deny.refuses("2.16.0.1", Location("DE"))
    allow = LocationPolicy("allow", frozenset({"DE"}))
    assert allow.refuses("81.2.69.1", gb) and not allow.refuses("2.16.0.1", Location("DE"))
    assert not allow.refuses("10.0.0.1", unknown)  # unknown: admitted by default
    assert LocationPolicy("allow", frozenset({"DE"}), unknown="refuse").refuses("10.0.0.1",
                                                                               unknown)
    import ipaddress
    exempt = LocationPolicy("deny", frozenset({"GB"}), (ipaddress.ip_network("81.2.69.0/24"),))
    assert not exempt.refuses("81.2.69.1", gb)
    assert not LocationPolicy().refuses("81.2.69.1", gb)
    with pytest.raises(ValueError):
        LocationPolicy.from_config(HubConfig(location_policy="block"))


def test_refused_before_lock_with_one_line():
    w = geo_world(location_policy="deny", location_countries=("GB",))
    s, conn = w.connect("81.2.69.1")
    assert conn.take() == ["<sublevels> This hub does not take connections from your country "
                           "(GB).|"]
    assert conn.closed and s.close_reason == "location policy (GB)"
    assert w.hub.counters.snapshot()["refusals"]["location"] == 1
    s, conn = w.connect("2.16.0.1")
    assert conn.take()[0].startswith("$Lock ")


def test_country_prefix_in_myinfo():
    w = geo_world(country_prefix=True)
    a, ca = w.login("alice", ip="10.0.0.1")
    ca.take()
    b, cb = w.login("bob", ip="81.2.69.1", description="hi")
    assert ca.take() == [myinfo("bob", description="[GB] hi")]
    w.clock.advance(120)
    w.send(b, myinfo("bob", description="hi again"))
    assert ca.take() == [myinfo("bob", description="[GB] hi again")]
    c, cc = w.login("carol", ip="10.0.0.2")  # unknown: no prefix
    assert ca.take() == [myinfo("carol")]
    assert w.hub.stats()["countries"] == {"?": 2, "GB": 1}


def test_no_prefix_unless_enabled():
    w = geo_world()
    a, ca = w.login("alice")
    ca.take()
    w.login("bob", ip="81.2.69.1")
    assert ca.take() == [myinfo("bob")]


def test_connection_log(tmp_path):
    pytest.importorskip("sqlalchemy")
    from sublevels.nmdc.connlog import ConnLog

    class Accounts:
        def is_registered(self, nick):
            return nick == "op"

        def check(self, nick, password):
            return password == "pw"

        def is_operator(self, nick):
            return nick == "op"

    log = ConnLog(f"sqlite:///{tmp_path / 'c.sqlite3'}")
    w = World(HubConfig(location_policy="deny", location_countries=("DE",)), accounts=Accounts())
    w.hub.geo = FakeGeo({"81.2.69.1": Location("GB", "England", "London"),
                         "2.16.0.1": Location("DE")})
    w.hub.connlog = log
    a, ca = w.login("alice", ip="81.2.69.1", client="ncdc")
    o, co = w.connect("10.0.0.5", tls=True)
    w.send(o, "$Supports NoHello|", f"$Key {w.key_for(co)}|", "$ValidateNick op|",
           "$MyPass pw|", myinfo("op"))
    p, cp = w.login("pinger", supports="NoHello BotINFO", share=0)
    d, cd = w.connect("2.16.0.1")
    live = w.hub.connections()["live"]
    assert {r["nick"] for r in live} == {"alice", "op", "pinger"}
    for s, why in ((a, "closed by the client"), (o, "closed by the client"),
                   (p, "pinger time limit")):
        w.hub.disconnect(s, why)
    log.flush()
    log2 = ConnLog(log.url)
    rows = log2.query()
    by = {r["nick"]: r for r in rows}
    assert by["alice"]["country"] == "GB" and by["alice"]["city"] == "London"
    assert by["alice"]["client"] == "ncdc" and by["alice"]["version"] == "1.0"
    assert by["alice"]["client_id"] == w.hub.identity_of("81.2.69.1")
    assert by["alice"]["bytes_out"] > 0
    assert by["op"]["account"] == "op" and by["op"]["tls"] and by["op"]["protocol"] == "nmdcs"
    assert by["pinger"]["pinger"] and by["pinger"]["close_reason"] == "pinger time limit"
    assert by[""]["close_reason"] == "location policy (DE)" and by[""]["address"] == "2.16.0.1"
    assert [r["nick"] for r in log2.query(country="gb")] == ["alice"]
    assert [r["nick"] for r in log2.query(nick="ALICE")] == ["alice"]
    assert all(r["closed_at"] >= r["opened_at"] for r in rows)
    log2.flush()


def test_salt_kept_in_the_data_dir(tmp_path):
    a = World(HubConfig(data_dir=str(tmp_path))).hub
    b = World(HubConfig(data_dir=str(tmp_path))).hub
    assert a.salt == b.salt and a.identity_of("81.2.69.1") == b.identity_of("81.2.69.1")
    assert oct(os.stat(tmp_path / "identity_salt").st_mode & 0o777) == "0o600"
    assert World().hub.salt != World().hub.salt  # without one: per run


def test_admin_connections_api(tmp_path):
    pytest.importorskip("sqlalchemy")
    from fastapi.testclient import TestClient

    from sublevels.api import create_app
    from sublevels.nmdc.connlog import ConnLog
    from sublevels.service import Service

    w = geo_world()
    w.hub.connlog = ConnLog(f"sqlite:///{tmp_path / 'c.sqlite3'}")
    a, _ = w.login("alice", ip="81.2.69.1")
    b, _ = w.login("bob", ip="2.16.0.1")
    w.hub.disconnect(b)
    w.hub.connlog.flush()
    w.hub.connlog = ConnLog(w.hub.connlog.url)
    service = Service()
    service.hub = w.hub
    service.admin_token = "t0ken"
    c = TestClient(create_app(service))
    assert c.get("/admin/connections").status_code == 401
    r = c.get("/admin/connections", headers={"Authorization": "Bearer t0ken"}).json()
    assert [x["nick"] for x in r["live"]] == ["alice"]
    assert [x["nick"] for x in r["stored"]] == ["bob"] and r["attribution"] == ATTRIBUTION
    r = c.get("/admin/connections?country=DE", headers={"Authorization": "Bearer t0ken"}).json()
    assert r["live"] == [] and [x["nick"] for x in r["stored"]] == ["bob"]
    w.hub.connlog.flush()


def test_help_shows_the_attribution():
    w = geo_world()
    s, conn = w.login("alice")
    conn.take()
    w.send(s, "<alice> +help|")
    assert conn.take()[-1] == f"<sublevels> {ATTRIBUTION}|"
