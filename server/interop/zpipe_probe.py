"""Which ZPipe0 framing a real client takes (P7 §3's To capture), with AirDC++ as a black box.

    python interop/zpipe_probe.py [--airdcppd PATH] [--eiskaltdcpp-driver PATH] [--out DIR]

[NMDC] writes ZPipe0 as ``$ZOn blob|``, which leaves open whether the ``|`` comes right after
``$ZOn`` (then the zlib stream) or after the stream. For each framing the hub (tools/run_hub.py)
compresses its login user list to clients that list ZPipe0. AirDC++ and EiskaltDC++ list it
(EiskaltDC++ through its JSON-lines driver, eiskaltdcpp-interop). With 40 users logged
in first, AirDC++ logs in, and the probe asks its API who it sees. Then AirDC++ sends a main-chat
line, so the probe also checks that plain messages work again after the stream. A framing passes if
AirDC++ sees every user and its line arrives. The hub's counters say whether a block was sent.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(SERVER, "tools"))
sys.path.insert(0, HERE)

from airdcpp_interop import Airdcpp  # noqa: E402
from ncdc_interop import check, free_ports, results  # noqa: E402
from nmdc_client import Client  # noqa: E402

USERS = 40
FRAMINGS = ("zon", "zon-blob")


async def hub_with_users(framing: str, out: str, tag: str):
    """The hub (run_hub.py) set to ``framing``, with USERS test clients logged in."""
    nmdc, = free_ports(1)
    stats_path = os.path.join(out, f"hub-stats-{tag}-{framing}.json")
    env = dict(os.environ, SUBLEVELS_NMDC_LISTEN=f"127.0.0.1:{nmdc}",
               SUBLEVELS_NMDCS_LISTEN="off", SUBLEVELS_ZPIPE=framing, SUBLEVELS_ZPIPE_MIN="0",
               SUBLEVELS_HUBLISTS="off",
               SUBLEVELS_NMDC_CAPTURE_DIR=os.path.join(out, f"{tag}-{framing}"))
    log = open(os.path.join(out, f"hub-{tag}-{framing}.log"), "w")
    hub = subprocess.Popen([sys.executable, os.path.join(SERVER, "tools", "run_hub.py"),
                            "--stats", stats_path], env=env, stdout=log, stderr=subprocess.STDOUT)
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", nmdc), 0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    clients = []
    for i in range(USERS):
        c = Client(f"user{i:02d}")
        c.share = 1000 + i
        await c.connect("127.0.0.1", nmdc)
        await c.login()
        clients.append(c)
    return hub, log, nmdc, stats_path, clients


async def heard(c: Client, line: str, timeout: float = 10.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            if await c.read(timeout=1.0) == line:
                return True
        except (TimeoutError, asyncio.TimeoutError):
            continue
    return False


async def stop_hub(hub, log, clients) -> None:
    for c in clients:
        await c.close()
    hub.send_signal(signal.SIGTERM)
    try:
        hub.wait(15)
    except subprocess.TimeoutExpired:
        hub.kill()
    log.close()


async def one_eiskalt(driver: str, framing: str, out: str) -> dict:
    from eiskaltdcpp_interop import Driver

    got = {"client": "eiskaltdcpp", "framing": framing}
    hub, log, nmdc, stats_path, clients = await hub_with_users(framing, out, "eiskalt")
    root = tempfile.mkdtemp(prefix=f"sublevels-zpipe-ei-{framing}-")
    d = Driver(driver, "eiskalt", root, log_dir=out)
    try:
        r = d.wait(lambda e: e.get("event") in ("ready", "error"), 300)
        if not r or r["event"] != "ready":
            got["error"] = json.dumps(r)
            return got
        d.send(op="connect", url=f"127.0.0.1:{nmdc}")
        e = d.wait(lambda e: e.get("event") == "hub" and e.get("state") in ("connected",
                                                                            "error"), 30)
        got["connected"] = bool(e and e["state"] == "connected")
        time.sleep(2)
        d.send(op="users")
        u = d.wait(lambda e: e.get("event") == "users", 10)
        got["users_seen"] = len(set(u["nicks"] if u else []) & {c.nick for c in clients})
        d.send(op="say", text=f"after the stream ({framing})")
        got["chat_after"] = await heard(clients[0], f"<eiskalt> after the stream ({framing})|")
    finally:
        d.quit()
        time.sleep(1.2)
        try:
            got["zpipe_blocks"] = json.load(open(stats_path))["totals"].get("zpipe_blocks", 0)
        except (OSError, ValueError, KeyError):
            pass
        await stop_hub(hub, log, clients)
    return got


async def one(airdcppd: str, framing: str, out: str) -> dict:
    root = tempfile.mkdtemp(prefix=f"sublevels-zpipe-{framing}-")
    nmdc, http, tcp, udp, tlsp = free_ports(5)
    stats_path = os.path.join(out, f"hub-stats-{framing}.json")
    env = dict(os.environ, SUBLEVELS_NMDC_LISTEN=f"127.0.0.1:{nmdc}",
               SUBLEVELS_NMDCS_LISTEN="off", SUBLEVELS_ZPIPE=framing, SUBLEVELS_ZPIPE_MIN="0",
               SUBLEVELS_HUBLISTS="off", SUBLEVELS_NMDC_CAPTURE_DIR=os.path.join(out, framing))
    hub_log = open(os.path.join(out, f"hub-{framing}.log"), "w")
    hub = subprocess.Popen([sys.executable, os.path.join(SERVER, "tools", "run_hub.py"),
                            "--stats", stats_path], env=env, stdout=hub_log,
                           stderr=subprocess.STDOUT)
    clients: list[Client] = []
    dc = None
    got = {"framing": framing}
    try:
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", nmdc), 0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        for i in range(USERS):
            c = Client(f"user{i:02d}")
            c.share = 1000 + i
            await c.connect("127.0.0.1", nmdc)
            await c.login()
            clients.append(c)
        dc = Airdcpp(airdcppd, root, http)
        if not dc.wait_up():
            got["error"] = "airdcppd's API did not answer"
            return got
        dc.api("POST", "/settings/set", {
            "nick": "airdcpp", "tcp_port": tcp, "udp_port": udp, "tls_port": tlsp,
            "connection_auto_v4": False, "connection_mode_v4": 0,
            "connection_ip_v4": "127.0.0.1", "connection_mode_v6": -1})
        sid = dc.api("POST", "/hubs", {"hub_url": f"127.0.0.1:{nmdc}"})["id"]
        state = dc.poll(lambda: dc.api("GET", f"/hubs/{sid}")["connect_state"]["id"]
                        == "connected" and "connected", 30)
        got["connected"] = state == "connected"
        time.sleep(2)
        nicks = {u["nick"] for u in dc.api("GET", f"/hubs/{sid}/users/0/500")}
        got["users_seen"] = len(nicks & {c.nick for c in clients})
        dc.api("POST", f"/hubs/{sid}/chat_message", {"text": f"after the stream ({framing})"})
        heard = False
        end = time.monotonic() + 10
        while time.monotonic() < end and not heard:
            try:
                m = await clients[0].read(timeout=1.0)
            except (TimeoutError, asyncio.TimeoutError):
                continue
            heard = m == f"<airdcpp> after the stream ({framing})|"
        got["chat_after"] = heard
        got["still_connected"] = dc.api("GET", f"/hubs/{sid}")["connect_state"]["id"] == \
            "connected"
    except Exception as e:  # the client's API failing is a finding too
        got["error"] = f"{type(e).__name__}: {e}"
        got["airdcppd_exited"] = dc.exited(2.0) if dc is not None else None
    finally:
        time.sleep(1.2)
        try:
            got["zpipe_blocks"] = json.load(open(stats_path))["totals"].get("zpipe_blocks", 0)
        except (OSError, ValueError, KeyError):
            pass
        for c in clients:
            await c.close()
        if dc is not None:
            dc.stop()
        hub.send_signal(signal.SIGTERM)
        try:
            hub.wait(15)
        except subprocess.TimeoutExpired:
            hub.kill()
        hub_log.close()
    return got


async def run(airdcppd: str, out: str, driver: str = "") -> int:
    findings = ["# ZPipe0: which framing each client takes", ""]
    runs = [(f, "airdcpp") for f in FRAMINGS if airdcppd] + \
        [(f, "eiskaltdcpp") for f in FRAMINGS if driver]
    for framing, client in runs:
        g = await (one(airdcppd, framing, out) if client == "airdcpp"
                   else one_eiskalt(driver, framing, out))
        g.setdefault("client", client)
        ok = (g.get("connected") and g.get("users_seen") == USERS and g.get("chat_after")
              and g.get("zpipe_blocks", 0) >= 1)
        form = "$ZOn| then the stream" if framing == "zon" else "$ZOn <stream>|"
        findings.append(f"- {client}, `{framing}` ({form}): {'taken' if ok else 'not taken'}: "
                        f"`{json.dumps(g)}`")
        print(f"zpipe: {client} {framing}: {'taken' if ok else 'not taken'}: {json.dumps(g)}",
              flush=True)
        g["taken"] = bool(ok)
        results.append((framing, bool(ok), json.dumps(g)))
    with open(os.path.join(out, "findings.md"), "w") as f:
        f.write("\n".join(findings) + "\n")
    # The hub's framing (SUBLEVELS_ZPIPE=on) is zon: a client that stops taking it is a failure.
    return 0 if all(ok for name, ok, _ in results if name == "zon") else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--airdcppd", default="")
    ap.add_argument("--eiskaltdcpp-driver", default="")
    ap.add_argument("--out", default="zpipe-out")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    sys.exit(asyncio.run(run(a.airdcppd, os.path.abspath(a.out), a.eiskaltdcpp_driver)))
