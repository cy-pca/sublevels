"""D12: the location policy holds, and only exempt accounts, with their passwords, get past it."""

import ipaddress
import json

import pytest

import mmdb_writer
from nmdc_fakes import World, myinfo
from sublevels.nmdc import HubConfig
from sublevels.nmdc.accounts import FileAccounts, hash_password
from sublevels.nmdc.geo import UNKNOWN, Location, LocationPolicy, lookup
from test_geo import FakeGeo, FakeReader

DENIED = "81.2.69.1"  # GB, on the denylist below


class Accounts:
    def __init__(self, roles):
        self.roles = roles  # every password is "pw"

    def is_registered(self, nick):
        return nick in self.roles

    def check(self, nick, password):
        return password == "pw"

    def role(self, nick):
        return self.roles.get(nick, "user")

    def is_operator(self, nick):
        return self.role(nick) in ("op", "admin")


def world(roles=None, accounts=None, **kw):
    kw.setdefault("location_policy", "deny")
    kw.setdefault("location_countries", ("GB",))
    kw.setdefault("geoip_db", "/nonexistent/GeoLite2-City.mmdb")
    kw.setdefault("password_requires_tls", False)
    w = World(HubConfig(**kw), accounts=accounts or Accounts(
        roles if roles is not None else {"op": "op", "boss": "admin", "member": "user"}))
    w.hub.geo = FakeGeo({DENIED: Location("GB", "England", "London")})
    return w


def attempt(w, nick, password="pw", ip=DENIED, tls=False):
    s, conn = w.connect(ip, tls)
    w.send(s, "$Supports NoHello NoGetINFO|", f"$Key {w.key_for(conn)}|", f"$ValidateNick {nick}|")
    if s.state == "pass":
        w.send(s, f"$MyPass {password}|")
    if s.state != "closed":
        w.send(s, myinfo(nick))
    return s, conn


REFUSAL = "<sublevels> This hub does not take connections from your country (GB).|"


def test_without_accounts_refused_before_lock():
    w = world(roles={}, accounts=None)
    w.hub.accounts = __import__("sublevels.nmdc.hub", fromlist=["NoAccounts"]).NoAccounts()
    s, conn = w.connect(DENIED)
    assert conn.take() == [REFUSAL] and conn.closed


def test_operators_log_in_with_their_password():
    w = world()
    for nick in ("op", "boss"):
        s, conn = attempt(w, nick)
        assert s.state == "online" and s.role in ("op", "admin")
        assert REFUSAL not in conn.messages() and "$GetPass|" in conn.messages()
    e = [a for a in w.hub.actions if a["action"] == "location_exempt"]
    assert [x["target"] for x in e] == ["op", "boss"] and e[0]["extra"]["country"] == "GB"
    assert w.hub.counters.snapshot()["totals"]["location_exempted"] == 2


def test_a_wrong_password_gets_nothing():
    w = world()
    s, conn = attempt(w, "op", password="nope")
    assert conn.closed and s.state == "closed" and "$BadPass|" in conn.messages()
    assert "op" not in w.hub.users
    assert not any(m.startswith("$MyINFO") or m.startswith("$NickList") for m in conn.messages())


def test_everyone_else_is_refused_at_validatenick():
    w = world()
    a, ca = attempt(w, "alice")  # no account
    m, cm = attempt(w, "member")  # an account, but not exempt
    for s, conn in ((a, ca), (m, cm)):
        out = conn.messages()
        assert out[-1] == REFUSAL and conn.closed and s.close_reason == "location policy (GB)"
        assert "$GetPass|" not in out and not any("$ValidateDenide" in x for x in out)
        assert not any(x.startswith("$Supports") for x in out)  # never got past the nick
    assert not w.hub.users
    assert [x["target"] for x in w.hub.actions if x["action"] == "refused_location"] == \
        ["alice", "member"]


def test_a_held_connection_cannot_skip_the_nick():
    w = world()
    s, conn = w.connect(DENIED)
    conn.take()
    w.send(s, "$Supports NoHello|", f"$Key {w.key_for(conn)}|", myinfo("op"), "<op> hi|",
           "$GetNickList|", "$MyPass pw|")
    assert s.state == "nick" and conn.take() == [] and not w.hub.users
    w.clock.advance(w.hub.limits.login_timeout + 1)
    assert conn.closed and s.close_reason == "login timeout"


