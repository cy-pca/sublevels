"""The hub on real sockets: plain NMDC and NMDCS (P7 §1), message limits, a slow reader, and the
certificate reload. Each test runs its own event loop; no pycvc is needed."""

import asyncio
import shutil
import ssl
import subprocess

import pytest

from nmdc_client import Client
from sublevels.nmdc import Hub, HubConfig, Limits, start_listeners
from sublevels.nmdc.listener import TlsContext


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, 30))


async def hub_on(config: HubConfig):
    hub = Hub(config)
    listeners = await start_listeners(hub)
    ports = {proto: port for proto, _, port in listeners.addresses()}
    return hub, listeners, ports


@pytest.fixture(params=["asyncio", "engine"])
def transport(request):
    """Plain NMDC on the hub's loop, and on libcvc's native engine (N2) when pycvc has it."""
    if request.param == "engine":
        from sublevels.nmdc.engine import available
        if not available():
            import os
            if os.environ.get("SUBLEVELS_REQUIRE_ENGINE") == "1":
                pytest.fail("this pycvc has no connection engine (SUBLEVELS_REQUIRE_ENGINE=1)")
            pytest.skip("this pycvc has no connection engine (libcvc N2)")
    return request.param


def plain(transport="asyncio", **kw) -> HubConfig:
    return HubConfig(nmdc_listen="127.0.0.1:0", nmdcs_listen="off", transport=transport, **kw)


def test_two_clients_chat_pm_search_and_ctm(transport):
    async def go():
        hub, listeners, ports = await hub_on(plain(transport))
        a, b = Client("alice"), Client("bob", mode="P")
        for c in (a, b):
            await c.connect("127.0.0.1", ports["nmdc"])
            await c.login()
        a.send("<alice> hello|")
        assert await b.read_until(lambda m: m.startswith("<alice>")) == "<alice> hello|"
        assert await a.read_until(lambda m: m.startswith("<alice>")) == "<alice> hello|"
        b.send("$To: alice From: bob $<bob> psst|")
        assert await a.read_until(lambda m: m.startswith("$To:")) == "$To: alice From: bob $<bob> psst|"
        b.send("$Search Hub:bob F?T?0?1?song|")
        assert await a.read_until(lambda m: m.startswith("$Search")) == "$Search Hub:bob F?T?0?1?song|"
        a.send("$SR alice song.mp3\x0510 1/1\x05hub (127.0.0.1)\x05bob|")
        assert await b.read_until(lambda m: m.startswith("$SR")) == "$SR alice song.mp3\x0510 1/1\x05hub (127.0.0.1)|"
        b.send("$RevConnectToMe bob alice|")
        assert await a.read_until(lambda m: m.startswith("$RevConnectToMe")) == "$RevConnectToMe bob alice|"
        a.send("$ConnectToMe bob 9.9.9.9:5000|")
        assert await b.read_until(lambda m: m.startswith("$ConnectToMe")) == "$ConnectToMe bob 127.0.0.1:5000|"
        assert hub.stats()["users"] == 2
        await a.close()
        assert await b.read_until(lambda m: m.startswith("$Quit")) == "$Quit alice|"
        await b.close()
        await listeners.close(hub)

    run(go())


def test_message_too_long_closes(transport):
    async def go():
        hub, listeners, ports = await hub_on(plain(transport, limits=Limits(login_message_max=64)))
        c = Client("x")
        await c.connect("127.0.0.1", ports["nmdc"])
        await c.read()  # $Lock
        # No '|' yet: the partial message is already too long. The engine has one frame limit per
        # listener (the online one, 64 KiB), not per connection, so it needs more.
        c.send(b"$Supports " + b"A" * (100 if transport == "asyncio" else 70000))
        with pytest.raises((asyncio.IncompleteReadError, ConnectionError)):
            while True:
                await c.read()
        await listeners.close(hub)
        assert hub.counters.drops["too_long"] == 1

    run(go())


def test_slow_reader_is_closed(transport):
    async def go():
        hub, listeners, ports = await hub_on(plain(transport,
                                                   limits=Limits(output_queue_max=256 * 1024)))
        a = Client("alice")
        await a.connect("127.0.0.1", ports["nmdc"])
        await a.login()
        s = hub.users["alice"]
        # alice stops reading; the hub keeps writing until her queue passes the limit.
        for _ in range(4096):
            if s.state == "closed":
                break
            s.conn.write(b"x" * 16384)
            await asyncio.sleep(0)
        for _ in range(200):
            if s.state == "closed":
                break
            await asyncio.sleep(0.05)
        assert s.state == "closed" and hub.counters.refusals["slow_reader"] == 1
        await listeners.close(hub)

    run(go())


