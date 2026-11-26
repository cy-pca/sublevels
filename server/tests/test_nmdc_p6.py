"""P6: operators: roles, $Kick, $Close, $OpForceMove, the hub commands, bans, menus, the log."""

import json

import pytest

from nmdc_fakes import World, myinfo
from sublevels.nmdc import HubConfig
from sublevels.nmdc.accounts import FileAccounts, hash_password, main as accounts_main, \
    verify_password

MENUS = "NoHello NoGetINFO UserCommand HubTopic"


class Accounts:
    def __init__(self, roles):
        self.roles = roles  # nick -> role; every password is "pw"

    def is_registered(self, nick):
        return nick in self.roles

    def check(self, nick, password):
        return password == "pw"

    def role(self, nick):
        return self.roles.get(nick, "user")

    def is_operator(self, nick):
        return self.role(nick) in ("op", "admin")


def world(roles=None, **kw):
    kw.setdefault("password_requires_tls", False)  # P2 §4 is tested in test_nmdc_p2
    w = World(HubConfig(**kw), accounts=Accounts(roles or {"op": "op", "op2": "op",
                                                           "boss": "admin"}))
    w.wall = [1_800_000_000.0]
    w.hub.bans.wall = lambda: w.wall[0]
    return w


def login(w, nick, *, ip="10.0.0.1", supports="NoHello NoGetINFO"):
    s, conn = w.connect(ip)
    w.send(s, f"$Supports {supports}|", f"$Key {w.key_for(conn)}|", f"$ValidateNick {nick}|")
    if s.state == "pass":
        w.send(s, "$MyPass pw|")
    if s.state != "closed":
        w.send(s, myinfo(nick))
    return s, conn


def lines(conn):
    return [m for m in conn.take()]


def bot(conn):
    return [m[len("<sublevels> "):-1] for m in conn.take() if m.startswith("<sublevels> ")]


def test_roles_oplist_and_menus():
    w = world()
    a, ca = login(w, "alice", supports=MENUS)
    out = ca.take()
    assert "$UserCommand 255 15|" in out
    assert "$UserCommand 2 1 Rules$<%[mynick]> +rules&#124;|" in out
    assert not any("!kick" in m for m in out)
    o, co = login(w, "op", supports=MENUS)
    out = co.take()
    assert "$LogedIn op|" in out and o.role == "op"
    assert "$UserCommand 2 6 Kick$<%[mynick]> !kick %[nick] %[line:Reason]&#124;|" in out
    assert not any("Ban permanently" in m for m in out)
    assert "$OpList sublevels$$op$$|" in ca.take()
    b, cb = login(w, "boss", supports=MENUS)
    assert any("Ban permanently" in m and "perm" in m for m in cb.take())
    assert b.role == "admin" and b.is_op


def test_kick_with_the_reason_from_a_private_message():
    w = world()
    a, ca = login(w, "alice")
    o, co = login(w, "op")
    c, cc = login(w, "carol")
    for x in (ca, co, cc):
        x.take()
    w.send(o, "$To: alice From: op $<op> please stop flooding|", "$Kick alice|")
    out = ca.take()
    assert out[0] == "$To: alice From: op $<op> please stop flooding|"
    assert out[1] == "<sublevels> You were kicked by op because: please stop flooding|"
    assert ca.closed and a.close_reason == "kicked by op"
    assert "<sublevels> op is kicking alice because: please stop flooding|" in co.take()
    assert not any("is kicking" in m for m in cc.take())  # ops only, by default
    e = w.hub.actions[-1]
    assert (e["action"], e["actor"], e["target"], e["reason"]) == \
        ("kick", "op", "alice", "please stop flooding")
    assert w.hub.bans.in_force() == []  # a kick is a warning


def test_kick_default_reason_close_and_notice_to_all():
    w = world(kick_notice_all=True)
    a, ca = login(w, "alice")
    b, cb = login(w, "bob")
    o, co = login(w, "op")
    for x in (ca, cb, co):
        x.take()
    w.clock.advance(11)
    w.send(o, "$Kick alice|")
    assert "<sublevels> You were kicked by op because: kicked by op|" in ca.take()
    assert "<sublevels> op is kicking alice because: kicked by op|" in cb.take()
    w.send(o, "$Close bob|")
    assert not any("kicked" in m for m in cb.take() if "kicking" not in m)
    assert cb.closed and b.close_reason == "closed by op"


