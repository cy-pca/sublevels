"""P3: main chat, private messages and the hub bot's commands."""

from nmdc_fakes import World
from sublevels.nmdc import HubConfig, Limits


def two():
    w = World()
    a, ca = w.login("alice")
    b, cb = w.login("bob")
    ca.take()
    cb.take()
    return w, a, ca, b, cb


def test_chat_is_echoed_to_everyone_sender_included():
    w, a, ca, b, cb = two()
    w.send(a, "<alice> hello &#36;5 &#124; ok|")
    assert ca.take() == ["<alice> hello &#36;5 &#124; ok|"]
    assert cb.take() == ["<alice> hello &#36;5 &#124; ok|"]
    assert w.hub.counters.totals["chat"] == 1


def test_chat_bytes_are_passed_through():
    w, a, ca, b, cb = two()
    raw = "<alice> caf\xe9 \x81|"
    w.send(a, raw)
    assert cb.take() == [raw]


def test_chat_under_another_nick_is_dropped_then_closes():
    w, a, ca, b, cb = two()
    for _ in range(2):
        w.send(a, "<bob> I am bob|")
    assert cb.take() == [] and not ca.closed
    w.send(a, "<bob> I am bob|")
    assert ca.closed


def test_chat_kick_shaped_line_from_a_user_is_ordinary_chat():
    w, a, ca, b, cb = two()
    w.send(a, "<alice> sublevels is kicking bob because: no|")
    assert cb.take() == ["<alice> sublevels is kicking bob because: no|"]


def test_chat_length_and_rate_limits():
    w = World(HubConfig(limits=Limits(chat_text_max=10)))
    a, ca = w.login("alice")
    ca.take()
    w.send(a, "<alice> " + "x" * 11 + "|")
    out = ca.take()
    assert len(out) == 1 and out[0].startswith("<sublevels> ") and "longer" in out[0]
    w2, a, ca, b, cb = two()
    for i in range(4):
        w2.send(a, f"<alice> line {i}|")
    assert len(cb.take()) == 3  # a burst of 3
    w2.clock.advance(2)
    w2.send(a, "<alice> later|")
    assert cb.take() == ["<alice> later|"]


def test_commands_answer_the_sender_only():
    w, a, ca, b, cb = two()
    w.send(a, "<alice> +help|")
    out = ca.take()
    assert out and all(m.startswith("<sublevels> ") for m in out)
    assert any("+rules" in m for m in out)
    assert cb.take() == []
    w.send(a, "<alice> +rules|")
    assert any("civil" in m for m in ca.take())
    w.send(a, "<alice> +motd|")
    assert any("Welcome" in m for m in ca.take())
    w.send(a, "<alice> +nope|")
    assert "No such command" in ca.take()[0]


def test_bang_from_a_user_is_chat():
    w, a, ca, b, cb = two()
    w.send(a, "<alice> !hello|")
    assert cb.take() == ["<alice> !hello|"]


def test_private_message_goes_to_the_target_only_unchanged():
    w, a, ca, b, cb = two()
    pm = "$To: bob From: alice $<alice> hi &#36;|"
    w.send(a, pm)
    assert cb.take() == [pm]
    assert ca.take() == []


def test_private_message_checks():
    w, a, ca, b, cb = two()
    w.send(a, "$To: bob From: carol $<carol> spoof|")
    assert cb.take() == []
    w.send(a, "$To: nobody From: alice $<alice> hi|")
    assert ca.take() == ["$To: alice From: sublevels $<sublevels> nobody is not online.|"]


def test_private_message_to_the_bot_is_a_command():
    w, a, ca, b, cb = two()
    w.send(a, "$To: sublevels From: alice $<alice> +rules|")
    out = ca.take()
    assert out and all(m.startswith("$To: alice From: sublevels $<sublevels> ") for m in out)


def test_private_message_rate():
    w, a, ca, b, cb = two()
    for i in range(11):
        w.send(a, f"$To: bob From: alice $<alice> {i}|")
    assert len(cb.take()) == 10
    assert "too fast" in ca.take()[-1]


def test_hub_text_is_escaped():
    w = World(HubConfig(motd=("costs $5 | more & less",)))
    a, ca = w.login("alice")
    assert "<sublevels> costs &#36;5 &#124; more &amp; less|" in ca.take()
