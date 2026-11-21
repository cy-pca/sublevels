"""The room bridge through libcvc's ServerHarness (pycvc.ariadne 4.0.2+, ServerHarness(room=)):
Ariadne participants see NMDC users in the room's roster and chat with them both ways."""

import pytest

from nmdc_fakes import CP, Conn, myinfo
from sublevels.nmdc.codec import lock_to_key


@pytest.fixture
def room(harness, server):
    if server.hub.bridge is None:
        import os
        if os.environ.get("SUBLEVELS_REQUIRE_BRIDGE") == "1":
            pytest.fail("this host has no bridged-users API (SUBLEVELS_REQUIRE_BRIDGE=1)")
        pytest.skip("this host has no bridged-users API (pycvc.ariadne before 4.0.2)")
    return server.hub


def nmdc_login(hub, nick):
    conn = Conn()
    s = hub.connect(conn, "10.9.9.9", 4000)
    lock = bytes(conn.data).split(b"|", 1)[0][len(b"$Lock "):].split(b" Pk=")[0]
    for m in (b"$Supports NoHello|", b"$Key " + lock_to_key(lock) + b"|",
              f"$ValidateNick {nick}|".encode(), CP.encode(myinfo(nick, client="ncdc"))):
        hub.message(s, m)
    assert s.online
    conn.take()
    return s, conn


def test_ariadne_clients_see_nmdc_users_and_chat_with_them(room, harness):
    hub = room
    s, conn = nmdc_login(hub, "zed")
    try:
        ann = harness.connect(name="ann")
        (entry,) = [e for e in ann.roster("bridged") if e["uid"] == "zed"]
        assert entry["name"] == "zed" and entry["via"] == "nmdc" and entry["bridged"] == "1"
        assert "ncdc" in entry["detail"]

        hub.message(s, CP.encode("<zed> hello from NMDC|"))
        last = ann.chat_messages()[-1]
        assert (last["name"], last["text"], last["via"]) == ("zed", "hello from NMDC", "nmdc")

        ann.chat_post("hello from the room")
        assert conn.take()[-1] == "<[standup]ann> hello from the room|"

        ann.chat_post("just you", to=entry["pid"])
        assert conn.take()[-1] == "$To: zed From: [standup]ann $<[standup]ann> just you|"
        hub.message(s, CP.encode("$To: [standup]ann From: zed $<zed> just you too|"))
        assert ann.chat_messages(entry["pid"])[-1]["text"] == "just you too"
    finally:
        hub.disconnect(s)
    assert not [e for e in harness.connect(name="bea").roster("bridged") if e["uid"] == "zed"]
