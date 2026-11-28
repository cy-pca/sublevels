"""IDENTITY.md 3.8-3.9 on the hub: guests on or off, bans that cover an id's addresses, the ids seen
at the same addresses, and the salt in a file of its own."""

import pytest

from nmdc_fakes import World, myinfo
from sublevels.nmdc import HubConfig


class Accounts:
    def is_registered(self, nick):
        return nick in ("member", "boss")

    def check(self, nick, password):
        return password == "pw"

    def role(self, nick):
        return {"boss": "admin"}.get(nick, "user")

    def is_operator(self, nick):
        return nick == "boss"


def attempt(w, nick, ip="10.0.0.1", supports="NoHello NoGetINFO"):
    s, conn = w.connect(ip)
    w.send(s, f"$Supports {supports}|", f"$Key {w.key_for(conn)}|", f"$ValidateNick {nick}|")
    if s.state == "pass":
        w.send(s, "$MyPass pw|")
    if s.state != "closed":
        w.send(s, myinfo(nick, share=0))
    return s, conn


def test_guests_off():
    w = World(HubConfig(allow_guests=False, password_requires_tls=False), accounts=Accounts())
    s, conn = attempt(w, "visitor")
    assert conn.closed and conn.messages()[-1] == "<sublevels> This hub is for registered users only.|"
    assert w.hub.counters.snapshot()["refusals"]["guests_off"] == 1
    s, conn = attempt(w, "member")
    assert s.state == "online"
    s, conn = attempt(w, "lister", supports="NoHello BotINFO")  # a hub list's pinger
    assert s.state == "online" and s.pinger


def test_guests_on_by_default():
    s, conn = attempt(World(), "visitor")
    assert s.state == "online"


def test_an_id_ban_covers_the_addresses_it_was_seen_at(tmp_path):
    pytest.importorskip("sqlalchemy")
    from sublevels.nmdc.connlog import ConnLog

    w = World(HubConfig(password_requires_tls=False), accounts=Accounts())
    w.hub.connlog = ConnLog(f"sqlite:///{tmp_path / 'c.sqlite3'}")
    g, _ = attempt(w, "guesty", ip="10.5.0.1")
    gid = g.identity
    w.hub.disconnect(g)
    w.hub.connlog.flush()
    w.hub.connlog = ConnLog(w.hub.connlog.url)
    b, cb = attempt(w, "boss", ip="10.9.9.9")
    w.send(b, f"<boss> !ban id:{gid} 1h repeat guest|")
    ban = w.hub.bans.in_force()[0]
    assert ban.extra["addresses"] == ["10.5.0.1"]
    s, c = w.connect("10.5.0.1")  # the same address: the same id, refused
    assert c.closed
    w.hub.connlog.flush()


def test_a_nick_ban_covers_its_users_address():
    w = World(HubConfig(password_requires_tls=False), accounts=Accounts())
    v, cv = attempt(w, "visitor", ip="10.6.0.1")
    b, cb = attempt(w, "boss", ip="10.9.9.9")
    w.send(b, "<boss> !ban visitor 1h|")
    assert cv.closed
    assert w.hub.bans.in_force()[0].extra["addresses"] == ["10.6.0.1"]
    s, c = attempt(w, "newname", ip="10.6.0.1")  # a new nick from the same address
    assert c.closed and "banned" in c.messages()[-1]


def test_related_ids(tmp_path):
    pytest.importorskip("sqlalchemy")
    from fastapi.testclient import TestClient

    from sublevels.api import create_app
    from sublevels.nmdc.connlog import ConnLog
    from sublevels.service import Service

    w = World()
    w.hub.connlog = ConnLog(f"sqlite:///{tmp_path / 'c.sqlite3'}")
    a, _ = attempt(w, "alpha", ip="10.7.0.1")
    a_id = a.identity
    w.hub.disconnect(a)
    # Another id at that address: a stand-in for a native client on the same machine.
    w.hub.connlog.record({"hub": "main", "opened_at": 1.0, "closed_at": 2.0, "nick": "native",
                          "client_id": "deadbeefdeadbeef", "address": "10.7.0.1"})
    w.hub.connlog.flush()
    w.hub.connlog = ConnLog(w.hub.connlog.url)
    r = w.hub.related(a_id)
    assert r["addresses"] == ["10.7.0.1"] and r["others"] == {"deadbeefdeadbeef": ["10.7.0.1"]}
    service = Service()
    service.hub = w.hub
    service.admin_token = "t0ken"
    c = TestClient(create_app(service))
    got = c.get(f"/admin/connections/related?client_id={a_id}",
                headers={"Authorization": "Bearer t0ken"}).json()
    assert got["others"] == {"deadbeefdeadbeef": ["10.7.0.1"]}
    w.hub.connlog.flush()


def test_the_salt_file(tmp_path):
    path = tmp_path / "shared" / "identity_salt"
    a = World(HubConfig(identity_salt_file=str(path))).hub
    b = World(HubConfig(identity_salt_file=str(path), data_dir=str(tmp_path / "other"))).hub
    assert path.exists() and a.salt == b.salt == path.read_text().strip()
    assert HubConfig.from_env({"SUBLEVELS_IDENTITY_SALT_FILE": "/x/salt",
                               "SUBLEVELS_ALLOW_GUESTS": "0"}).allow_guests is False
