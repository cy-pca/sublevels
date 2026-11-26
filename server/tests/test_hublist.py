"""P10: hublist mode: registrations on 2501, the pinger against a real hub, the entries' states,
and the XML list clients load."""

import asyncio
import bz2
import shutil
import subprocess
import xml.etree.ElementTree as ET

import pytest

pytest.importorskip("sqlalchemy")

from nmdc_client import Client  # noqa: E402
from sublevels.hublist import pinger  # noqa: E402
from sublevels.hublist.listxml import COLUMNS, Lists  # noqa: E402
from sublevels.hublist.register import RegistrationServer, parse_record  # noqa: E402
from sublevels.hublist.service import BACKOFF, Hublist, HublistConfig  # noqa: E402
from sublevels.hublist.store import DROP_AFTER, Store, normalise  # noqa: E402
from sublevels.nmdc import Hub, HubConfig, start_listeners  # noqa: E402
from sublevels.nmdc.codec import CodePage, escape, legacy_magic, lock_to_key  # noqa: E402

CP = CodePage("cp1252")


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, 60))


class Clock:
    def __init__(self):
        self.now = 1_800_000_000.0

    def __call__(self):
        return self.now


def store(tmp_path, clock=None):
    return Store(f"sqlite:///{tmp_path / 'h.sqlite3'}", clock=clock or Clock())


async def push(port, fields, *, legacy=False, key_ok=True):
    """A hub's registration (P9 §1), written here from the spec."""
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    first = await reader.readuntil(b"|")
    lock = first[len(b"$Lock "):].split(b" Pk=")[0]
    magic = legacy_magic(writer.get_extra_info("sockname")[1]) if legacy else 5
    key = lock_to_key(lock, magic) if key_ok else b"nonsense"
    writer.write(b"$Key " + key + b"|" + CP.encode("".join(f + "|" for f in fields)))
    await writer.drain()
    await asyncio.sleep(0.2)
    writer.close()


def test_normalise():
    assert normalise("Hub.Example.org") == "dchub://hub.example.org:411"
    assert normalise("hub.example.org:4012") == "dchub://hub.example.org:4012"
    assert normalise("nmdcs://hub.example.org:4111/?kp=SHA256/X") == "nmdcs://hub.example.org:4111"
    assert normalise("[2001:db8::1]:411") == "dchub://[2001:db8::1]:411"
    for bad in ("", "ftp://x", "a b", "hub:99999"):
        with pytest.raises(ValueError):
            normalise(bad)


def test_parse_record():
    r = parse_record(["My&#124;Hub", "hub.example.org", "[MINSHARE:5MB] a &#36;hub &amp; more",
                      "12", "3000", "list.example:2501"])
    assert r == {"name": "My|Hub", "users": 12, "share": 3000, "min_share": 5 * 1024 * 1024,
                 "description": "a $hub & more", "address": "dchub://hub.example.org:411"}
    for bad in (["n", "a"], ["n", "hub", "d", "x", "1"], ["n", "not a hub", "d", "1", "1"]):
        with pytest.raises(ValueError):
            parse_record(bad)


def test_registrations(tmp_path):
    async def go():
        st = store(tmp_path)
        reg = RegistrationServer(st)
        port = await reg.start("127.0.0.1", 0)
        rec = ["Hub One", "one.example.org", "the first", "5", "100", f"127.0.0.1:{port}"]
        await push(port, rec)
        await push(port, ["Hub Two", "two.example.org:4012", "", "0", "0"], legacy=True)
        await push(port, ["Bad", "bad.example.org", "", "1", "1"], key_ok=False)
        for i in range(4):  # one address, past 4 an hour
            await push(port, ["Hub One", "one.example.org", f"again {i}", "5", "100"])
        st.set_state("dchub://two.example.org:4012", "banned")
        await push(port, ["Hub Two", "two.example.org:4012", "", "0", "0"])
        await reg.close()
        return st, reg

    st, reg = run(go())
    one = st.get("dchub://one.example.org:411")
    assert one["state"] == "pending" and one["name"] == "Hub One" and one["source"] == "127.0.0.1"
    assert st.get("dchub://two.example.org:4012") is not None
    assert st.get("dchub://bad.example.org:411") is None
    assert reg.counts["wrong_key"] == 1 and reg.counts["banned"] == 1
    # 4 an hour per source too: one + two + 2 more of one's, then the limit
    assert reg.counts["registered"] == 4 and reg.counts["rate_limited"] == 2


def test_key_check_off(tmp_path):
    async def go():
        st = store(tmp_path)
        reg = RegistrationServer(st, check_key=False)
        port = await reg.start("127.0.0.1", 0)
        await push(port, ["Any", "any.example.org", "", "1", "1"], key_ok=False)
        await reg.close()
        return st

    assert run(go()).get("dchub://any.example.org:411")["name"] == "Any"


