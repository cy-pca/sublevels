"""P9 §2: hub-list pingers, and $HubINFO."""

from nmdc_fakes import World, myinfo
from sublevels import __version__
from sublevels.nmdc import HubConfig

PINGER = "NoHello NoGetINFO BotINFO"


def world(**kw):
    kw.setdefault("public_address", "hub.example.org")
    return World(HubConfig(name="Test Hub", topic="the $topic", owner="op@example.org",
                           max_users=500, min_share=1024, min_slots=2, max_hubs=10, **kw))


def test_hub_lists_hubinfo_in_its_supports():
    w = world()
    s, conn = w.login("alice")
    assert "$Supports NoGetINFO NoHello UserIP2 BotList HubTopic HubINFO|" in conn.messages()


def test_a_pinger_is_answered_and_not_announced():
    w = world()
    a, ca = w.login("alice", share=5000)
    ca.take()
    p, cp = w.login("pinger", supports=PINGER, share=0)
    assert p.pinger and p.state == "online"
    out = cp.take()
    assert myinfo("alice", share=5000) in out  # it reads the user list
    assert "$HubTopic the &#36;topic|" not in out  # no HubTopic in its $Supports
    assert not any(m.startswith("<sublevels>") for m in out)  # no message of the day
    assert ca.take() == []  # nobody hears of it
    assert list(w.hub.users) == ["alice"] and list(w.hub.pingers) == [p.id]
    w.send(p, "$BotINFO te-home.net pinger|")
    assert cp.take() == [f"$HubINFO Test Hub$hub.example.org:411$the &#36;topic$500$1024$2$10$"
                         f"sublevels {__version__}$op@example.org|"]
    assert w.hub.stats()["users"] == 1 and w.hub.stats()["pingers"] == 1
    assert w.hub.counters.snapshot()["totals"]["pinger_visits"] == 1
    v = w.hub.visits[-1]
    assert (v["nick"], v["address"], v["botinfo"]) == ("pinger", "10.0.0.1", "te-home.net pinger")


def test_a_pinger_may_not_chat_search_or_connect():
    w = world()
    a, ca = w.login("alice")
    p, cp = w.login("pinger", supports=PINGER, share=0)
    ca.take()
    w.send(p, "<pinger> hello|", "$To: alice From: pinger $<pinger> hi|",
           "$Search 10.0.0.1:412 F?F?0?1?x|", "$ConnectToMe alice 10.0.0.1:412|",
           "$RevConnectToMe pinger alice|", myinfo("pinger", share=99))
    assert ca.take() == []
    assert w.hub.counters.snapshot()["drops"]["pinger"] == 5


def test_a_pinger_is_closed_after_a_minute():
    w = world()
    p, cp = w.login("pinger", supports=PINGER, share=0)
    w.clock.advance(59)
    assert not cp.closed
    w.clock.advance(2)
    assert cp.closed and p.close_reason == "pinger time limit" and not w.hub.pingers


def test_botinfo_from_a_user_takes_it_out_of_the_user_list():
    w = world()
    a, ca = w.login("alice")
    p, cp = w.login("checker", supports="NoHello NoGetINFO")
    ca.take()
    assert "checker" in w.hub.users
    w.send(p, "$BotINFO dchublist.biz|")
    assert ca.take() == ["$Quit checker|"]
    assert p.pinger and "checker" not in w.hub.users and p.id in w.hub.pingers
    assert cp.take()[-1].startswith("$HubINFO Test Hub$")
    assert w.hub.visits[-1]["botinfo"] == "dchublist.biz"


def test_botinfo_before_myinfo():
    w = world()
    s, conn = w.connect()
    w.send(s, "$Supports NoHello|", f"$Key {w.key_for(conn)}|", "$ValidateNick pinger|",
           "$BotINFO pwiam|", myinfo("pinger", share=0))
    assert s.pinger and s.state == "online" and "pinger" not in w.hub.users
    assert w.hub.visits[-1]["botinfo"] == "pwiam"


def test_hubinfo_never_gives_a_loopback_address():
    for addr in ("", "127.0.0.1:411", "localhost", "[::1]:411"):
        w = world(public_address=addr)
        assert w.hub.listed_address() == ""
        assert w.hub.hub_info().startswith("$HubINFO Test Hub$$")
    assert world(public_address="hub.example.org:4012").hub.listed_address() == \
        "hub.example.org:4012"
    assert world(public_address="[2001:db8::1]:411").hub.listed_address() == "[2001:db8::1]:411"