needs_openssl = pytest.mark.skipif(shutil.which("openssl") is None, reason="no openssl CLI")


def make_cert(tmp_path, name="hub"):
    cert, key = tmp_path / f"{name}.crt", tmp_path / f"{name}.key"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                    "-subj", f"/CN={name}", "-keyout", str(key), "-out", str(cert)],
                   check=True, capture_output=True)
    return str(cert), str(key)


@needs_openssl
def test_nmdcs_login(tmp_path, transport):
    cert, key = make_cert(tmp_path)

    async def go():
        hub, listeners, ports = await hub_on(HubConfig(nmdc_listen="off",
                                                       nmdcs_listen="127.0.0.1:0",
                                                       tls_cert=cert, tls_key=key,
                                                       transport=transport))
        assert bool(listeners.engines) == (transport == "engine")  # NMDCS on the engine (N3)
        c = Client("secure")
        await c.connect("127.0.0.1", ports["nmdcs"], tls=True)
        await c.login()
        assert hub.users["secure"].tls and hub.users["secure"].protocol == "nmdcs"
        assert hub.stats()["tls_connections"] == 1
        await c.close()
        await listeners.close(hub)

    run(go())


@needs_openssl
def test_nmdcs_refuses_tls_1_1(tmp_path):
    cert, key = make_cert(tmp_path)

    async def go():
        hub, listeners, ports = await hub_on(HubConfig(nmdc_listen="off",
                                                       nmdcs_listen="127.0.0.1:0",
                                                       tls_cert=cert, tls_key=key))
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        try:
            ctx.maximum_version = ssl.TLSVersion.TLSv1_1
            ctx.minimum_version = ssl.TLSVersion.TLSv1_1
        except (ValueError, ssl.SSLError):
            pytest.skip("this OpenSSL cannot offer TLS 1.1")
        with pytest.raises((ssl.SSLError, ConnectionError)):
            await asyncio.open_connection("127.0.0.1", ports["nmdcs"], ssl=ctx)
        await listeners.close(hub)

    run(go())


def test_nmdcs_without_a_certificate_is_skipped():
    async def go():
        hub, listeners, ports = await hub_on(HubConfig(nmdc_listen="127.0.0.1:0",
                                                       nmdcs_listen="127.0.0.1:0"))
        assert set(ports) == {"nmdc"}
        await listeners.close(hub)

    run(go())


@needs_openssl
def test_certificate_reload(tmp_path):
    cert, key = make_cert(tmp_path)
    tls = TlsContext(cert, key)
    assert not tls.reload_if_changed()
    new_cert, new_key = make_cert(tmp_path, "hub2")
    shutil.copy(new_cert, cert)
    shutil.copy(new_key, key)
    import os
    os.utime(cert, ns=(1, 2))  # a different stamp even within the clock's resolution
    assert tls.reload_if_changed()
    open(cert, "w").write("not a certificate")
    os.utime(cert, ns=(3, 4))
    assert not tls.reload_if_changed()  # a bad file keeps the old certificate


def test_capture_records_both_ways(tmp_path):
    async def go():
        hub, listeners, ports = await hub_on(plain(capture_dir=str(tmp_path)))
        c = Client("cap")
        await c.connect("127.0.0.1", ports["nmdc"])
        await c.login()
        await c.close()
        await asyncio.sleep(0.2)
        await listeners.close(hub)

    run(go())
    (log,) = tmp_path.glob("*-nmdc.log")
    text = log.read_text()
    assert text.startswith("# nmdc from 127.0.0.1:")
    assert "> b'$Lock EXTENDEDPROTOCOL_sublevels_" in text
    assert "< b'$ValidateNick cap|'" in text
    assert "# closed:" in text