async def hub_on(tmp_path=None, **kw):
    kw.setdefault("nmdc_listen", "127.0.0.1:0")
    kw.setdefault("nmdcs_listen", "off")
    kw.setdefault("max_users", 500)
    hub = Hub(HubConfig(name="Pinged Hub", topic="the topic", owner="op@example.org",
                        min_share=1024, public_address="hub.example.org", **kw))
    listeners = await start_listeners(hub)
    ports = {p: n for p, _, n in listeners.addresses()}
    return hub, listeners, ports


def test_the_pinger_against_a_real_hub():
    async def go():
        hub, listeners, ports = await hub_on()
        users = []
        try:
            for nick, share in (("alice", 1000), ("bob", 2500)):
                c = Client(nick)
                c.share = share
                await c.connect("127.0.0.1", ports["nmdc"])
                await c.login()
                users.append(c)
            got = await pinger.ping(f"dchub://127.0.0.1:{ports['nmdc']}", list_name="test list",
                                    list_url="https://list.example/")
            await asyncio.sleep(0.2)
            return got, list(hub.visits), dict(hub.users)
        finally:
            for c in users:
                await c.close()
            await listeners.close(hub)

    got, visits, users = run(go())
    assert got["name"] == "Pinged Hub" and got["topic"] == "the topic"
    assert got["users"] == 2 and got["bots"] == 1 and got["errcode"] == 0
    assert got["max_users"] == 500 and got["min_share"] == 1024 and got["owner"] == "op@example.org"
    assert got["software"].startswith("sublevels ") and got["peer"] == "127.0.0.1"
    assert visits[-1]["botinfo"] == "test list https://list.example/"  # the hub saw a pinger
    assert "sublevels-pinger" not in users


def test_the_pinger_over_tls_records_the_keyprint(tmp_path):
    if shutil.which("openssl") is None:
        pytest.skip("no openssl")
    cert, key = tmp_path / "hub.crt", tmp_path / "hub.key"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                    "-subj", "/CN=127.0.0.1", "-keyout", str(key), "-out", str(cert)], check=True,
                   capture_output=True)

    async def go():
        hub, listeners, ports = await hub_on(nmdcs_listen="127.0.0.1:0", tls_cert=str(cert),
                                             tls_key=str(key))
        try:
            got = await pinger.ping(f"nmdcs://127.0.0.1:{ports['nmdcs']}")
            return got, hub.keyprint
        finally:
            await listeners.close(hub)

    got, kp = run(go())
    assert got["kp"] == kp and kp.startswith("SHA256/") and got["users"] == 0


def test_a_full_hub_and_one_that_is_down():
    async def go():
        hub, listeners, ports = await hub_on(max_users=0)
        try:
            full = await pinger.ping(f"dchub://127.0.0.1:{ports['nmdc']}")
        finally:
            await listeners.close(hub)
        with pytest.raises(pinger.PingError):
            await pinger.ping(f"dchub://127.0.0.1:{ports['nmdc']}", timeout=3)
        return full

    assert run(go())["errcode"] == 226


def test_states_reliability_and_drop(tmp_path):
    clock = Clock()
    st = store(tmp_path, clock)
    a = "dchub://hub.example.org:411"
    st.register(a, "10.0.0.1", {"name": "H"})
    assert st.record_ping(a, True, {"users": 3}, clock.now + 1800)["state"] == "listed"
    clock.now += 3600
    e = st.record_ping(a, False, {}, clock.now, "refused")
    assert e["state"] == "listed" and e["fails"] == 1 and e["last_error"] == "refused"
    assert st.reliability(a) == 50.0
    clock.now += DROP_AFTER
    assert st.record_ping(a, False, {}, clock.now)["state"] == "dropped"
    clock.now += 60
    assert st.record_ping(a, True, {}, clock.now)["state"] == "listed"  # back by a good ping
    st.set_state(a, "hidden")
    assert st.record_ping(a, True, {}, clock.now)["state"] == "hidden"
    assert st.due(clock.now + 1e9, 10) == []  # hidden ones are not pinged
    with pytest.raises(ValueError):
        st.set_state(a, "gone")


def test_backoff_and_the_schedule(tmp_path):
    clock = Clock()
    h = Hublist(HublistConfig(enabled=True, db=f"sqlite:///{tmp_path / 'h.sqlite3'}"),
                clock=clock, rand=lambda: 0.5)
    assert h.next_after(True, 0) == clock.now + 1800 + 150
    assert [h.next_after(False, n) - clock.now for n in range(5)] == \
        [BACKOFF[0], BACKOFF[1], BACKOFF[2], BACKOFF[2], BACKOFF[2]]
    h.store.close()