def test_only_operators_and_rank():
    w = world()
    a, ca = login(w, "alice")
    o, co = login(w, "op")
    o2, co2 = login(w, "op2")
    boss, cboss = login(w, "boss")
    for x in (ca, co, co2, cboss):
        x.take()
    w.send(a, "$Kick op|")
    assert bot(ca) == ["You are not an operator."] and not co.closed
    w.send(o, "$Kick op2|")
    assert "same as or above yours" in bot(co)[0] and not co2.closed
    w.send(o, "$Kick boss|")
    assert not cboss.closed
    w.send(o, "$Kick sublevels|")
    assert bot(co) == ["You cannot kick boss: their role is admin, the same as or above yours.",
                       "The hub bot cannot be the target."]
    w.send(boss, "$Kick op2|")
    assert co2.closed


def test_op_force_move():
    w = world()
    a, ca = login(w, "alice")
    o, co = login(w, "op")
    ca.take()
    co.take()
    w.send(o, "$OpForceMove $Who:alice$Where:not a hub!$Msg:x|")
    assert "is not a hub address" in bot(co)[0] and not ca.closed
    w.send(o, "$OpForceMove $Who:alice$Where:nmdcs://other.example:4111$Msg:moved|")
    assert ca.take() == ["<sublevels> You are being redirected to nmdcs://other.example:4111 "
                         "because: moved|", "$ForceMove nmdcs://other.example:4111|"]
    assert ca.closed and w.hub.actions[-1]["action"] == "redirect"


def test_ban_by_nick_holds_at_connect_and_at_validatenick():
    w = world()
    a, ca = login(w, "alice", ip="10.0.0.7")
    o, co = login(w, "op")
    co.take()
    w.send(o, "<op> !ban alice 1h spamming|")
    assert ca.closed and "You are banned until" in ca.messages()[-1]
    assert bot(co)[0].startswith("Ban #1 on nick:alice for 1h; closed alice")
    s, c = w.connect("10.0.0.7")  # same client id: refused before $Lock
    assert c.take() == ["<sublevels> You are banned until 2027-01-15 09:00 UTC because: "
                        "spamming|"] and c.closed
    s, c = login(w, "alice", ip="10.0.0.99")  # another address: refused at $ValidateNick
    assert c.closed and "banned" in c.messages()[-1] and "$ValidateDenide" not in c.messages()[-1]
    w.wall[0] += 3601  # expired
    s, c = login(w, "alice", ip="10.0.0.7")
    assert s.state == "online"
    assert [e["action"] for e in w.hub.actions].count("refused_ban") == 2


def test_ban_limits_by_role():
    w = world()
    o, co = login(w, "op")
    b, cb = login(w, "boss")
    co.take()
    cb.take()
    w.send(o, "<op> !ban ip:10.9.9.9 perm|", "<op> !ban ip:10.9.9.9 2d|", "<op> !ban x 5x|",
           "<op> !ban|")
    out = bot(co)
    assert out[0] == "Only an admin may do that."
    assert out[1] == "An op's bans last at most 1d."
    assert "is not a duration" in out[2] and out[3].startswith("Usage: !ban")
    w.send(b, "<boss> !ban net:10.9.0.0/16 perm abuse|")
    assert bot(cb) == ["Ban #1 on net:10.9.0.0/16 permanently."]
    s, c = w.connect("10.9.4.4")
    assert c.take() == ["<sublevels> You are banned permanently because: abuse|"]


