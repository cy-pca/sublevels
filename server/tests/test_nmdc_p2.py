"""P2: login, nicks, passwords, $MyINFO and the user list."""

from nmdc_fakes import ALL, World, myinfo
from sublevels import __version__
from sublevels.nmdc import HubConfig
from sublevels.nmdc.myinfo import parse


def test_on_connect_lock_then_hub_name():
    w = World(HubConfig(name="Test Hub"))
    s, conn = w.connect()
    first, second = conn.take()
    assert first.startswith("$Lock EXTENDEDPROTOCOL_sublevels_")
    assert first.endswith(f" Pk=sublevels{__version__}|")
    assert second == "$HubName Test Hub|"


def test_full_login_sequence():
    w = World(HubConfig(topic="testing"))
    s, conn = w.connect()
    conn.take()
    w.send(s, f"$Supports {ALL} |", f"$Key {w.key_for(conn)}|", "$ValidateNick alice|")
    assert conn.take() == ["$Supports NoGetINFO NoHello UserIP2 BotList HubTopic HubINFO|",
                           "$Hello alice|"]
    assert s.state == "info" and s.supports >= {"NoHello", "HubTopic"}
    w.send(s, "$Version 1,0091|", "$GetNickList|", myinfo("alice"))
    assert s.state == "online"
    out = conn.take()
    assert out[0].startswith("$MyINFO $ALL sublevels ")  # the bot first
    assert out[1] == myinfo("alice")  # the newcomer's own, verbatim
    assert out[2] == "$OpList sublevels$$|"
    assert out[3] == "$BotList sublevels$$|"
    assert out[4] == "$UserIP alice 10.0.0.1|"
    assert out[5] == "$HubTopic testing|"
    assert out[6].startswith("<sublevels> ")  # the message of the day
    assert "+rules" in out[-1]


def test_others_see_hello_and_myinfo():
    w = World()
    a, ca = w.login("alice", supports="UserIP2")  # no NoHello
    b, cb = w.login("bob", supports="NoHello NoGetINFO")
    ca.take()
    cb.take()
    c, cc = w.login("carol")
    assert ca.take() == ["$Hello carol|", myinfo("carol")]
    assert cb.take() == [myinfo("carol")]


def test_nicklist_for_clients_without_nohello():
    w = World()
    w.login("alice")
    s, conn = w.login("bob", supports="")
    out = conn.take()
    assert "$NickList sublevels$$alice$$bob$$|" in out
    assert not any(m.startswith("$MyINFO $ALL alice") for m in out)
    w.send(s, "$GetINFO alice bob|")
    assert conn.take() == [myinfo("alice")]
    w.send(s, "$GetINFO sublevels bob|")
    assert conn.take()[0].startswith("$MyINFO $ALL sublevels ")


def test_nogetinfo_without_nohello_gets_list_and_infos():
    w = World()
    w.login("alice")
    s, conn = w.login("bob", supports="NoGetINFO")
    out = conn.take()
    assert "$NickList sublevels$$alice$$bob$$|" in out
    assert myinfo("alice") in out


def test_topic_in_hub_name_without_hubtopic():
    w = World(HubConfig(name="H", topic="t"))
    s, conn = w.login("alice", supports="NoHello")
    assert "$HubName H - t|" in conn.take()


def test_nick_rules():
    hub = World().hub
    assert hub.nick_problem("alice") == ""
    assert hub.nick_problem("") == "length" and hub.nick_problem("x" * 65) == "length"
    for bad in ("a b", "a$b", "a|b", "<a", "a>", "a\x01b", "a\x85b"):
        assert hub.nick_problem(bad) == "characters", bad
    assert hub.nick_problem(".a") == "dots" and hub.nick_problem("a.") == "dots"
    assert hub.nick_problem("SubLevels") == "reserved"
    assert World(HubConfig(reserved_nicks=("Admin",))).hub.nick_problem("admin") == "reserved"


def test_taken_nick_case_insensitive():
    w = World()
    w.login("Alice")
    s, conn = w.connect()
    w.send(s, "$Key x|", "$ValidateNick aLICE|")
    assert conn.take()[-1] == "$ValidateDenide aLICE|"
    assert conn.closed
    assert w.hub.counters.refusals["nick_taken"] == 1


def test_validate_nick_before_key_is_ignored():
    w = World()
    s, conn = w.connect()
    conn.take()
    w.send(s, "$ValidateNick alice|")
    assert conn.take() == [] and s.state == "lock"


def test_myinfo_for_another_nick_closes_during_login():
    w = World()
    s, conn = w.connect()
    w.send(s, "$Key x|", "$ValidateNick alice|", myinfo("bob"))
    assert conn.closed


def test_login_timeout():
    w = World()
    s, conn = w.connect()
    w.clock.advance(29)
    assert not conn.closed
    w.clock.advance(2)
    assert conn.closed and w.hub.counters.refusals["login_timeout"] == 1
    s2, conn2 = w.login("bob")
    w.clock.advance(60)
    assert not conn2.closed


