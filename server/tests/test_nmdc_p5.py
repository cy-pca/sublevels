"""P5: $ConnectToMe and $RevConnectToMe."""

from nmdc_fakes import World


def pair(mode_a="A", mode_b="A"):
    w = World()
    a, ca = w.login("alice", ip="10.0.0.1", mode=mode_a)
    b, cb = w.login("bob", ip="10.0.0.2", mode=mode_b)
    ca.take()
    cb.take()
    return w, a, ca, b, cb


def test_ctm_rewrites_the_address_and_goes_to_the_remote_only():
    w, a, ca, b, cb = pair()
    w.send(a, "$ConnectToMe bob 6.6.6.6:3000|")
    assert cb.take() == ["$ConnectToMe bob 10.0.0.1:3000|"]
    assert ca.take() == []


def test_ctm_keeps_the_suffix():
    w, a, ca, b, cb = pair()
    w.send(a, "$ConnectToMe bob 10.0.0.1:3000S|")
    w.send(a, "$ConnectToMe bob 10.0.0.1:3001NS|")
    assert cb.take() == ["$ConnectToMe bob 10.0.0.1:3000S|", "$ConnectToMe bob 10.0.0.1:3001NS|"]


def test_ctm_three_argument_form():
    w, a, ca, b, cb = pair()
    w.send(a, "$ConnectToMe alice bob 1.1.1.1:3000|")
    assert cb.take() == ["$ConnectToMe alice bob 10.0.0.1:3000|"]
    w.send(a, "$ConnectToMe carol bob 1.1.1.1:3000|")
    assert cb.take() == []


def test_ctm_drops():
    w, a, ca, b, cb = pair()
    for bad in ("$ConnectToMe alice 10.0.0.1:3000|", "$ConnectToMe sublevels 10.0.0.1:3000|",
                "$ConnectToMe nobody 10.0.0.1:3000|", "$ConnectToMe bob 10.0.0.1:0|",
                "$ConnectToMe bob 10.0.0.1:99999|", "$ConnectToMe bob 10.0.0.1|",
                "$ConnectToMe bob 10.0.0.1:12ab3|"):
        w.send(a, bad)
    assert cb.take() == [] and ca.take() == []


def test_rctm_forwarded_unchanged():
    w, a, ca, b, cb = pair("P", "A")
    w.send(a, "$RevConnectToMe alice bob|")
    assert cb.take() == ["$RevConnectToMe alice bob|"]


def test_rctm_sender_must_be_self():
    w, a, ca, b, cb = pair("P", "A")
    w.send(a, "$RevConnectToMe bob alice|")
    assert cb.take() == [] and ca.take() == []


def test_rctm_both_passive_refused_with_a_bot_line():
    w, a, ca, b, cb = pair("P", "P")
    w.send(a, "$RevConnectToMe alice bob|")
    assert cb.take() == []
    out = ca.take()
    assert len(out) == 1 and out[0].startswith("<sublevels> bob is passive too")


def test_ctm_limits():
    w, a, ca, b, cb = pair()
    for i in range(6):
        w.send(a, f"$ConnectToMe bob 10.0.0.1:{3000 + i}|")
    assert len(cb.take()) == 5  # at most 5 to one remote in 10 s
    w2 = World()
    a2, _ = w2.login("alice")
    conns = [w2.login(f"u{i}")[1] for i in range(25)]
    for c in conns:
        c.take()
    for i in range(25):
        w2.send(a2, f"$ConnectToMe u{i} 10.0.0.1:3000|")
    assert sum(len(c.take()) for c in conns) == 20
    w2.clock.advance(10)
    w2.send(a2, "$ConnectToMe u0 10.0.0.1:3000|")
    assert len(conns[0].take()) == 1
