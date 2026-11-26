"""The service through libcvc S7's ServerHarness (ARIADNE_SCRIPTING_HOSTS.md 6.5.8): room-call as
a client makes it, and the published subtree as the relay would send it. No arisrvx, no network."""

import pytest
from fastapi.testclient import TestClient

from nmdc_fakes import CP, Conn, myinfo
from sublevels import __version__
from sublevels.rpc import PING


def test_ping_over_room_call(harness):
    alice = harness.connect()  # no authenticate hook yet: the principal is the pid
    assert alice.call(PING) == {"pong": True, "version": __version__, "who": alice.pid}


def test_unknown_function_is_not_found(harness):
    from pycvc.ariadne.testing import CallError

    with pytest.raises(CallError) as e:
        harness.connect().call("sublevels.nope")
    assert e.value.code == "not_found"


def test_ping_takes_no_arguments(harness):
    from pycvc.ariadne.testing import CallError

    with pytest.raises(CallError) as e:
        harness.connect().call(PING, "extra")
    assert e.value.code == "internal"


def test_client_reads_the_published_subtree(harness):
    bob = harness.connect()
    tree = bob.get_tree("sublevels.server")
    assert tree["version"] == __version__
    assert tree["status"] == "up"
    assert bob.get("sublevels.server.version") == __version__


def test_client_reads_nothing_outside_the_namespace(harness, server):
    server.host.set("private.note", "the server's own")
    assert server.host.get("private.note") == "the server's own"
    carol = harness.connect()
    assert not carol.reads("private.note")
    assert carol.get("private.note") is None


def test_http_and_room_call_share_the_service(harness, server):
    http = TestClient(server.app).get("/healthz").json()
    assert http["version"] == harness.connect().call(PING)["version"] == __version__


def test_client_reads_the_hub_and_its_user_list(harness, server):
    hub = server.hub
    conn = Conn()
    s = hub.connect(conn, "10.9.8.7", 4000)
    first = bytes(conn.data).split(b"|", 1)[0]
    from sublevels.nmdc.codec import lock_to_key
    key = lock_to_key(first[len(b"$Lock "):].split(b" Pk=")[0])
    for m in (b"$Supports NoHello|", b"$Key " + key + b"|", b"$ValidateNick dora|",
              CP.encode(myinfo("dora", share=42, description="hi ", client="ncdc"))):
        hub.message(s, m)
    assert s.online
    try:
        hub.publish()
        viewer = harness.connect()
        tree = viewer.get_tree(hub.tree_path)
        assert tree["name"] == hub.config.name and tree["bot"] == hub.config.bot_nick
        assert tree["users"]["count"] == "1"
        (entry,) = tree["users"]["list"].values()
        assert entry["nick"] == "dora" and entry["share"] == "42" and entry["client"] == "ncdc"
        assert entry["mode"] == "A" and entry["protocol"] == "nmdc"
        assert "10.9.8.7" not in repr(tree)  # addresses are never published
    finally:
        hub.disconnect(s)
        hub.publish()
    assert harness.connect().get_tree(hub.tree_path)["users"]["count"] == "0"


def test_native_operator_functions_need_the_admin_role(harness, server):
    from pycvc.ariadne.testing import CallError

    with pytest.raises(CallError) as e:
        harness.connect().call("sublevels.bans", "")
    assert e.value.code != "not_found"  # there, but not for a plain participant
    hub = server.hub
    conn = Conn()
    s = hub.connect(conn, "10.9.8.6", 4000)
    first = bytes(conn.data).split(b"|", 1)[0]
    from sublevels.nmdc.codec import lock_to_key
    key = lock_to_key(first[len(b"$Lock "):].split(b" Pk=")[0])
    for m in (b"$Supports NoHello|", b"$Key " + key + b"|", b"$ValidateNick eddie|",
              CP.encode(myinfo("eddie"))):
        hub.message(s, m)
    admin = harness.client({"id": "joe", "name": "Joe", "roles": ["admin"]})
    try:
        info = admin.call("sublevels.info", "eddie")
        assert info["ok"] and info["online"] and info["address"] == "10.9.8.6"
        assert admin.call("sublevels.kick", "eddie", "from the room")["ok"]
        assert conn.closed and b"kicked by Joe because: from the room" in bytes(conn.data)
        assert admin.call("sublevels.kick", "eddie", "")["ok"] is False  # gone
        r = admin.call("sublevels.ban", "ip:10.9.8.5", "1h", "test")
        assert r["ok"]
        assert [b["target"] for b in admin.call("sublevels.bans", "")["bans"]] == ["ip:10.9.8.5"]
        assert admin.call("sublevels.unban", "ip:10.9.8.5")["ok"]
    finally:
        hub.disconnect(s)


def test_the_public_user_count_series(harness, server):
    hub = server.hub
    hub.analytics.tick()
    hub.analytics.tick()
    viewer = harness.connect()
    got = viewer.get(f"{hub.tree_path}.series.users")
    assert got is not None and [float(x) for x in str(got).split(",")][-2:] == [0.0, 0.0]
    admin = harness.client({"id": "joe", "name": "Joe", "roles": ["admin"]})
    r = admin.call("sublevels.analytics", "users", "10s", "0")
    assert r["ok"] and r["kind"] == "gauge" and len(r["points"]) >= 2