def test_pingers_from_a_refused_location_are_refused():
    w = world()
    s, conn = w.connect(DENIED)
    w.send(s, "$Supports NoHello BotINFO|", f"$Key {w.key_for(conn)}|", "$ValidateNick lister|")
    assert conn.closed and conn.messages()[-1] == REFUSAL


def test_exempt_by_name_by_flag_and_by_role_setting(tmp_path):
    w = world(location_exempt_accounts=("Member",))
    s, _ = attempt(w, "member")
    assert s.state == "online"
    w = world(location_exempt_roles=("admin",))
    s, conn = attempt(w, "op")
    assert conn.closed and conn.messages()[-1] == REFUSAL
    s, _ = attempt(w, "boss")
    assert s.state == "online"
    path = tmp_path / "accounts.json"
    path.write_text(json.dumps({"accounts": {
        "traveller": {"password": hash_password("pw"), "role": "user", "location_exempt": True},
        "homebody": {"password": hash_password("pw"), "role": "user"}}}))
    w = world(accounts=FileAccounts(str(path)))
    s, _ = attempt(w, "traveller")
    assert s.state == "online"
    s, conn = attempt(w, "homebody")
    assert conn.closed and conn.messages()[-1] == REFUSAL


def test_passwords_in_the_clear_rule_still_holds():
    w = world(password_requires_tls=True)
    s, conn = attempt(w, "op")
    assert conn.closed and s.state == "closed" and "op" not in w.hub.users
    s, conn = attempt(w, "op", tls=True)
    assert s.state == "online"


def test_admitted_addresses_are_untouched():
    w = world()
    s, conn = attempt(w, "alice", ip="2.16.0.1")  # not placed: admitted by default
    assert s.state == "online" and s.location_refusal == ""


def test_configuration_errors():
    def cfg(**kw):
        kw.setdefault("location_policy", "deny")
        kw.setdefault("location_countries", ("GB",))
        kw.setdefault("geoip_db", "/x.mmdb")
        return LocationPolicy.from_config(HubConfig(**kw))

    assert cfg().on
    for bad, what in ((dict(geoip_db=""), "SUBLEVELS_GEOIP_DB"),
                      (dict(location_countries=()), "SUBLEVELS_LOCATION_COUNTRIES"),
                      (dict(location_countries=("GBR",)), "two-letter"),
                      (dict(location_exempt_roles=("owner",)), "not roles"),
                      (dict(location_exempt=("10.0.0.0/33",)), "SUBLEVELS_LOCATION_EXEMPT"),
                      (dict(location_unknown="maybe"), "admit or refuse")):
        with pytest.raises(ValueError, match=what):
            cfg(**bad)
    with pytest.raises(ValueError):
        World(HubConfig(location_policy="allow", location_countries=("DE",)))
    assert not LocationPolicy.from_config(HubConfig()).on  # off needs nothing


def test_ipv4_mapped_addresses():
    r = FakeReader({DENIED: mmdb_writer.city("GB")})
    assert lookup(r, "::ffff:" + DENIED) == Location("GB")
    assert lookup(r, "::ffff:10.0.0.1") is UNKNOWN
    p = LocationPolicy("deny", frozenset({"GB"}), (ipaddress.ip_network("81.2.69.0/24"),))
    assert p.refuses("::ffff:" + DENIED, Location("GB")) == ""


def test_the_server_does_not_start_without_its_database(tmp_path):
    from sublevels.nmdc import Hub

    hub = Hub(HubConfig(location_policy="deny", location_countries=("GB",),
                        geoip_db=str(tmp_path / "missing.mmdb")))
    with pytest.raises(ValueError, match="did not load"):
        hub.check_ready()
    Hub(HubConfig()).check_ready()  # no policy: nothing to load


def test_a_database_that_goes_bad_keeps_the_old_one(tmp_path):
    pytest.importorskip("maxminddb")
    import os

    from sublevels.nmdc.geo import GeoDB

    path = str(tmp_path / "GeoLite2-City.mmdb")
    mmdb_writer.write(path, {"81.2.69.0/24": mmdb_writer.city("GB")})
    now = [0.0]
    db = GeoDB(path, clock=lambda: now[0])
    (tmp_path / "bad").write_bytes(b"truncated")
    os.utime(tmp_path / "bad", ns=(0, os.stat(path).st_mtime_ns + 10**9))
    os.replace(tmp_path / "bad", path)
    now[0] = 31.0
    assert db.lookup(DENIED) == Location("GB") and db.status()["error"]
    os.remove(path)
    now[0] = 62.0
    assert db.lookup(DENIED) == Location("GB")