@needs_openssl
def test_keyprint_matches_openssl_and_follows_reloads(tmp_path, transport):
    """On asyncio the hub reloads the files itself; on the engine (N3) its watch does, and
    on_tls_reload gives the hub the new keyprint."""
    import base64

    from sublevels.nmdc.listener import keyprint_of

    cert, key = make_cert(tmp_path)
    der = subprocess.run(["openssl", "x509", "-in", cert, "-outform", "DER"], check=True,
                         capture_output=True).stdout
    digest = subprocess.run(["openssl", "dgst", "-sha256", "-binary"], input=der, check=True,
                            capture_output=True).stdout
    expect = "SHA256/" + base64.b32encode(digest).decode().rstrip("=")
    assert keyprint_of(cert) == expect and len(expect) == len("SHA256/") + 52

    async def go():
        hub = Hub(HubConfig(nmdc_listen="off", nmdcs_listen="127.0.0.1:0", tls_cert=cert,
                            tls_key=key, nmdcs_address="nmdcs://hub.example:4111",
                            transport=transport))
        listeners = await start_listeners(hub, cert_check_every=0.2)
        on_engine = bool(listeners.engines)
        assert on_engine == (transport == "engine")
        assert hub.keyprint == expect
        assert hub.nmdcs_url() == f"nmdcs://hub.example:4111/?kp={expect}"
        assert hub.stats()["nmdcs_keyprint"] == expect
        new_cert, new_key = make_cert(tmp_path, "hub2")
        shutil.copy(new_cert, cert)
        shutil.copy(new_key, key)
        import os
        os.utime(cert, ns=(5, 6))
        if on_engine:
            for _ in range(50):  # the watch loads it once both files have kept their times
                if hub.keyprint != expect:
                    break
                await asyncio.sleep(0.1)
        else:
            assert listeners.tls.reload_if_changed()
        assert hub.keyprint == keyprint_of(new_cert) != expect
        await listeners.close(hub)

    run(go())


@needs_openssl
def test_tls_required_force_move_carries_the_keyprint(tmp_path):
    cert, key = make_cert(tmp_path)

    async def go():
        hub, listeners, ports = await hub_on(HubConfig(
            nmdc_listen="127.0.0.1:0", nmdcs_listen="127.0.0.1:0", tls_cert=cert, tls_key=key,
            tls_required=True, nmdcs_address="nmdcs://hub.example:4111"))
        c = Client("plain")
        await c.connect("127.0.0.1", ports["nmdc"])
        msgs = [await c.read(), await c.read()]
        assert msgs[1] == f"$ForceMove nmdcs://hub.example:4111/?kp={hub.keyprint}|"
        assert "TLS" in msgs[0]
        await c.close()
        t = Client("secure")
        await t.connect("127.0.0.1", ports["nmdcs"], tls=True)
        await t.login()
        await t.close()
        await listeners.close(hub)

    run(go())


def test_transport_auto_picks_the_engine_where_pycvc_has_it(monkeypatch):
    from sublevels.nmdc import engine
    from sublevels.nmdc.listener import pick_transport

    monkeypatch.setattr(engine, "available", lambda: True)
    assert HubConfig().transport == "auto"
    assert pick_transport(HubConfig()) == "engine"
    assert pick_transport(HubConfig(transport="asyncio")) == "asyncio"  # the fallback setting
    assert pick_transport(HubConfig(capture_dir="/tmp/x")) == "asyncio"  # captures are asyncio's
    monkeypatch.setattr(engine, "available", lambda: False)
    assert pick_transport(HubConfig()) == "asyncio"
    assert pick_transport(HubConfig(transport="engine")) == "engine"  # start_listeners refuses it
    with pytest.raises(ValueError):
        pick_transport(HubConfig(transport="uvloop"))


def test_location_policy_lets_operators_in_with_their_password(transport):
    """D12 on real sockets: 127.0.0.1 cannot be placed, and this hub refuses what it cannot
    place, so only the exempt operator, with its password, gets in."""

    class Accounts:
        def is_registered(self, nick):
            return nick == "op"

        def check(self, nick, password):
            return nick == "op" and password == "pw"

        def is_operator(self, nick):
            return nick == "op"

    async def go():
        cfg = plain(transport, location_policy="allow", location_countries=("DE",),
                    location_unknown="refuse", geoip_db="/nonexistent.mmdb",
                    password_requires_tls=False)
        hub = Hub(cfg, accounts=Accounts())
        listeners = await start_listeners(hub)
        port = {p: n for p, _, n in listeners.addresses()}["nmdc"]
        try:
            op = Client("op")
            await op.connect("127.0.0.1", port)
            await op.login(password="pw")
            alice = Client("alice")
            await alice.connect("127.0.0.1", port)
            with pytest.raises((RuntimeError, ConnectionError, asyncio.IncompleteReadError)):
                await alice.login(timeout=3)
            wrong = Client("op")
            await wrong.connect("127.0.0.1", port)
            with pytest.raises((RuntimeError, ConnectionError, asyncio.IncompleteReadError)):
                await wrong.login(timeout=3, password="nope")
            await asyncio.sleep(0.2)
            return set(hub.users), [a["action"] for a in hub.actions]
        finally:
            await listeners.close(hub)

    users, actions = run(go())
    assert users == {"op"}
    assert "location_exempt" in actions and "refused_location" in actions
