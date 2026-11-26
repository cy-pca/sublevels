"""P9 §1: registration with hub lists, against a stand-in list server (tests/hublist_stub.py)."""

import asyncio
import socket

import pytest

from hublist_stub import StubList
from nmdc_fakes import World
from sublevels.nmdc import HubConfig
from sublevels.nmdc.hublists import BACKOFF_AFTER, Publisher, Target, push, push_address


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, 30))


def hub_world(**kw):
    kw.setdefault("public_address", "hub.example.org")
    w = World(HubConfig(name="Test|Hub", topic="a $test hub", **kw))
    w.login("alice", share=1000)
    w.login("bob", share=2000)
    return w


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_target_parse():
    assert Target.parse("hublist.te-home.net") == Target("hublist.te-home.net", 2501)
    assert Target.parse("l.example:2600;key=legacy") == Target("l.example", 2600, "legacy")
    assert Target.parse("adchub.com;method=pinger").method == "pinger"
    for bad in ("l.example:x", ":2501", "l.example;key=odd"):
        with pytest.raises(ValueError):
            Target.parse(bad)


def test_defaults_are_verlihubs_two_and_off_without_a_public_address():
    p = Publisher(World().hub)
    assert [t.target.name for t in p.targets] == ["hublist.te-home.net:2501",
                                                  "hublist.pwiam.com:2501"]
    assert not p.enabled and "public address" in p.why_off()
    assert run(p.register_now()) == []  # nothing pushed, nothing dialled


def test_push_address():
    assert push_address(World(HubConfig(public_address="hub.example.org")).hub) == "hub.example.org"
    assert push_address(World(HubConfig(public_address="hub.example.org:411")).hub) == \
        "hub.example.org"
    assert push_address(World(HubConfig(public_address="hub.example.org:4012")).hub) == \
        "hub.example.org:4012"
    assert push_address(World(HubConfig(public_address="127.0.0.1:411")).hub) == ""
    tls_only = HubConfig(public_address="hub.example.org", tls_required=True,
                         nmdcs_address="nmdcs://hub.example.org:4111")
    assert push_address(World(tls_only).hub) == "nmdcs://hub.example.org:4111"


def test_push_standard_key_and_the_record():
    async def go():
        stub = await StubList().start()
        w = hub_world(min_share=5 * 1024 * 1024 * 1024)
        a = await push(w.hub, Target("127.0.0.1", stub.port))
        await asyncio.sleep(0.1)
        await stub.close()
        return a, stub.got

    attempt, got = run(go())
    assert attempt["outcome"] == "sent" and attempt["users"] == 2 and attempt["share"] == 3000
    assert got == [{"key": "standard", "name": "Test&#124;Hub", "address": "hub.example.org",
                    "description": "[MINSHARE:5120MB] a &#36;test hub", "users": "2",
                    "share": "3000", "list": f"127.0.0.1:{got[0]['list'].split(':')[1]}"}]


def test_push_legacy_key():
    async def go():
        stub = await StubList().start()
        a = await push(hub_world().hub, Target("127.0.0.1", stub.port, key="legacy"))
        await asyncio.sleep(0.1)
        await stub.close()
        return a, stub.got

    attempt, got = run(go())
    assert attempt["outcome"] == "sent" and got[0]["key"] == "legacy"


def test_pingers_and_the_bot_are_not_counted():
    async def go():
        stub = await StubList().start()
        w = hub_world()
        p, _ = w.login("pinger", supports="NoHello BotINFO", share=0)
        assert p.pinger and p.state == "online"
        await push(w.hub, Target("127.0.0.1", stub.port))
        await asyncio.sleep(0.1)
        await stub.close()
        return stub.got

    assert run(go())[0]["users"] == "2"


def test_outcomes():
    async def go():
        w = hub_world()
        out = {"connect_failed": await push(w.hub, Target("127.0.0.1", free_port()))}
        stub = await StubList(greeting=b"HTTP/1.0 400 Bad Request\r\n\r\n|").start()
        out["no_lock"] = await push(w.hub, Target("127.0.0.1", stub.port))
        await stub.close()
        stub = await StubList(greeting=b"$Lock " + b"x" * 600 + b"|").start()
        out["long"] = await push(w.hub, Target("127.0.0.1", stub.port))
        await stub.close()
        stub = await StubList(silent=True).start()
        out["slow"] = await push(w.hub, Target("127.0.0.1", stub.port), timeout=0.5)
        await stub.close()
        return out

    out = run(go())
    assert out["connect_failed"]["outcome"] == "connect_failed"
    assert out["no_lock"]["outcome"] == "no_lock"
    assert out["long"]["outcome"] == "error" and "512" in out["long"]["reason"]
    assert out["slow"] == {**out["slow"], "outcome": "error", "reason": "timed out"}


def test_timer_jitter_backoff_and_results():
    async def go():
        w = hub_world(hublists=("a.example", "b.example;method=pinger"), hublist_interval=900,
                      hublist_jitter=120)
        slept = []
        outcomes = iter(["connect_failed"] * BACKOFF_AFTER + ["sent", "sent"])

        async def pusher(hub, target):
            return {"target": target.name, "outcome": next(outcomes), "users": 2, "share": 0}

        async def sleep(d):
            slept.append(d)
            if len(slept) > BACKOFF_AFTER + 2:
                raise asyncio.CancelledError

        p = Publisher(w.hub, rand=lambda: 0.5, sleep=sleep, pusher=pusher)
        assert [t.target.name for t in p.push_targets] == ["a.example:2501"]
        try:
            await p._run_one(p.push_targets[0])
        except asyncio.CancelledError:
            pass
        return p, slept, w

    p, slept, w = run(go())
    assert slept[0] == 60  # the first push: jitter only
    assert slept[1:BACKOFF_AFTER] == [960] * (BACKOFF_AFTER - 1)
    assert slept[BACKOFF_AFTER] == 3660  # 10 failures in a row: hourly
    assert slept[BACKOFF_AFTER + 1] == 960  # a success ends the back-off
    st = p.status()
    a, b = st["targets"]
    assert a["failures"] == 0 and a["last"]["outcome"] == "sent" and len(a["attempts"]) == 12
    assert b == {"target": "b.example:2501", "method": "pinger", "last_visit": None}
    assert w.hub.counters.snapshot()["totals"]["hublist_connect_failed"] == BACKOFF_AFTER


def test_pinger_target_shows_its_last_visit():
    w = hub_world(hublists=("adchub.com;method=pinger",))
    s, conn = w.login("FulDC-pinger", supports="NoHello BotINFO", share=0)
    w.send(s, "$BotINFO adchub.com hub list pinger|")
    t = Publisher(w.hub).status()["targets"][0]
    assert t["last_visit"]["nick"] == "FulDC-pinger"
    assert t["last_visit"]["botinfo"] == "adchub.com hub list pinger"
