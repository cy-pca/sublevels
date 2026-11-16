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
