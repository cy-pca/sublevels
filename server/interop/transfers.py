"""End-to-end file transfers through the hub, with real clients as black boxes (M1): users see
each other and download each other's files, over plain NMDC or over NMDCS.

    python interop/transfers.py --airdcppd PATH [--ncdc PATH] [--tls] [--out DIR]

The hub runs alone (tools/run_hub.py) with a plain and a TLS listener and its capture on. Three
users share one file each (1 MiB of random bytes, named after them):

  ncdcA    ncdc, active
  ncdcC    ncdc, passive
  airdcpp  the AirDC++ web client, active

With --tls every client connects to nmdcs://…/?kp=<the hub's keyprint> and the hub refuses plain
connections (tls_required, with a $ForceMove to the NMDCS address); without it, all use plain
NMDC. Checks:
  1. every client logs in, and each one's user list shows the other two;
  2. five downloads, each found by search, each file compared by SHA-256 with its source:
       airdcpp <- ncdcA   (active <- active: $ConnectToMe)
       airdcpp <- ncdcC   (active <- passive: $ConnectToMe, ncdcC connects out)
       ncdcA   <- airdcpp (active <- active)
       ncdcC   <- airdcpp (passive <- active: $RevConnectToMe, then $ConnectToMe)
       ncdcA   <- ncdcC   (active <- passive)
  3. with --tls: AirDC++ refuses a wrong keyprint (keyprint_mismatch), and a plain AirDC++ session
     is redirected by $ForceMove and follows it to NMDCS.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
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
sys.path.insert(0, HERE)

from airdcpp_interop import Airdcpp  # noqa: E402
from ncdc_interop import Ncdc, check, free_ports, results, write_findings  # noqa: E402

SIZE = 1 << 20


def payload(share: str, nick: str) -> tuple[str, str]:
    """A file of random bytes in ``share``: (its name, its SHA-256)."""
    name = f"{nick.lower()}-payload.bin"
    data = os.urandom(SIZE)
    with open(os.path.join(share, name), "wb") as f:
        f.write(data)
    return name, hashlib.sha256(data).hexdigest()


def find_file(root: str, name: str) -> str | None:
    for d, _, files in os.walk(root):
        if name in files:
            return os.path.join(d, name)
    return None


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


async def wait_file(root: str, name: str, digest: str, timeout: float) -> tuple[bool, str]:
    """Whether ``name`` arrives complete under ``root`` within ``timeout`` with this SHA-256."""
    end = time.time() + timeout
    while time.time() < end:
        p = find_file(root, name)
        if p is not None and os.path.getsize(p) == SIZE:
            got = sha256_of(p)
            return got == digest, got[:16]
        await asyncio.sleep(0.5)
    return False, "not downloaded"


def ncdc_download(n: Ncdc, name: str) -> bool:
    """Search for ``name`` from ncdc and queue the first result ('d' in the results tab)."""
    n.cmd(f"/search -all {name}", 1.0)
    if not n.wait_screen(name.replace(".", r"\."), 30):
        return False
    subprocess.run(["tmux", "send-keys", "-t", n.session, "d"], check=True)
    time.sleep(0.5)
    close_tab(n)
    return True


def close_tab(n: Ncdc) -> None:
    """Alt+c: close the current tab (a list tab takes no commands), back to the hub tab."""
    subprocess.run(["tmux", "send-keys", "-t", n.session, "M-c"], check=True)
    time.sleep(0.5)


def dc_download(dc: Airdcpp, name: str) -> str:
    inst = dc.api("POST", "/search", {"expiration": 5})
    res = None
    for _ in range(4):  # the sharer may still be hashing; the hub allows a search per 10 s
        dc.api("POST", f"/search/{inst['id']}/hub_search",
               {"query": {"pattern": name.rsplit(".", 1)[0], "file_type": "file"},
                "priority": 4})
        res = dc.poll(lambda: [r for r in dc.api("GET", f"/search/{inst['id']}/results/0/20")
                               if r.get("name") == name], 11)
        if res:
            break
    if not res:
        return "no search result"
    out = dc.api("POST", f"/search/{inst['id']}/results/{res[0]['id']}/download", {})
    return json.dumps(out)[:120]


async def run(airdcppd: str, ncdc_bin: str, tls: bool, out: str) -> int:
    root = tempfile.mkdtemp(prefix="sublevels-transfers-")
    cap = os.path.join(out, "capture")
    os.makedirs(cap, exist_ok=True)
    nmdc, nmdcs, http, tcp, udp, tlsp, pa, pc = free_ports(8)
    cert, key = os.path.join(root, "hub.crt"), os.path.join(root, "hub.key")
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                    "-subj", "/CN=127.0.0.1", "-keyout", key, "-out", cert], check=True,
                   capture_output=True)
    env = dict(os.environ, SUBLEVELS_NMDC_LISTEN=f"127.0.0.1:{nmdc}",
               SUBLEVELS_NMDCS_LISTEN=f"127.0.0.1:{nmdcs}", SUBLEVELS_TLS_CERT=cert,
               SUBLEVELS_TLS_KEY=key, SUBLEVELS_NMDC_CAPTURE_DIR=cap,
               SUBLEVELS_HUB_NAME="sublevels transfers",
               SUBLEVELS_NMDCS_ADDRESS=f"nmdcs://127.0.0.1:{nmdcs}")
    if tls:
        env["SUBLEVELS_TLS_REQUIRED"] = "1"
    stats_path = os.path.join(out, "hub-stats.json")
    hub_log = open(os.path.join(out, "hub.log"), "w")
    hub = subprocess.Popen([sys.executable, os.path.join(SERVER, "tools", "run_hub.py"),
                            "--stats", stats_path], env=env, stdout=hub_log,
                           stderr=subprocess.STDOUT)
    clients: list[Ncdc] = []
    dc = None
    try:
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", nmdc), 0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        kp = ""
        for _ in range(50):
            if os.path.exists(stats_path):
                kp = json.load(open(stats_path)).get("nmdcs_keyprint", "")
                if kp:
                    break
            time.sleep(0.2)
        check("the hub publishes its NMDCS keyprint", kp.startswith("SHA256/"), kp)
        plain_url = f"127.0.0.1:{nmdc}"
        url = f"nmdcs://127.0.0.1:{nmdcs}/?kp={kp}" if tls else plain_url
        print(f"transfers: hub at {url}", flush=True)

        a, c = Ncdc("ncdcA", ncdc_bin, root), Ncdc("ncdcC", ncdc_bin, root)
        clients = [a, c]
        files = {}
        for n, port, active in ((a, pa, True), (c, pc, False)):
            n.cmd(f"/set nick {n.name}")
            n.cmd(f"/set download_dir {os.path.join(root, 'dl-' + n.name)}")
            n.cmd("/set local_address 127.0.0.1")
            if active:
                n.cmd("/set active_ip 127.0.0.1")
                n.cmd(f"/set active_port {port}")
                n.cmd("/set active true")
            else:
                n.cmd("/set active false")
            for old in os.listdir(n.share):
                os.remove(os.path.join(n.share, old))
            files[n.name] = payload(n.share, n.name)
            n.cmd(f"/share files {n.share}", 1.0)
            n.cmd(f"/open hub {url}", 1.5)

        dc = Airdcpp(airdcppd, root, http)
        if not check("airdcppd's API answers", dc.wait_up()):
            return 1
        dc_dl = os.path.join(root, "dl-airdcpp")
        os.makedirs(dc_dl)
        for old in os.listdir(dc.share):
            os.remove(os.path.join(dc.share, old))
        files["airdcpp"] = payload(dc.share, "airdcpp")
        dc.api("POST", "/settings/set", {
            "nick": "airdcpp", "tcp_port": tcp, "udp_port": udp, "tls_port": tlsp,
            "connection_auto_v4": False, "connection_mode_v4": 0,
            "connection_ip_v4": "127.0.0.1", "connection_mode_v6": -1,
            "download_directory": dc_dl + "/"})
        dc.api("POST", "/share_roots", {"path": dc.share + "/"})
        sid = dc.api("POST", "/hubs", {"hub_url": url})["id"]

        # 1. login, and who sees whom
        state = dc.poll(lambda: dc.api("GET", f"/hubs/{sid}")["connect_state"]["id"]
                        == "connected" and "connected", 30)
        check("airdcpp logs in", state == "connected",
              dc.api("GET", f"/hubs/{sid}")["connect_state"]["str"])
        seen = dc.poll(lambda: {u["nick"] for u in dc.api("GET", f"/hubs/{sid}/users/0/100")}
                       >= {"ncdcA", "ncdcC"} and True, 30)
        check("airdcpp sees ncdcA and ncdcC", bool(seen))
        # AirDC++ hashes its share before it can answer searches: wait until its own share search
        # finds the file. (ncdc's user lists are checked at the end: its user list tab takes no
        # commands.)
        dc.api("POST", "/share/refresh", {})
        indexed = dc.poll(lambda: dc.api("POST", "/share/search", {
            "query": {"pattern": "airdcpp-payload"}}), 60)
        check("airdcpp has hashed its share", bool(indexed))
        time.sleep(3)  # and ncdc its own

        # 2. downloads, each checked by SHA-256
        name, digest = files["ncdcA"]
        detail = dc_download(dc, name)
        ok, got = await wait_file(dc_dl, name, digest, 60)
        check("airdcpp <- ncdcA (active <- active)", ok, got if ok else detail)
        name, digest = files["ncdcC"]
        detail = dc_download(dc, name)
        ok, got = await wait_file(dc_dl, name, digest, 60)
        check("airdcpp <- ncdcC (active <- passive)", ok, got if ok else detail)
        name, digest = files["airdcpp"]
        ncdc_download(a, name)
        ok, got = await wait_file(os.path.join(root, "dl-ncdcA"), name, digest, 60)
        check("ncdcA <- airdcpp (active <- active)", ok, got)
        ncdc_download(c, name)
        ok, got = await wait_file(os.path.join(root, "dl-ncdcC"), name, digest, 60)
        check("ncdcC <- airdcpp (passive <- active, $RevConnectToMe)", ok, got)
        name, digest = files["ncdcC"]
        ncdc_download(a, name)
        ok, got = await wait_file(os.path.join(root, "dl-ncdcA"), name, digest, 60)
        check("ncdcA <- ncdcC (active <- passive)", ok, got)

        for n in clients:  # who sees whom, from ncdc's user list tab
            n.cmd("/userlist", 1.0)
            others = [m for m in ("ncdcA", "ncdcC", "airdcpp") if m != n.name]
            ok = all(n.wait_screen(rf"\b{o}\b", 20) for o in others)
            check(f"{n.name} sees {' and '.join(others)}", ok)
            close_tab(n)

        # 3. NMDCS: the keyprint is enforced, and a plain session is moved to NMDCS
        if tls:
            dc.api("DELETE", f"/hubs/{sid}")
            time.sleep(1.0)
            bad = "SHA256/" + "A" * 52
            before = json.load(open(stats_path))["totals"].get("logins", 0)
            sid = dc.api("POST", "/hubs", {"hub_url": f"nmdcs://127.0.0.1:{nmdcs}/?kp={bad}"})["id"]
            state = dc.poll(lambda: dc.api("GET", f"/hubs/{sid}")["connect_state"]["id"]
                            in ("keyprint_mismatch", "disconnected") and
                            dc.api("GET", f"/hubs/{sid}")["connect_state"], 20)
            time.sleep(2.0)
            after = json.load(open(stats_path))["totals"].get("logins", 0)
            # Refused means no login: AirDC++ drops the handshake once the certificate's keyprint
            # does not match, so the hub never sees the session.
            check("airdcpp refuses a wrong keyprint", bool(state) and after == before,
                  f"state {json.dumps(state)[:120]}, hub logins {before} -> {after}")
            dc.api("DELETE", f"/hubs/{sid}")
            time.sleep(1.0)
            sid = dc.api("POST", "/hubs", {"hub_url": plain_url})["id"]
            # The hub answers a plain connection with $ForceMove to the NMDCS address (with its
            # keyprint). AirDC++ shows that as the 'redirect' state, which the API follows; it may
            # also be brief. Either way the end state is a connected NMDCS session.
            seen_states = []

            def nmdcs_connected():
                hs = dc.api("GET", "/hubs")
                for h in hs:
                    st = h["connect_state"]
                    if not seen_states or seen_states[-1] != st["id"]:
                        seen_states.append(st["id"])
                    if st["id"] == "redirect":
                        check("the plain session gets $ForceMove (redirect state, to nmdcs://)",
                              st.get("data", {}).get("hub_url", "").startswith("nmdcs://"),
                              st.get("data", {}).get("hub_url", ""))
                        dc.api("POST", f"/hubs/{h['id']}/redirect")
                return [h["hub_url"] for h in hs if h["connect_state"]["id"] == "connected"
                        and h["hub_url"].startswith("nmdcs://")]

            ok = dc.poll(nmdcs_connected, 40, every=0.1)
            check("airdcpp ends up on NMDCS after the hub's $ForceMove", bool(ok),
                  f"{ok}; states seen: {' -> '.join(seen_states)}")
    finally:
        for n in clients:
            n.stop(out)
        if dc is not None:
            dc.stop()
            shutil.copy(os.path.join(root, "airdcppd.log"), os.path.join(out, "airdcppd.log"))
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
    check("the hub signalled $ConnectToMe and $RevConnectToMe",
          totals.get("ctm", 0) > 0 and totals.get("rctm", 0) > 0,
          f"ctm={totals.get('ctm', 0)} rctm={totals.get('rctm', 0)}")
    if tls:
        plain = totals.get("connections.nmdc", 0)
        check("every plain connection was turned away (tls_required)",
              plain == stats.get("refusals", {}).get("tls_required", 0),
              f"plain connections {plain} (the redirect test's), refused "
              f"{stats.get('refusals', {}).get('tls_required', 0)}; "
              f"nmdcs={totals.get('connections.nmdcs', 0)}")
    write_findings(cap, stats, os.path.join(out, "findings.md"))
    failed = [r for r in results if not r[1]]
    print(f"transfers: {len(results) - len(failed)}/{len(results)} passed", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--airdcppd", required=True)
    p.add_argument("--ncdc", default=shutil.which("ncdc") or "ncdc")
    p.add_argument("--tls", action="store_true", help="every client over NMDCS; the hub TLS-only")
    p.add_argument("--out", default="transfers-out")
    a = p.parse_args()
    os.makedirs(a.out, exist_ok=True)
    sys.exit(asyncio.run(run(a.airdcppd, a.ncdc, a.tls, os.path.abspath(a.out))))
