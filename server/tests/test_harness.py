"""The service through libcvc S7's ServerHarness (ARIADNE_SCRIPTING_HOSTS.md 6.5.8): room-call as
a client makes it, and the published subtree as the relay would send it. No arisrvx, no network."""

import pytest
from fastapi.testclient import TestClient

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
