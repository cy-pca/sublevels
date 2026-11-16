"""Interop with ncdc, run as a black box (M1): ncdc as packaged by the distribution, driven only
through its documented interactive commands (ncdc(1)) in tmux, against the hub run alone
(tools/run_hub.py) with its capture on. An observer client (tools/nmdc_client.py) sits in the hub
to see what the hub sends.

    python interop/ncdc_interop.py [--ncdc PATH] [--out DIR]

What it checks:
  1. login: ncdcA (active, plain NMDC), ncdcB (active, NMDCS with a self-signed certificate) and
     ncdcC (passive, plain) appear in the user list;
  2. main chat from ncdcA reaches the observer;
  3. private messages both ways between the observer and ncdcA;
  4. a search from ncdcB reaches the others with its address rewritten, and ncdcB shows ncdcA's
     result (UDP, client to client);
  4. a transfer the hub signals: ncdcB (active) downloads ncdcA's file list ($ConnectToMe), and
     ncdcC (passive) does too ($RevConnectToMe, then ncdcA's $ConnectToMe);
then it writes what the captures show for spec/'s "To capture" items to DIR/findings.md.
The hub's captures, its stats and the ncdc screens are left in DIR.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
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

from nmdc_client import Client  # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append((name, ok, detail))
    print(f"interop: {'PASS' if ok else 'FAIL'} {name}{': ' + detail if detail else ''}",
          flush=True)
    return ok


def free_ports(n: int) -> list[int]:
    socks = [socket.socket() for _ in range(n)]
    for s in socks:
        s.bind(("127.0.0.1", 0))
    ports = [s.getsockname()[1] for s in socks]
    for s in socks:
        s.close()
    return ports


class Ncdc:
    """One ncdc in a tmux session of its own."""

    def __init__(self, name: str, binary: str, root: str):
        self.name = name
        self.session = f"interop-{name}-{os.getpid()}"
        self.dir = os.path.join(root, name)
        self.share = os.path.join(root, f"share-{name}")
        os.makedirs(self.dir)
        os.makedirs(self.share)
        with open(os.path.join(self.share, f"interop-{name.lower()}.txt"), "w") as f:
            f.write(f"a file {name} shares for the interop run\n" * 64)
        subprocess.run(["tmux", "new-session", "-d", "-s", self.session, "-x", "200", "-y", "50",
                        f"env NCDC_DIR={self.dir} TERM=xterm-256color {binary}"], check=True)
        time.sleep(1.0)

    def cmd(self, line: str, pause: float = 0.3) -> None:
        subprocess.run(["tmux", "send-keys", "-t", self.session, "-l", line], check=True)
        subprocess.run(["tmux", "send-keys", "-t", self.session, "Enter"], check=True)
        time.sleep(pause)

    def screen(self) -> str:
        return subprocess.run(["tmux", "capture-pane", "-p", "-t", self.session, "-S", "-3000"],
                              capture_output=True, text=True).stdout

    def wait_screen(self, pattern: str, timeout: float = 20.0) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            if re.search(pattern, self.screen()):
                return True
            time.sleep(0.3)
        return False

    def file_lists(self) -> list[str]:
        fl = os.path.join(self.dir, "fl")
        return sorted(os.listdir(fl)) if os.path.isdir(fl) else []

    def stop(self, out: str) -> None:
        with open(os.path.join(out, f"screen-{self.name}.txt"), "w") as f:
            f.write(self.screen())
        subprocess.run(["tmux", "send-keys", "-t", self.session, "-l", "/quit"])
        subprocess.run(["tmux", "send-keys", "-t", self.session, "Enter"])
        time.sleep(0.5)
        subprocess.run(["tmux", "kill-session", "-t", self.session], capture_output=True)


async def observe(port: int, nicks: list[str], timeout: float) -> Client:
    obs = Client("observer", supports="NoHello NoGetINFO UserIP2 BotList HubTopic")
    await obs.connect("127.0.0.1", port)
    await obs.login()
    return obs


async def seen(obs: Client, pattern: str, timeout: float = 20.0) -> str | None:
    rx = re.compile(pattern)
    for m in obs.received:
        if rx.search(m):
            return m
    try:
        return await obs.read_until(lambda m: rx.search(m) is not None, timeout)
    except (TimeoutError, asyncio.TimeoutError):
        return None


async def run(ncdc_bin: str, out: str) -> int:
    root = tempfile.mkdtemp(prefix="sublevels-interop-")
    cap = os.path.join(out, "capture")
    os.makedirs(cap, exist_ok=True)
    nmdc, nmdcs, pa, pb = free_ports(4)
    cert, key = os.path.join(root, "hub.crt"), os.path.join(root, "hub.key")
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                    "-subj", "/CN=interop", "-keyout", key, "-out", cert], check=True,
                   capture_output=True)
    env = dict(os.environ, SUBLEVELS_NMDC_LISTEN=f"127.0.0.1:{nmdc}",
               SUBLEVELS_NMDCS_LISTEN=f"127.0.0.1:{nmdcs}", SUBLEVELS_TLS_CERT=cert,
               SUBLEVELS_TLS_KEY=key, SUBLEVELS_NMDC_CAPTURE_DIR=cap,
               SUBLEVELS_HUB_NAME="sublevels interop", SUBLEVELS_HUB_TOPIC="ncdc run")
    stats_path = os.path.join(out, "hub-stats.json")
    hub_log = open(os.path.join(out, "hub.log"), "w")
    hub = subprocess.Popen([sys.executable, os.path.join(SERVER, "tools", "run_hub.py"),
                            "--stats", stats_path], env=env, stdout=hub_log,
                           stderr=subprocess.STDOUT)
    clients: list[Ncdc] = []
    obs = None
    try:
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", nmdc), 0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        obs = await observe(nmdc, [], 10)

        a = Ncdc("ncdcA", ncdc_bin, root)
        b = Ncdc("ncdcB", ncdc_bin, root)
        c = Ncdc("ncdcC", ncdc_bin, root)
        clients = [a, b, c]
        for n, port in ((a, pa), (b, pb)):
            n.cmd(f"/set nick {n.name}")
            n.cmd("/set active_ip 127.0.0.1")
            n.cmd("/set local_address 127.0.0.1")
            n.cmd(f"/set active_port {port}")
            n.cmd("/set active true")
        c.cmd("/set nick ncdcC")
        c.cmd("/set active false")
        for n in clients:
            n.cmd(f"/share files {n.share}", 1.0)
        a.cmd(f"/open plain 127.0.0.1:{nmdc}", 1.0)
        b.cmd(f"/open secure nmdcs://127.0.0.1:{nmdcs}", 1.0)
        c.cmd(f"/open plain 127.0.0.1:{nmdc}", 1.0)

        # 1. login
        for n in clients:
            m = await seen(obs, rf"^\$MyINFO \$ALL {n.name} ", 30)
            if m is None and n is b and b.wait_screen(r"/accept", 3):
                b.cmd("/accept", 2.0)  # a keyprint ncdc has not seen before
                m = await seen(obs, rf"^\$MyINFO \$ALL {n.name} ", 20)
            check(f"login {n.name}", m is not None, (m or "").strip("|")[:120])

        # 2. main chat
        a.cmd("/say hello from ncdcA")
        m = await seen(obs, r"^<ncdcA> hello from ncdcA\|$")
        check("main chat ncdcA -> everyone", m is not None)
        b.cmd("/me waves")
        m = await seen(obs, r"^<ncdcB> .*waves", 10)
        check("/me from ncdcB reaches chat", m is not None, (m or "").strip("|"))

        # 3. private messages
        obs.send("$To: ncdcA From: observer $<observer> hi ncdcA|")
        check("private message observer -> ncdcA (ncdc opens a PM tab)",
              a.wait_screen(r"~observer"))
        a.cmd("/msg observer hi observer")
        m = await seen(obs, r"^\$To: observer From: ncdcA \$<ncdcA> hi observer\|$")
        check("private message ncdcA -> observer", m is not None)

        # 4. transfers the hub signals (from each one's hub tab, where /browse finds the user)
        b.cmd("/browse ncdcA", 1.0)
        deadline = time.time() + 30
        while time.time() < deadline and not b.file_lists():
            await asyncio.sleep(0.5)
        check("ncdcB (active) downloads ncdcA's file list via $ConnectToMe", bool(b.file_lists()),
              ", ".join(b.file_lists()))
        c.cmd("/browse ncdcA", 1.0)
        deadline = time.time() + 30
        while time.time() < deadline and not c.file_lists():
            await asyncio.sleep(0.5)
        check("ncdcC (passive) downloads ncdcA's file list via $RevConnectToMe",
              bool(c.file_lists()), ", ".join(c.file_lists()))

        # 5. search (-all: the current tab is a file list now)
        b.cmd("/search -all interop-ncdca", 1.0)
        m = await seen(obs, r"^\$Search .*interop", 20)
        check("search from ncdcB reaches the hub's users", m is not None, (m or "").strip("|"))
        if m is not None:
            check("the searcher's address is the hub's view of it",
                  m.startswith(f"$Search 127.0.0.1:{pb} ") or m.startswith("$Search Hub:"),
                  m.split(" ")[1])
        check("ncdcB shows ncdcA's result", b.wait_screen(r"/files/interop-ncdca\.txt", 20))
        c.cmd("/search -all interop-ncdcb", 1.0)
        m = await seen(obs, r"^\$Search Hub:ncdcC .*interop-ncdcb", 20)
        check("passive search from ncdcC goes through the hub", m is not None,
              (m or "").strip("|"))
        check("ncdcC shows ncdcB's result ($SR through the hub)",
              c.wait_screen(r"/files/interop-ncdcb\.txt", 20))
    finally:
        for n in clients:
            n.stop(out)
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
    check("the hub forwarded a $ConnectToMe", totals.get("ctm", 0) > 0, str(totals.get("ctm")))
    check("the hub forwarded a $RevConnectToMe", totals.get("rctm", 0) > 0,
          str(totals.get("rctm")))
    check("every ncdc $Key matched", totals.get("key_mismatch", 0) == 0,
          f"key_mismatch={totals.get('key_mismatch', 0)}")
    write_findings(cap, stats, os.path.join(out, "findings.md"))
    failed = [r for r in results if not r[1]]
    print(f"interop: {len(results) - len(failed)}/{len(results)} passed", flush=True)
    return 1 if failed else 0


def write_findings(cap: str, stats: dict, path: str) -> None:
    """What the captures say about spec/'s "To capture" items, for the spec writer."""
    lines = ["# ncdc interop: what the captures show", "",
             "Black-box captures of ncdc (the distribution's package) against the hub. Each item",
             "names the spec section it informs.", ""]
    for name in sorted(os.listdir(cap)):
        msgs = []
        header = ""
        for line in open(os.path.join(cap, name), encoding="ascii", errors="replace"):
            if line.startswith("#"):
                header = header or line[2:].strip()
                continue
            parts = line.rstrip("\n").split(None, 2)
            if len(parts) == 3:
                msgs.append((float(parts[0]), parts[1], parts[2]))
        sent = [(t, m) for t, w, m in msgs if w == "<"]
        nick = next((m.split(" ")[1].rstrip("|'") for _, m in sent
                     if m.startswith("b'$ValidateNick")), "")
        if nick in ("", "observer"):
            continue
        lines.append(f"## {nick} ({header})")
        lines.append("")
        lines.append("- Login burst (P2 To capture), the client's first messages with their times:")
        for t, m in sent[:8]:
            lines.append(f"  - `{t:.3f}` `{m[:140]}`")
        keepalives = [t for t, m in sent if m == "b'|'"]
        lines.append(f"- Keepalives (P1): {len(keepalives)} empty messages"
                     + (f", first at {keepalives[0]:.1f} s" if keepalives else "") + ".")
        for prefix, what in (("b'$Supports", "$Supports"), ("b'$MyINFO", "first $MyINFO"),
                             ("b'$ConnectToMe", "$ConnectToMe form (P5)"),
                             ("b'$RevConnectToMe", "$RevConnectToMe"),
                             ("b'$Search", "$Search (P4)"), ("b'<", "a chat line (P3)")):
            m = next((m for _, m in sent if m.startswith(prefix)), None)
            if m is not None:
                lines.append(f"- {what}: `{m[:200]}`")
        lines.append("")
    lines += ["## Hub counters", "", "```", json.dumps({k: stats.get(k) for k in
                                                        ("totals", "refusals", "drops", "closes",
                                                         "clients", "modes")}, indent=1), "```",
              ""]
    with open(path, "w") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--ncdc", default=shutil.which("ncdc") or "ncdc")
    p.add_argument("--out", default="interop-out")
    a = p.parse_args()
    os.makedirs(a.out, exist_ok=True)
    sys.exit(asyncio.run(run(a.ncdc, os.path.abspath(a.out))))
