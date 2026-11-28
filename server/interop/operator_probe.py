"""How real clients show an operator's kick, ban and redirect (P6's To capture), as black boxes.

    python interop/operator_probe.py [--airdcppd PATH] [--ncdc PATH] [--out DIR]

Two hubs run (tools/run_hub.py), A and B, each with an operator account. The operator is this
repository's test client, logged in to A with a password generated for the run. For each client:

  1. kick: the operator sends the client a private message, then $Kick. Recorded: what the client
     shows, and whether it comes back by itself within 20 s;
  2. ban: the operator bans its nick for 10 minutes (!ban). Recorded: what it shows, and how many
     times it tries again within 30 s (the hub's refusals: P6 §4 stops answering after the third
     in a minute);
  3. redirect: after an unban, $OpForceMove to hub B. Recorded: whether it goes there by itself
     (AirDC++ reports a redirect state; the probe then follows it through its API).

The checks are the hub's: the kick and the ban close the client, and the redirect is sent. What
each client did is in findings.md, for the spec writer.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(SERVER, "tools"))
sys.path.insert(0, os.path.join(SERVER, "src"))
sys.path.insert(0, HERE)

from ncdc_interop import check, free_ports, results  # noqa: E402
from nmdc_client import Client  # noqa: E402


class Hub:
    def __init__(self, out: str, tag: str, accounts: str):
        self.port, = free_ports(1)
        self.stats_path = os.path.join(out, f"hub-{tag}-stats.json")
        env = dict(os.environ, SUBLEVELS_NMDC_LISTEN=f"127.0.0.1:{self.port}",
                   SUBLEVELS_NMDCS_LISTEN="off", SUBLEVELS_HUBLISTS="off",
                   SUBLEVELS_ACCOUNTS_FILE=accounts, SUBLEVELS_PASSWORD_REQUIRES_TLS="0",
                   SUBLEVELS_HUB_NAME=f"operator probe {tag}",
                   SUBLEVELS_NMDC_CAPTURE_DIR=os.path.join(out, f"capture-{tag}"))
        self.log = open(os.path.join(out, f"hub-{tag}.log"), "w")
        self.proc = subprocess.Popen([sys.executable, os.path.join(SERVER, "tools", "run_hub.py"),
                                      "--stats", self.stats_path], env=env, stdout=self.log,
                                     stderr=subprocess.STDOUT)
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", self.port), 0.2).close()
                break
            except OSError:
                time.sleep(0.1)

    def stats(self) -> dict:
        time.sleep(1.2)  # written every second
        try:
            return json.load(open(self.stats_path))
        except (OSError, ValueError):
            return {}

    def stop(self) -> None:
        self.proc.send_signal(signal.SIGTERM)
        try:
            self.proc.wait(15)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self.log.close()


async def operator(hub: Hub, password: str) -> Client:
    op = Client("hubop")
    await op.connect("127.0.0.1", hub.port)
    await op.login(password=password)
    return op


async def online(op: Client, nick: str, timeout: float) -> bool:
    """Whether ``nick`` logs in (its $MyINFO reaches the operator) within ``timeout``."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            m = await op.read(timeout=1.0)
        except (TimeoutError, asyncio.TimeoutError):
            continue
        if m.startswith(f"$MyINFO $ALL {nick} "):
            return True
    return False


async def probe_airdcpp(airdcppd: str, a: Hub, b: Hub, password: str, root: str) -> list[str]:
    from airdcpp_interop import Airdcpp

    nick = "airdcpp"
    tcp, udp, tls, http = free_ports(4)
    dc = Airdcpp(airdcppd, root, http)
    out = []
    op = None
    try:
        if not check("airdcpp: its API answers", dc.wait_up()):
            return ["- AirDC++: its API did not answer"]
        dc.api("POST", "/settings/set", {
            "nick": nick, "tcp_port": tcp, "udp_port": udp, "tls_port": tls,
            "connection_auto_v4": False, "connection_mode_v4": 0,
            "connection_ip_v4": "127.0.0.1", "connection_mode_v6": -1})
        op = await operator(a, password)
        sid = dc.api("POST", "/hubs", {"hub_url": f"127.0.0.1:{a.port}"})["id"]

        def state():
            h = dc.api("GET", f"/hubs/{sid}")
            return h["connect_state"]["id"], h["connect_state"].get("data") or {}, h["hub_url"]

        def texts(since: int) -> tuple[list[str], int]:
            ms = dc.api("GET", f"/hubs/{sid}/messages/0")
            got = []
            for m in ms[since:]:
                body = m.get("chat_message") or m.get("status_message") or {}
                got.append(str(body.get("text", "")))
            return got, len(ms)

        check("airdcpp: logs in", await online(op, nick, 30))
        _, seen = texts(0)
        # 1. kick
        op.send(f"$To: {nick} From: hubop $<hubop> testing kicks|")
        await asyncio.sleep(0.5)
        op.send(f"$Kick {nick}|")
        await asyncio.sleep(2)
        shown, seen = texts(seen)
        st_after = state()[0]
        back = await online(op, nick, 20)
        check("airdcpp: the kick closes it", a.stats().get("closes", {}).get(
            "kicked by hubop", 0) >= 1)
        out += ["- **AirDC++ 2.14.0, kick** (a PM, then `$Kick`):",
                f"  - shown: {json.dumps(shown)}",
                f"  - its state 2 s later: `{st_after}`; back by itself within 20 s: "
                f"{'yes' if back else 'no'}"]
        if not back:
            dc.api("POST", f"/hubs/{sid}/reconnect", {})
            await online(op, nick, 20)
        # 2. ban
        _, seen = texts(0)
        op.send(f"<hubop> !ban {nick} 10m testing bans|")
        await asyncio.sleep(2)
        shown, seen = texts(seen)
        await asyncio.sleep(28)
        st = a.stats()
        refused = st.get("refusals", {}).get("banned", 0)
        check("airdcpp: the ban closes it", any(k.startswith("banned by hubop")
                                                for k in st.get("closes", {})))
        out += ["- **AirDC++, ban** (`!ban airdcpp 10m`):",
                f"  - shown: {json.dumps(shown)}",
                f"  - refused reconnects in 30 s: {refused} (no line after the third a minute)"]
        # 3. redirect
        op.send(f"<hubop> !unban {nick}|")
        dc.api("POST", f"/hubs/{sid}/reconnect", {})
        check("airdcpp: back after the unban", await online(op, nick, 30))
        op.send(f"$OpForceMove $Who:{nick}$Where:127.0.0.1:{b.port}$Msg:moving you|")
        await asyncio.sleep(3)
        s, data, url = state()
        followed = url.endswith(f":{b.port}") and s == "connected"
        out += ["- **AirDC++, redirect** (`$OpForceMove` to hub B):",
                f"  - its state: `{s}` {json.dumps(data)}; followed by itself: "
                f"{'yes' if followed else 'no'}"]
        if s == "redirect":
            dc.api("POST", f"/hubs/{sid}/redirect", {})
            for _ in range(30):
                s, data, url = state()
                if s == "connected" and url.endswith(f":{b.port}"):
                    break
                await asyncio.sleep(0.5)
            out.append(f"  - after POST /hubs/{{id}}/redirect: `{s}` at `{url}`")
        check("airdcpp: reaches hub B after the redirect",
              s == "connected" and url.endswith(f":{b.port}"), f"{s} {url}")
    finally:
        if op is not None:
            await op.close()
        dc.stop()
    return out