def test_the_xml_list(tmp_path):
    clock = Clock()
    st = store(tmp_path, clock)
    for addr, cc in (("dchub://a.example:411", "DE"), ("nmdcs://b.example:4111", "US"),
                     ("dchub://c.example:411", "")):
        st.register(addr, "10.0.0.1", {"name": f"Hub <{addr[8]}> & co", "users": 4,
                                       "share": 5_497_558_138_880})
        st.record_ping(addr, True, {"country": cc, "kp": "SHA256/K" if "nmdcs" in addr else "",
                                    "encoding": "CP1252", "software": "sublevels 0.1.0"},
                       clock.now)
    st.register("dchub://pending.example:411", "10.0.0.1", {"name": "not yet"})
    lists = Lists(st, name="test list", url="https://list.example/", clock=clock)
    body, etag, _ = lists.get("hublist.xml")
    root = ET.fromstring(body)
    assert root.tag == "Hublist" and root.get("Name") == "test list"
    cols = [c.get("Name") for c in root.iter("Column")]
    assert cols == [c for c, _ in COLUMNS] and cols[-1] == "KP"
    hubs = list(root.iter("Hub"))
    assert [h.get("Address") for h in hubs] == ["dchub://a.example:411", "dchub://c.example:411",
                                                "nmdcs://b.example:4111"]
    a = hubs[0]
    assert a.get("Name") == "Hub <a> & co" and a.get("Shared") == "5497558138880"
    assert a.get("Reliability") == "100.0%" and a.get("Country") == "DE"
    assert list(a.attrib) == [c for c, _ in COLUMNS]  # every attribute, in the columns' order
    assert hubs[2].get("KP") == "SHA256/K"
    assert bz2.decompress(lists.get("hublist.xml.bz2")[0]) == body
    de = ET.fromstring(bz2.decompress(lists.get("hublist-de.xml.bz2")[0]))
    assert [h.get("Address") for h in de.iter("Hub")] == ["dchub://a.example:411"]
    assert lists.get("hublist-fr.xml.bz2") is None


def test_end_to_end_and_the_http_api(tmp_path):
    """A hub registers, the list pings it, the list serves it, and operators moderate it."""
    from fastapi.testclient import TestClient

    from sublevels.api import create_app
    from sublevels.service import Service

    async def go():
        hub, listeners, ports = await hub_on()
        h = Hublist(HublistConfig(enabled=True, listen="127.0.0.1:0", name="e2e list",
                                  db=f"sqlite:///{tmp_path / 'h.sqlite3'}"))
        await h.start()
        try:
            await push(h.port, [escape(hub.config.name), f"127.0.0.1:{ports['nmdc']}",
                                "desc", "0", "0", f"127.0.0.1:{h.port}"])
            entry = h.store.get(f"dchub://127.0.0.1:{ports['nmdc']}")
            assert entry["state"] == "pending"
            after = await h.ping_one(entry)
            return h, after
        finally:
            await listeners.close(hub)
            await h.registrar.close()
            for t in h.tasks:
                t.cancel()

    h, after = run(go())
    assert after["state"] == "listed" and after["topic"] == "the topic"
    service = Service()
    service.hublist = h
    service.admin_token = "t0ken"
    c = TestClient(create_app(service))
    r = c.get("/hublist.xml.bz2")
    assert r.status_code == 200 and r.headers["content-type"] == "application/x-bzip2"
    assert b"Pinged Hub" in bz2.decompress(r.content)
    assert c.get("/hublist.xml.bz2", headers={"If-None-Match": r.headers["etag"]}).status_code \
        == 304
    auth = {"Authorization": "Bearer t0ken"}
    assert c.get("/admin/hublist").status_code == 401
    listing = c.get("/admin/hublist", headers=auth).json()
    assert listing["stats"]["entries"] == {"listed": 1}
    assert listing["entries"][0]["reliability"] == 100.0
    addr = listing["entries"][0]["address"]
    assert c.put("/admin/hublist/state", json={"address": addr, "state": "hidden"},
                 headers=auth).json()["state"] == "hidden"
    assert b"Pinged Hub" not in c.get("/hublist.xml").content
    assert c.put("/admin/hublist/state", json={"address": addr, "state": "gone"},
                 headers=auth).status_code == 422
    assert c.delete(f"/admin/hublist/entry?address={addr}", headers=auth).json()["ok"]
    assert c.get("/admin/hublist", headers=auth).json()["entries"] == []
    no = TestClient(create_app(Service()))
    assert no.get("/hublist.xml").status_code == 404
    h.store.close()