def test_account_bans_and_repeated_attempts():
    w = world(roles={"op": "op", "boss": "admin", "reg": "user"})
    b, cb = login(w, "boss")
    w.send(b, "<boss> !ban account:reg 1d|")
    s, c = login(w, "reg", ip="10.1.1.1")
    assert c.closed and "banned" in c.messages()[-1]
    w.send(b, "<boss> !ban ip:10.2.2.2 1h|")
    seen = []
    for _ in range(5):
        s, c = w.connect("10.2.2.2")
        seen.append(c.messages())
    assert all(m for m in seen[:3]) and seen[3] == [] and seen[4] == []
    w.clock.advance(61)
    s, c = w.connect("10.2.2.2")
    assert c.messages()


def test_unban_bans_and_info():
    w = world()
    a, ca = login(w, "alice", ip="10.3.3.3")
    o, co = login(w, "op")
    o2, co2 = login(w, "op2")
    b, cb = login(w, "boss")
    for x in (co, co2, cb):
        x.take()
    w.send(o, "<op> !ban ip:10.5.5.5 1h one|", "<op> !ban ip:10.6.6.6 1h two|")
    co.take()
    w.send(o2, "<op2> !unban ip:10.5.5.5|")
    assert bot(co2) == ["An op may remove only their own bans."]
    w.send(o, "<op> !bans|")
    assert [x.split(" ")[1] for x in bot(co)] == ["ip:10.5.5.5", "ip:10.6.6.6"]
    w.send(o, "<op> !unban ip:10.5.5.5|")
    assert bot(co) == ["Removed ban #1."]
    w.send(b, "<boss> !unban #2|")
    assert bot(cb) == ["Removed ban #2."] and w.hub.bans.in_force() == []
    w.send(o, "$To: sublevels From: op $<op> !info alice|")
    out = [m for m in co.take() if "alice:" in m]
    assert len(out) == 1 and "id " + a.identity in out[0] and "10.3.3.3" in out[0]
    w.send(o, "<op> !info nobody|")
    assert bot(co) == ["nobody is not online."]


def test_help_by_role_topic_and_documents():
    w = world()
    a, ca = login(w, "alice", supports="NoHello NoGetINFO HubTopic")
    c, cc = login(w, "carol", supports="NoHello NoGetINFO")
    o, co = login(w, "op")
    b, cb = login(w, "boss")
    for x in (ca, cc, co, cb):
        x.take()
    w.send(a, "<alice> +help|")
    assert not any("!kick" in m for m in bot(ca))
    w.send(o, "<op> +help|")
    out = bot(co)
    assert any(m.startswith("!kick") for m in out) and not any("!topic" in m for m in out)
    w.send(o, "<op> !topic nope|")
    assert bot(co) == ["Only an admin may do that."]
    w.send(b, "<boss> !topic New $topic|")
    assert ca.take() == ["$HubTopic New &#36;topic|"]
    assert cc.take() == ["$HubName sublevels - New &#36;topic|"]
    assert w.hub.topic == "New $topic"
    w.send(b, "<boss> !motd set Line one\\nLine two|")
    assert w.hub.documents.lines("motd") == ["Line one", "Line two"]
    w.send(a, "<alice> +motd|")
    assert bot(ca) == ["Line one", "Line two"]


def test_kick_reconnect_block():
    w = world(kick_reconnect_block=300)
    a, ca = login(w, "alice", ip="10.4.4.4")
    o, co = login(w, "op")
    w.send(o, "$Kick alice|")
    s, c = w.connect("10.4.4.4")
    assert c.closed and "banned" in c.messages()[0]


def test_bans_and_topic_survive_a_restart(tmp_path):
    w = world(data_dir=str(tmp_path))
    b, cb = login(w, "boss")
    w.send(b, "<boss> !ban ip:10.7.7.7 perm|", "<boss> !topic kept|")
    w2 = world(data_dir=str(tmp_path))
    assert [x.target for x in w2.hub.bans.in_force()] == ["ip:10.7.7.7"]
    assert w2.hub.topic == "kept"
    assert json.loads((tmp_path / "bans.json").read_text())["next_id"] == 2


