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


def plain(**kw) -> HubConfig:
    return HubConfig(nmdc_listen="127.0.0.1:0", nmdcs_listen="off", **kw)


def test_two_clients_chat_pm_search_and_ctm():
    async def go():
        hub, listeners, ports = await hub_on(plain())
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


def test_message_too_long_closes():
    async def go():
        hub, listeners, ports = await hub_on(plain(limits=Limits(login_message_max=64)))
        c = Client("x")
        await c.connect("127.0.0.1", ports["nmdc"])
        await c.read()  # $Lock
        c.send(b"$Supports " + b"A" * 100)  # no '|' yet: the partial message is already too long
        with pytest.raises((asyncio.IncompleteReadError, ConnectionError)):
            while True:
                await c.read()
        await listeners.close(hub)
        assert hub.counters.drops["too_long"] == 1

    run(go())


def test_slow_reader_is_closed():
    async def go():
        hub, listeners, ports = await hub_on(plain(limits=Limits(output_queue_max=2048)))
        a = Client("alice")
        await a.connect("127.0.0.1", ports["nmdc"])
        await a.login()
        s = hub.users["alice"]
        s.conn.write(b"x" * 4096)  # more than the queue may hold in one turn
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
def test_nmdcs_login(tmp_path):
    cert, key = make_cert(tmp_path)

    async def go():
        hub, listeners, ports = await hub_on(HubConfig(nmdc_listen="off",
                                                       nmdcs_listen="127.0.0.1:0",
                                                       tls_cert=cert, tls_key=key))
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