def test_capacity():
    w = World(HubConfig(max_users=1))
    w.login("alice")
    s, conn = w.connect()
    out = conn.take()
    assert out[0].startswith("<sublevels> ") and out[1] == "$HubIsFull|"
    assert conn.closed


def test_quit_on_leave():
    w = World()
    a, ca = w.login("alice")
    b, cb = w.login("bob")
    cb.take()
    w.hub.disconnect(a)
    assert cb.take() == ["$Quit alice|"]
    assert "alice" not in w.hub.users and a.id not in w.hub.sessions


def test_myinfo_updates_rate_and_identical():
    w = World()
    a, ca = w.login("alice")
    b, cb = w.login("bob")
    cb.take()
    w.send(a, myinfo("alice"))  # byte-identical: not sent
    assert cb.take() == []
    w.send(a, myinfo("alice", share=2000))  # within 10 s of login: held
    w.send(a, myinfo("alice", share=3000))  # the newest replaces it
    assert cb.take() == []
    w.clock.advance(10)
    assert cb.take() == [myinfo("alice", share=3000)]
    w.clock.advance(11)
    w.send(a, myinfo("alice", share=4000))  # past the interval: at once
    assert cb.take() == [myinfo("alice", share=4000)]


def test_myinfo_parse():
    i = parse("$MyINFO $ALL bob hello <ncdc V:1.24,M:P,H:1/2/3,S:5>$ $Cable\x05$b@x$12345$")
    assert (i.nick, i.description, i.client, i.mode, i.slots, i.hubs) == \
        ("bob", "hello ", "ncdc", "P", 5, "1/2/3")
    assert (i.connection, i.flag, i.email, i.share) == ("Cable", 5, "b@x", 12345)
    plain = parse("$MyINFO $ALL bob $ $\x01$$0$")
    assert plain.mode == "?" and plain.tag == "" and plain.share == 0
    assert parse("$MyINFO $ALL bob only$two$") is None
    v6 = parse("$MyINFO $ALL bob <c V:1,M:AP,H:1/0/0,S:1>$ $x\x01$$1$", ipv6=True)
    assert v6.mode == "P"


def test_myinfo_bad_shape_is_dropped():
    w = World()
    s, conn = w.connect()
    w.send(s, "$Key x|", "$ValidateNick alice|", "$MyINFO $ALL alice nope|")
    assert s.state == "info" and not conn.closed


class Accounts:
    def __init__(self, registered):
        self.registered = registered

    def is_registered(self, nick):
        return nick in self.registered

    def check(self, nick, password):
        return self.registered.get(nick) == password

    def is_operator(self, nick):
        return nick == "op"


def test_registered_nick_needs_tls_by_default():
    w = World(accounts=Accounts({"alice": "pw"}))
    s, conn = w.connect()
    w.send(s, "$Key x|", "$ValidateNick alice|")
    assert conn.closed and "TLS" in conn.messages()[-1]


def test_password_login_and_bad_password():
    w = World(accounts=Accounts({"alice": "pw", "op": "pw2"}))
    s, conn = w.connect(tls=True)
    w.send(s, "$Key x|", "$ValidateNick alice|")
    assert conn.take()[-1] == "$GetPass|"
    w.send(s, "$MyPass nope|")
    assert conn.take()[-1] == "$BadPass|" and conn.closed
    s2, conn2 = w.connect(tls=True)
    w.send(s2, "$Key x|", "$ValidateNick op|", "$MyPass pw2|")
    assert conn2.take()[-1] == "$Hello op|"
    w.send(s2, myinfo("op"))
    out = conn2.take()
    assert out[0] == "$LogedIn op|" and "$OpList sublevels$$op$$|" in out


def test_bad_password_rate():
    w = World(accounts=Accounts({"alice": "pw"}))
    for _ in range(5):
        s, conn = w.connect(tls=True)
        w.send(s, "$Key x|", "$ValidateNick alice|", "$MyPass nope|")
    s, conn = w.connect(tls=True)
    w.send(s, "$Key x|", "$ValidateNick alice|")
    assert conn.closed and "$GetPass|" not in conn.messages()
    assert w.hub.counters.refusals["bad_password_rate"] == 1


def test_registered_user_reconnecting_takes_the_nick_over():
    w = World(accounts=Accounts({"alice": "pw"}))
    s1, c1 = w.connect(tls=True)
    w.send(s1, "$Key x|", "$ValidateNick alice|", "$MyPass pw|", myinfo("alice"))
    assert s1.online
    s2, c2 = w.connect(tls=True)
    w.send(s2, "$Key x|", "$ValidateNick alice|", "$MyPass pw|", myinfo("alice"))
    assert c1.closed and s2.online and w.hub.users["alice"] is s2


def test_tls_required():
    w = World(HubConfig(tls_required=True, nmdcs_address="nmdcs://hub.example:4111"))
    s, conn = w.connect()
    out = conn.take()
    assert out[-1] == "$ForceMove nmdcs://hub.example:4111|" and conn.closed
    s2, conn2 = w.connect(tls=True)
    assert conn2.take()[0].startswith("$Lock ")
