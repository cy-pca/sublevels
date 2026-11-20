"""The room bridge on a stand-in host (any pycvc, or none): NMDC users join the room, the two chats
are joined, and direct messages go both ways."""

from nmdc_fakes import World, myinfo
from sublevels.bridge import RoomBridge, available, user_id


class Msg:
    def __init__(self, pid, name, text, to=None):
        self.pid, self.name, self.text, self.to = pid, name, text, to


class FakeHost:
    """The bridged-users API as the hub uses it, recording the calls."""

    def __init__(self):
        self.present, self.posts, self.hooks = {}, [], {}

    def room_presence(self, room, uid, info, *, via):
        self.present[(via, uid)] = dict(info)

    def room_absent(self, room, uid, *, via):
        self.present.pop((via, uid), None)

    def room_chat_post(self, room, text, *, sender_id, name=None, via, to=None):
        if len(text.encode()) > 2000:
            raise ValueError("longer than 2000 bytes")
        self.posts.append({"room": room, "text": text, "from": sender_id, "name": name,
                           "via": via, "to": to})

    def on_room_chat(self, room):
        return lambda fn: self.hooks.setdefault("chat", fn)

    def on_room_dm(self, room):
        return lambda fn: self.hooks.setdefault("dm", fn)


def bridged():
    host = FakeHost()
    w = World()
    b = RoomBridge(w.hub, host, "standup")
    return w, host, b


def test_available():
    assert available(FakeHost())
    assert not available(object())


def test_nmdc_users_join_and_leave_the_room():
    w, host, b = bridged()
    s, c = w.login("alice", share=3 << 30, client="ncdc")
    assert host.present[("nmdc", "alice")] == {"name": "alice", "detail": "ncdc, 3.0 GiB shared"}
    w.send(s, myinfo("alice", share=1 << 20, client="ncdc"))
    w.clock.advance(11)
    assert host.present[("nmdc", "alice")]["detail"] == "ncdc, 1.0 MiB shared"
    w.hub.disconnect(s)
    assert ("nmdc", "alice") not in host.present


def test_nmdc_chat_reaches_the_room_unescaped_but_commands_do_not():
    w, host, b = bridged()
    s, c = w.login("alice")
    w.send(s, "<alice> costs &#36;5 &amp; more|", "<alice> +help|")
    assert host.posts == [{"room": "standup", "text": "costs $5 & more", "from": "alice",
                           "name": "alice", "via": "nmdc", "to": None}]


def test_room_posts_reach_nmdc_users_with_the_room_prefix():
    w, host, b = bridged()
    s, c = w.login("alice")
    c.take()
    host.hooks["chat"](Msg("p1", "Bob Smith", "hi $ all"))
    assert c.take() == ["<[standup]Bob_Smith> hi &#36; all|"]


def test_direct_messages_both_ways():
    w, host, b = bridged()
    s, c = w.login("alice")
    c.take()
    host.hooks["dm"](Msg("p1", "bob", "psst", to=user_id("alice")))
    assert c.take() == ["$To: alice From: [standup]bob $<[standup]bob> psst|"]
    w.send(s, "$To: [standup]bob From: alice $<alice> hi &#36;back|")
    assert host.posts[-1] == {"room": "standup", "text": "hi $back", "from": "alice",
                              "name": "alice", "via": "nmdc", "to": "p1"}


def test_pm_to_an_unknown_room_user():
    w, host, b = bridged()
    s, c = w.login("alice")
    c.take()
    w.send(s, "$To: [standup]nobody From: alice $<alice> hi|")
    assert "not in the room" in c.take()[0] and host.posts == []


def test_hub_nicks_cannot_take_the_room_prefix():
    w, host, b = bridged()
    assert w.hub.nick_problem("[standup]bob") == "reserved"
    assert w.hub.nick_problem("[STANDUP]bob") == "reserved"
    assert w.hub.nick_problem("[other]bob") == ""


def test_long_nicks_get_a_stable_id():
    long = "é" * 64
    assert user_id(long) == user_id(long) and len(user_id(long).encode()) <= 128
