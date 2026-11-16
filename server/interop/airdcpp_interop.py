"""Interop with the AirDC++ web client, run as a black box (M1): its released portable build,
configured with its documented command-line options and driven only through its documented HTTP
API (https://github.com/airdcpp-web/airdcpp-apidocs, the API Blueprint), against the hub run alone
with its capture on. An ncdc (as in ncdc_interop.py) shares files and an observer client
(tools/nmdc_client.py) sits in the hub.

    python interop/airdcpp_interop.py --airdcppd PATH [--ncdc PATH] [--out DIR]

What it checks:
  1. AirDC++ logs in (active mode) and its hub session reaches "connected";
  2. main chat both ways, and a third-person (/me) message;
  3. private messages both ways between AirDC++ and the observer;
  4. a search from AirDC++ reaches the others with its address rewritten, and AirDC++ collects
     ncdc's result (UDP, client to client);
  5. a transfer the hub signals: AirDC++ downloads ncdc's file list, and ncdc downloads
     AirDC++'s;
then it writes what the captures show for spec/'s "To capture" items to DIR/findings.md (the
same report as ncdc_interop.py). AirDC++'s log, the hub's captures and its stats stay in DIR.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(SERVER, "tools"))
sys.path.insert(0, HERE)

from ncdc_interop import Ncdc, check, free_ports, results, seen, write_findings  # noqa: E402
from nmdc_client import Client  # noqa: E402

USER, PASSWORD = "interop", "interop-test-password"  # a test web user for this run only


class Airdcpp:
    """One airdcppd with a config directory of its own, and its HTTP API."""

    def __init__(self, binary: str, root: str, http_port: int):
        self.binary = binary
        self.cfg = os.path.join(root, "airdcpp-config") + "/"
        os.makedirs(self.cfg)
        self.share = os.path.join(root, "share-airdcpp")
        os.makedirs(self.share)
        with open(os.path.join(self.share, "interop-airdcpp.txt"), "w") as f:
            f.write("a file AirDC++ shares for the interop run\n" * 64)
        self.base = f"http://127.0.0.1:{http_port}/api/v1"
        self.auth = "Basic " + base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode()
        # The documented setup commands, answering their prompts on stdin.
        subprocess.run([binary, f"-c={self.cfg}", "--add-user"], input=f"{USER}\n{PASSWORD}\n"
                       f"{PASSWORD}\n", text=True, capture_output=True, check=True, timeout=60)
        subprocess.run([binary, f"-c={self.cfg}", "--configure"], input=f"{http_port}\n0\n",
                       text=True, capture_output=True, check=True, timeout=60)
        self.log = open(os.path.join(root, "airdcppd.log"), "w")
        self.proc = subprocess.Popen([binary, f"-c={self.cfg}", "--no-auto-connect", "--cdm-hub"],
                                     stdout=self.log, stderr=subprocess.STDOUT,
                                     stdin=subprocess.DEVNULL)

    def api(self, method: str, path: str, body=None, timeout: float = 10.0):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Authorization": self.auth,
                                              "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            text = r.read().decode()
            return json.loads(text) if text else None

    def wait_up(self, timeout: float = 60.0) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            try:
                self.api("GET", "/sessions/self")
                return True
            except (urllib.error.URLError, ConnectionError, OSError):
                time.sleep(0.5)
        return False

    def poll(self, fn, timeout: float = 30.0, every: float = 0.5):
        end = time.time() + timeout
        while time.time() < end:
            try:
                v = fn()
                if v:
                    return v
            except urllib.error.HTTPError:
                pass
            time.sleep(every)
        return None

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
            try:
                self.proc.wait(20)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.log.close()


async def run(airdcppd: str, ncdc_bin: str, out: str) -> int:
    root = tempfile.mkdtemp(prefix="sublevels-airdcpp-")
    cap = os.path.join(out, "capture")
    os.makedirs(cap, exist_ok=True)
    nmdc, http, tcp, udp, tls, pn = free_ports(6)
    env = dict(os.environ, SUBLEVELS_NMDC_LISTEN=f"127.0.0.1:{nmdc}", SUBLEVELS_NMDCS_LISTEN="off",
               SUBLEVELS_NMDC_CAPTURE_DIR=cap, SUBLEVELS_HUB_NAME="sublevels interop",
               SUBLEVELS_HUB_TOPIC="AirDC++ run")
    stats_path = os.path.join(out, "hub-stats.json")
    hub_log = open(os.path.join(out, "hub.log"), "w")
    hub = subprocess.Popen([sys.executable, os.path.join(SERVER, "tools", "run_hub.py"),
                            "--stats", stats_path], env=env, stdout=hub_log,
                           stderr=subprocess.STDOUT)
    hub_url = f"127.0.0.1:{nmdc}"  # an address with no scheme is an NMDC hub
    dc = None
    nc = None
    obs = None
    try:
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", nmdc), 0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        obs = Client("observer")
        await obs.connect("127.0.0.1", nmdc)
        await obs.login()

        nc = Ncdc("ncdcA", ncdc_bin, root)
        nc.cmd("/set nick ncdcA")
        for cmd in ("/set active_ip 127.0.0.1", "/set local_address 127.0.0.1",
                    f"/set active_port {pn}", "/set active true"):
            nc.cmd(cmd)
        nc.cmd(f"/share files {nc.share}", 1.0)
        nc.cmd(f"/open plain {hub_url}", 1.0)
        check("login ncdcA", await seen(obs, r"^\$MyINFO \$ALL ncdcA ", 30) is not None)

        dc = Airdcpp(airdcppd, root, http)
        if not check("airdcppd's API answers", dc.wait_up()):
            return 1
        dc.api("POST", "/settings/set", {
            "nick": "airdcpp", "tcp_port": tcp, "udp_port": udp, "tls_port": tls,
            "connection_auto_v4": False, "connection_mode_v4": 0,
            "connection_ip_v4": "127.0.0.1", "connection_mode_v6": -1,
        })
        dc.api("POST", "/share_roots", {"path": dc.share + "/"})
        hubsess = dc.api("POST", "/hubs", {"hub_url": hub_url})
        sid = hubsess["id"]

        # 1. login
        state = dc.poll(lambda: dc.api("GET", f"/hubs/{sid}")["connect_state"]["id"] == "connected"
                        and "connected", 30)
        check("AirDC++ hub session connected", state == "connected",
              dc.api("GET", f"/hubs/{sid}")["connect_state"]["str"])
        m = await seen(obs, r"^\$MyINFO \$ALL airdcpp ", 30)
        check("login airdcpp (the hub lists it)", m is not None, (m or "").strip("|")[:140])

        # 2. main chat
        dc.api("POST", f"/hubs/{sid}/chat_message", {"text": "hello from AirDC++"})
        check("main chat AirDC++ -> everyone",
              await seen(obs, r"^<airdcpp> hello from AirDC\+\+\|$") is not None)
        dc.api("POST", f"/hubs/{sid}/chat_message", {"text": "waves", "third_person": True})
        m = await seen(obs, r"^<airdcpp> .*waves", 10)
        check("third-person message from AirDC++ reaches chat", m is not None,
              (m or "").strip("|"))
        obs.send("<observer> hello AirDC++|")
        got = dc.poll(lambda: any("hello AirDC++" in json.dumps(x)
                                  for x in dc.api("GET", f"/hubs/{sid}/messages/0")), 15)
        check("main chat observer -> AirDC++", bool(got))

        # 3. private messages
        users = dc.poll(lambda: {u["nick"]: u for u in
                                 dc.api("GET", f"/hubs/{sid}/users/0/100")
                                 if u["nick"] in ("observer", "ncdcA")}, 15) or {}
        if check("AirDC++ lists the observer and ncdcA", len(users) == 2, ", ".join(users)):
            dc.api("POST", "/private_chat/chat_message", {
                "user": {"cid": users["observer"]["cid"], "hub_url": hub_url},
                "text": "hi observer"})
            m = await seen(obs, r"^\$To: observer From: airdcpp \$<airdcpp> hi observer\|$")
            check("private message AirDC++ -> observer", m is not None)
            obs.send("$To: airdcpp From: observer $<observer> hi AirDC++|")
            got = dc.poll(lambda: any("hi AirDC++" in json.dumps(dc.api(
                "GET", f"/private_chat/{p['id']}/messages/0")) for p in
                dc.api("GET", "/private_chat")), 15)
            check("private message observer -> AirDC++", bool(got))

        # 4. search
        inst = dc.api("POST", "/search", {"expiration": 5})
        dc.api("POST", f"/search/{inst['id']}/hub_search",
               {"query": {"pattern": "interop-ncdca"}, "priority": 4})
        m = await seen(obs, r"^\$Search .*interop", 30)
        check("search from AirDC++ reaches the hub's users", m is not None, (m or "").strip("|"))
        if m is not None:
            check("the searcher's address is the hub's view of it",
                  m.startswith(f"$Search 127.0.0.1:{udp} ") or m.startswith("$Search Hub:"),
                  m.split(" ")[1])
        res = dc.poll(lambda: dc.api("GET", f"/search/{inst['id']}/results/0/10"), 30)
        check("AirDC++ collects ncdcA's result", bool(res),
              ", ".join(r.get("name", "?") for r in res or []))

        # 5. transfers the hub signals
        if "ncdcA" in users:
            fl = dc.api("POST", "/filelists", {"user": {"cid": users["ncdcA"]["cid"],
                                                       "hub_url": hub_url}})
            state = dc.poll(lambda: dc.api("GET", f"/filelists/{fl['id']}")["state"]["id"]
                            == "loaded" and "loaded", 45)
            check("AirDC++ downloads ncdcA's file list", state == "loaded",
                  dc.api("GET", f"/filelists/{fl['id']}")["state"]["id"])
        dc.api("POST", "/share/refresh", {})
        time.sleep(3)
        nc.cmd("/browse airdcpp", 1.0)
        deadline = time.time() + 45
        while time.time() < deadline and not nc.file_lists():
            await asyncio.sleep(0.5)
        check("ncdcA downloads AirDC++'s file list", bool(nc.file_lists()),
              ", ".join(nc.file_lists()))
    finally:
        if nc is not None:
            nc.stop(out)
        if dc is not None:
            dc.stop()
            shutil.copy(os.path.join(root, "airdcppd.log"), os.path.join(out, "airdcppd.log"))
        if obs is not None:
            await obs.close()
        time.sleep(1.0)
        hub.send_signal(signal.SIGTERM)
        try:
            hub.wait(10)
        except subprocess.TimeoutExpired:
            hub.kill()
        hub_log.close()
        shutil.rmtree(root, ignore_errors=True)

    stats = json.load(open(stats_path)) if os.path.exists(stats_path) else {}
    totals = stats.get("totals", {})
    check("every $Key matched", totals.get("key_mismatch", 0) == 0,
          f"key_mismatch={totals.get('key_mismatch', 0)}")
    write_findings(cap, stats, os.path.join(out, "findings.md"))
    failed = [r for r in results if not r[1]]
    print(f"interop: {len(results) - len(failed)}/{len(results)} passed", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--airdcppd", required=True)
    p.add_argument("--ncdc", default=shutil.which("ncdc") or "ncdc")
    p.add_argument("--out", default="interop-airdcpp-out")
    a = p.parse_args()
    os.makedirs(a.out, exist_ok=True)
    sys.exit(asyncio.run(run(a.airdcppd, a.ncdc, os.path.abspath(a.out))))