def test_role_change_resends_menus_and_oplist(tmp_path):
    path = tmp_path / "accounts.json"
    path.write_text(json.dumps({"accounts": {"dan": {"password": hash_password("pw"),
                                                     "role": "user"}}}))
    acc = FileAccounts(str(path), recheck=0)
    w = World(HubConfig(password_requires_tls=False), accounts=acc)
    d, cd = login(w, "dan", supports=MENUS)
    x, cx = login(w, "xena")
    assert d.role == "user" and d.account == "dan"
    cd.take()
    cx.take()
    import os
    path.write_text(json.dumps({"accounts": {"dan": {"password": hash_password("pw"),
                                                     "role": "op"}}}))
    os.utime(path, ns=(0, os.stat(path).st_mtime_ns + 10**9))
    acc.is_registered("dan")  # the next look at the file
    assert d.role == "op"
    out = cd.take()
    assert "$LogedIn dan|" in out and "$UserCommand 255 15|" in out
    assert any("!kick" in m for m in out)
    assert "$OpList sublevels$$dan$$|" in cx.take()


def test_accounts_file_and_cli(tmp_path, monkeypatch, capsys):
    import io
    path = str(tmp_path / "accounts.json")
    monkeypatch.setattr("sys.stdin", io.StringIO("s3cret\n"))
    assert accounts_main([path, "add", "Eve", "--role", "op"]) == 0
    assert accounts_main([path, "role", "eve", "admin"]) == 0
    acc = FileAccounts(path)
    assert acc.is_registered("EVE") and acc.role("eve") == "admin" and acc.is_operator("eve")
    assert acc.check("eve", "s3cret") and not acc.check("eve", "wrong")
    assert "s3cret" not in open(path).read()
    assert oct(__import__("os").stat(path).st_mode & 0o777) == "0o600"
    assert accounts_main([path, "list"]) == 0 and "Eve\tadmin" in capsys.readouterr().out
    assert accounts_main([path, "remove", "eve"]) == 0
    assert not FileAccounts(path).is_registered("eve")
    assert verify_password("x", hash_password("x")) and not verify_password("x", "plain")


def test_native_operator_calls_and_the_admin_api(tmp_path):
    pytest.importorskip("sqlalchemy")
    from fastapi.testclient import TestClient

    from sublevels.api import create_app
    from sublevels.nmdc.connlog import ConnLog
    from sublevels.service import Service

    w = world()
    w.hub.connlog = ConnLog(f"sqlite:///{tmp_path / 'c.sqlite3'}")
    a, ca = login(w, "alice")
    service = Service()
    service.hub = w.hub
    service.admin_token = "t0ken"
    principal = {"id": "p1", "name": "Joe", "roles": ["admin"]}
    assert service.operator_call(principal, "info", "alice")["online"]
    assert service.operator_call(principal, "kick", "nobody", "")["ok"] is False
    assert service.operator_call(principal, "kick", "alice", "native kick")["ok"]
    assert ca.closed and "kicked by Joe because: native kick" in ca.messages()[-1]
    r = service.operator_call(principal, "ban", "ip:10.8.8.8", "perm", "x")
    assert r["ok"] and r["ban"]["permanent"]
    c = TestClient(create_app(service))
    auth = {"Authorization": "Bearer t0ken"}
    assert c.get("/admin/bans").status_code == 401
    assert [b["target"] for b in c.get("/admin/bans", headers=auth).json()["bans"]] == \
        ["ip:10.8.8.8"]
    r = c.post("/admin/bans", json={"target": "net:10.10.0.0/16", "duration": "1d"},
               headers=auth)
    assert r.status_code == 200 and r.json()["ban"]["by"] == "admin API"
    assert c.post("/admin/bans", json={"target": "ip:x", "duration": "1d"},
                  headers=auth).status_code == 422
    assert c.delete("/admin/bans/1", headers=auth).json()["removed"] == [1]
    assert c.delete("/admin/bans/99", headers=auth).status_code == 404
    log = c.get("/admin/oplog", headers=auth).json()
    assert [e["action"] for e in log["recent"]][:3] == ["unban", "ban", "ban"]
    w.hub.connlog.flush()
    stored = ConnLog(w.hub.connlog.url).query_actions(action="kick")
    assert stored[0]["actor"] == "Joe" and stored[0]["target"] == "alice"
