"""P4: search and results."""

from nmdc_fakes import World
from sublevels.nmdc.hub import Hub

TTH = "TTH:" + "A" * 39


def three():
    w = World()
    a, ca = w.login("alice", ip="10.0.0.1", mode="A")
    p, cp = w.login("pat", ip="10.0.0.2", mode="P")
    u, cu = w.login("uma", ip="10.0.0.3", mode=None)  # no tag: mode unknown
    for c in (ca, cp, cu):
        c.take()
    return w, a, ca, p, cp, u, cu


def test_query_shape():
    assert Hub.query_ok("F?T?0?1?some$words")
    assert Hub.query_ok("T?T?1024?2?x")
    assert Hub.query_ok(f"F?T?0?9?{TTH}")
    for bad in ("F?T?0?1", "X?T?0?1?x", "F?T?ten?1?x", "F?T?0?0?x", "F?T?0?10?x",
                "F?T?0?9?TTH:short"):
        assert not Hub.query_ok(bad), bad


def test_active_search_address_is_rewritten_and_goes_to_everyone_else():
    w, a, ca, p, cp, u, cu = three()
    w.send(a, "$Search 6.6.6.6:4000 F?T?0?1?x|")
    expect = "$Search 10.0.0.1:4000 F?T?0?1?x|"
    assert cp.take() == [expect] and cu.take() == [expect]
    assert ca.take() == []
    assert w.hub.counters.totals["search_rewritten"] == 1


def test_active_search_bad_port_dropped():
    w, a, ca, p, cp, u, cu = three()
    w.send(a, "$Search 10.0.0.1:0 F?T?0?1?x|")
    w.send(a, "$Search 10.0.0.1:70000 F?T?0?1?x|")
    assert cp.take() == []


def test_passive_search_goes_only_to_active_and_unknown():
    w, a, ca, p, cp, u, cu = three()
    w.send(p, "$Search Hub:pat F?T?0?1?x|")
    assert ca.take() == ["$Search Hub:pat F?T?0?1?x|"]
    assert cu.take() == ["$Search Hub:pat F?T?0?1?x|"]
    assert cp.take() == []


def test_passive_search_under_another_nick_dropped():
    w, a, ca, p, cp, u, cu = three()
    w.send(p, "$Search Hub:alice F?T?0?1?x|")
    assert ca.take() == []


def test_malformed_search_dropped():
    w, a, ca, p, cp, u, cu = three()
    w.send(a, "$Search 1.2.3.4:5 junk|")
    assert cp.take() == [] and w.hub.counters.drops["bad_search"] == 1


def test_chatonly():
    w = World()
    a, ca = w.login("alice")
    c, cc = w.login("chatty", supports="NoHello ChatOnly")
    cc.take()
    w.send(a, "$Search 10.0.0.1:4000 F?T?0?1?x|")
    assert cc.take() == []
    w.send(c, "$Search 10.0.0.1:4000 F?T?0?1?x|")
    assert cc.closed


def test_search_rate():
    w, a, ca, p, cp, u, cu = three()
    for _ in range(3):
        w.send(a, "$Search 10.0.0.1:4000 F?T?0?1?x|")
    assert len(cp.take()) == 2  # a burst of 2
    notice = ca.take()
    assert len(notice) == 1 and "too often" in notice[0]
    w.send(a, "$Search 10.0.0.1:4000 F?T?0?1?x|")
    assert ca.take() == []  # told once a minute
    w.clock.advance(10)
    w.send(a, "$Search 10.0.0.1:4000 F?T?0?1?x|")
    assert len(cp.take()) == 1


def test_sr_routing():
    w, a, ca, p, cp, u, cu = three()
    w.send(p, "$Search Hub:pat F?T?0?1?x|")
    sr = "$SR alice music\\x.mp3\x05123 3/5\x05TTH:" + "B" * 39 + " (10.0.0.9:411)\x05pat|"
    w.send(a, sr)
    assert cp.take() == ["$SR alice music\\x.mp3\x05123 3/5\x05TTH:" + "B" * 39 +
                         " (10.0.0.9:411)|"]


def test_sr_checks():
    w, a, ca, p, cp, u, cu = three()
    w.send(p, "$Search Hub:pat F?T?0?1?x|")
    cu.take()
    w.send(a, "$SR uma f\x05123 3/5\x05hub (1.2.3.4)\x05pat|")  # not the sender's nick
    w.send(a, "$SR alice f\x05123 3/5\x05hub (1.2.3.4)|")  # no target
    w.send(a, "$SR alice f\x05123 3/5\x05hub (1.2.3.4)\x05nobody|")  # target offline
    assert cp.take() == []
    w.send(a, "$SR alice f\x05123 3/5\x05hub (1.2.3.4)\x05uma|")  # uma never searched
    assert cu.take() == []


def test_sr_limit_per_responder_and_window():
    w, a, ca, p, cp, u, cu = three()
    w.send(p, "$Search Hub:pat F?T?0?1?x|")
    for i in range(12):
        w.send(a, f"$SR alice f{i}\x05123 3/5\x05hub (1.2.3.4)\x05pat|")
    assert len(cp.take()) == 10
    w.clock.advance(31)
    w.send(a, "$SR alice late\x05123 3/5\x05hub (1.2.3.4)\x05pat|")
    assert cp.take() == []


def test_ipv6_searcher_address():
    w = World()
    a, ca = w.login("alice", ip="2001:db8::1")
    b, cb = w.login("bob")
    cb.take()
    w.send(a, "$Search [2001:db8::9]:4000 F?T?0?1?x|")
    assert cb.take() == ["$Search [2001:db8::1]:4000 F?T?0?1?x|"]


def test_ipv4_mapped_address_is_plain_ipv4():
    w = World()
    a, ca = w.login("alice", ip="::ffff:10.1.2.3")
    assert a.ip == "10.1.2.3"


def test_passive_search_skips_socks5_users():
    w = World()
    p, cp = w.login("pat", mode="P")
    k, ck = w.login("sam", mode="5")
    ck.take()
    w.send(p, "$Search Hub:pat F?T?0?1?x|")
    assert ck.take() == []