async def probe_ncdc(ncdc_bin: str, a: Hub, b: Hub, password: str, root: str,
                     out_dir: str) -> list[str]:
    from ncdc_interop import Ncdc

    nick = "ncdcK"
    nc = Ncdc(nick, ncdc_bin, root)
    out = []
    op = None
    try:
        nc.cmd(f"/set nick {nick}")
        nc.cmd("/set local_address 127.0.0.1")
        nc.cmd("/set active false")
        op = await operator(a, password)
        nc.cmd(f"/open probe 127.0.0.1:{a.port}", 1.0)
        check("ncdc: logs in", await online(op, nick, 30))
        op.send(f"$To: {nick} From: hubop $<hubop> testing kicks|")
        await asyncio.sleep(0.5)
        op.send(f"$Kick {nick}|")
        back = await online(op, nick, 20)
        check("ncdc: the kick closes it", a.stats().get("closes", {}).get(
            "kicked by hubop", 0) >= 1)
        kicked_screen = "\n".join(line for line in nc.screen().splitlines()
                                  if "kick" in line.lower() or "disconnect" in line.lower())[-800:]
        out += ["- **ncdc, kick:**", f"  - back by itself within 20 s: {'yes' if back else 'no'}",
                "  - its screen's lines on it:", "", "```", kicked_screen, "```"]
        if not back:
            nc.cmd("/reconnect", 1.0)
            await online(op, nick, 20)
        op.send(f"<hubop> !ban {nick} 10m testing bans|")
        await asyncio.sleep(30)
        refused = a.stats().get("refusals", {}).get("banned", 0)
        out += [f"- **ncdc, ban:** refused reconnects in 30 s: {refused}"]
        op.send(f"<hubop> !unban {nick}|")
        nc.cmd("/reconnect", 1.0)
        if await online(op, nick, 30):
            op.send(f"$OpForceMove $Who:{nick}$Where:127.0.0.1:{b.port}$Msg:moving you|")
            await asyncio.sleep(5)
            screen = "\n".join(line for line in nc.screen().splitlines()
                               if "redirect" in line.lower() or str(b.port) in line)[-600:]
            out += ["- **ncdc, redirect:**", "", "```", screen, "```"]
    finally:
        if op is not None:
            await op.close()
        nc.stop(out_dir)
    return out


async def run(airdcppd: str, ncdc_bin: str, out: str) -> int:
    root = tempfile.mkdtemp(prefix="sublevels-operator-probe-")
    password = secrets.token_urlsafe(12)  # this run's operator only
    from sublevels.nmdc.accounts import hash_password
    accounts = os.path.join(root, "accounts.json")
    with open(accounts, "w") as f:
        json.dump({"accounts": {"hubop": {"password": hash_password(password), "role": "op"}}},
                  f)
    findings = ["# How clients show an operator's kick, ban and redirect (P6 To capture)", ""]
    for name, fn in (("airdcpp", probe_airdcpp if airdcppd else None),
                     ("ncdc", probe_ncdc if ncdc_bin else None)):
        if fn is None:
            continue
        a, b = Hub(out, f"{name}-A", accounts), Hub(out, f"{name}-B", accounts)
        try:
            if name == "airdcpp":
                findings += await fn(airdcppd, a, b, password, os.path.join(root, name))
            else:
                findings += await fn(ncdc_bin, a, b, password, os.path.join(root, name), out)
        finally:
            a.stop()
            b.stop()
    failed = [r for r in results if not r[1]]
    findings += ["", f"Checks: {len(results) - len(failed)}/{len(results)} passed."]
    with open(os.path.join(out, "findings.md"), "w") as f:
        f.write("\n".join(findings) + "\n")
    print("\n".join(findings), flush=True)
    shutil.rmtree(root, ignore_errors=True)
    return 1 if failed else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--airdcppd", default="")
    ap.add_argument("--ncdc", default="")
    ap.add_argument("--out", default="operator-probe-out")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    sys.exit(asyncio.run(run(a.airdcppd, a.ncdc, os.path.abspath(a.out))))
