"""EiskaltDC++ against the hub, as a black box (M2): two clients through ``eiskaltdcpp-interop``,
the JSON-lines driver from transfix/eiskaltdcpp-py (contract version 1; on cvcpkg as
``eiskaltdcpp-py-cp312``), plus an operator: this repository's own test client with a password.

    python interop/eiskaltdcpp_interop.py --driver P/bin/eiskaltdcpp-interop [--tls] [--out DIR]

The hub runs alone (tools/run_hub.py) with a plain and a TLS listener, an operator account, and its
capture on. Two clients share one file each (1 MiB of random bytes):

  eiskaltA  active (TCP/UDP on 127.0.0.1)
  eiskaltP  passive

Over plain NMDC, or with --tls over nmdcs://…/?kp=<the hub's keyprint>. Checks:
  1. both log in, and each one's user list shows the other and the hub bot;
  2. main chat both ways, and private messages both ways;
  3. search and download both ways, each file compared by SHA-256 with its source:
       eiskaltA <- eiskaltP  (active <- passive)
       eiskaltP <- eiskaltA  (passive <- active)
  4. with --tls: a wrong keyprint is refused;
  5. operators (P6): the operator redirects eiskaltA with $OpForceMove to the hub's other address,
     and EiskaltDC++ follows it there; then the operator kicks eiskaltP with a reason given in a
     private message just before. What EiskaltDC++ reports for the kick, and whether it comes back
     by itself within 15 s, is written to findings.md for P6's "To capture".
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import queue
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(SERVER, "tools"))
sys.path.insert(0, os.path.join(SERVER, "src"))
sys.path.insert(0, HERE)

from ncdc_interop import check, free_ports, results  # noqa: E402
from nmdc_client import Client  # noqa: E402

SIZE = 1 << 20
BOT = "sublevels"


class Driver:
    """One ``eiskaltdcpp-interop``: commands in, events out."""

    def __init__(self, binary: str, nick: str, root: str, *, port: int = 0, log_dir: str):
        self.nick = nick
        self.share = os.path.join(root, f"share-{nick}")
        self.dl = os.path.join(root, f"dl-{nick}")
        os.makedirs(self.share)
        os.makedirs(self.dl)
        data = os.urandom(SIZE)
        self.file = f"{nick.lower()}-payload.bin"
        with open(os.path.join(self.share, self.file), "wb") as f:
            f.write(data)
        self.digest = hashlib.sha256(data).hexdigest()
        args = [binary, "--nick", nick, "--share", self.share, "--download-dir", self.dl,
                "--log", os.path.join(log_dir, f"{nick}.log")]
        args += ["--active", "--port", str(port), "--ip", "127.0.0.1"] if port else ["--passive"]
        self.proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     text=True, bufsize=1)
        self.events: queue.Queue = queue.Queue()
        self.seen: list[dict] = []
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        for line in self.proc.stdout:
            try:
                self.events.put(json.loads(line))
            except ValueError:
                self.events.put({"event": "garbage", "line": line})

    def send(self, **cmd) -> None:
        self.proc.stdin.write(json.dumps(cmd) + "\n")
        self.proc.stdin.flush()

    def wait(self, pred, timeout: float) -> dict | None:
        """The first event (already seen or new) for which ``pred`` holds, or None."""
        for e in self.seen:
            if pred(e):
                self.seen.remove(e)
                return e
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                e = self.events.get(timeout=max(0.05, end - time.monotonic()))
            except queue.Empty:
                break
            if pred(e):
                return e
            self.seen.append(e)
        return None

    def events_so_far(self) -> list[dict]:
        while True:
            try:
                self.seen.append(self.events.get_nowait())
            except queue.Empty:
                return list(self.seen)

    def quit(self) -> int | None:
        try:
            self.send(op="quit")
        except (BrokenPipeError, ValueError):
            pass
        try:
            return self.proc.wait(15)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            return None


def hub_event(state: str):
    return lambda e: e.get("event") == "hub" and e.get("state") == state


def users_of(d: Driver) -> list[str]:
    d.send(op="users")
    e = d.wait(lambda e: e.get("event") == "users", 10)
    return e["nicks"] if e else []


def search_and_download(getter: Driver, owner: Driver) -> tuple[bool, str]:
    getter.send(op="search", pattern=owner.file.replace(".bin", ""))
    r = getter.wait(lambda e: e.get("event") == "result" and e.get("name") == owner.file
                    and e.get("from") == owner.nick, 30)
    if r is None:
        return False, "no search result"
    if not r.get("tth"):
        return False, f"a result without a TTH: {r}"
    getter.send(op="download", name=owner.file, **{"from": owner.nick})
    d = getter.wait(lambda e: e.get("event") in ("downloaded", "error"), 90)
    if d is None:
        return False, "no downloaded event in 90 s"
    if d["event"] == "error":
        return False, d.get("message", "")
    return d.get("sha256") == owner.digest, f"{d.get('path')} sha256 {d.get('sha256')}"


async def operator(port: int, tls: bool, password: str):
    op = Client("hubop")
    await op.connect("127.0.0.1", port, tls=tls)
    await op.login(password=password)
    return op


async def run(driver: str, tls: bool, out: str) -> int:
    root = tempfile.mkdtemp(prefix="sublevels-eiskalt-")
    cap = os.path.join(out, "capture")
    os.makedirs(cap, exist_ok=True)
    nmdc, nmdcs, pa = free_ports(3)
    cert, key = os.path.join(root, "hub.crt"), os.path.join(root, "hub.key")
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                    "-subj", "/CN=127.0.0.1", "-keyout", key, "-out", cert], check=True,
                   capture_output=True)
    password = secrets.token_urlsafe(12)  # this run's operator only
    from sublevels.nmdc.accounts import hash_password
    accounts = os.path.join(root, "accounts.json")
    with open(accounts, "w") as f:
        json.dump({"accounts": {"hubop": {"password": hash_password(password), "role": "op"}}}, f)
    env = dict(os.environ, SUBLEVELS_NMDC_LISTEN=f"127.0.0.1:{nmdc}",
               SUBLEVELS_NMDCS_LISTEN=f"127.0.0.1:{nmdcs}", SUBLEVELS_TLS_CERT=cert,
               SUBLEVELS_TLS_KEY=key, SUBLEVELS_NMDC_CAPTURE_DIR=cap,
               SUBLEVELS_HUB_NAME="sublevels eiskaltdcpp interop",
               SUBLEVELS_NMDCS_ADDRESS=f"nmdcs://127.0.0.1:{nmdcs}",
               SUBLEVELS_ACCOUNTS_FILE=accounts, SUBLEVELS_HUBLISTS="off")
    if not tls:
        env["SUBLEVELS_PASSWORD_REQUIRES_TLS"] = "0"
    stats_path = os.path.join(out, "hub-stats.json")
    hub_log = open(os.path.join(out, "hub.log"), "w")
    hub = subprocess.Popen([sys.executable, os.path.join(SERVER, "tools", "run_hub.py"),
                            "--stats", stats_path], env=env, stdout=hub_log,
                           stderr=subprocess.STDOUT)
    drivers: list[Driver] = []
    findings = ["# EiskaltDC++ interop: what the client showed", "",
                f"EiskaltDC++ through eiskaltdcpp-interop, over {'NMDCS' if tls else 'NMDC'}, "
                f"hub transport {os.environ.get('SUBLEVELS_NMDC_TRANSPORT', 'auto')}.", ""]
    op = None
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
        tls_url = f"nmdcs://127.0.0.1:{nmdcs}/?kp={kp}"
        url = tls_url if tls else plain_url
        print(f"eiskaltdcpp: hub at {url}", flush=True)

        a = Driver(driver, "eiskaltA", root, port=pa, log_dir=out)
        p = Driver(driver, "eiskaltP", root, log_dir=out)
        drivers = [a, p]
        for d in drivers:
            r = d.wait(lambda e: e.get("event") in ("ready", "error"), 300)
            if not check(f"{d.nick}: the driver is ready", bool(r and r["event"] == "ready"),
                         json.dumps(r)):
                return 1

        # 4. a wrong keyprint, before anything else
        if tls:
            wrong = f"nmdcs://127.0.0.1:{nmdcs}/?kp=SHA256/" + "A" * 52
            p.send(op="connect", url=wrong)
            e = p.wait(lambda e: e.get("event") == "hub", 30)
            check("a wrong keyprint is refused", bool(e and e["state"] == "error"),
                  json.dumps(e))

        # 1. login and user lists
        for d in drivers:
            d.send(op="connect", url=url)
        for d in drivers:
            e = d.wait(lambda e: e.get("event") == "hub" and e.get("state") in
                       ("connected", "error"), 30)
            check(f"{d.nick} logs in", bool(e and e["state"] == "connected"), json.dumps(e))
        time.sleep(1.0)
        for d, other in ((a, p), (p, a)):
            nicks = users_of(d)
            check(f"{d.nick} sees {other.nick} and the bot", {other.nick, BOT} <= set(nicks),
                  ", ".join(nicks))

        # 2. chat and private messages
        for d, other in ((a, p), (p, a)):
            text = f"hello from {d.nick}"
            d.send(op="say", text=text)
            e = other.wait(lambda e: e.get("event") == "chat" and e.get("from") == d.nick
                           and e.get("text") == text, 15)
            check(f"main chat {d.nick} -> {other.nick}", e is not None)
            text = f"psst from {d.nick}"
            d.send(op="pm", to=other.nick, text=text)
            e = other.wait(lambda e: e.get("event") == "pm" and e.get("from") == d.nick
                           and e.get("text") == text, 15)
            check(f"private message {d.nick} -> {other.nick}", e is not None)

        # 3. search and download both ways
        ok, detail = search_and_download(a, p)
        check("eiskaltA <- eiskaltP (active <- passive)", ok, detail)
        ok, detail = search_and_download(p, a)
        check("eiskaltP <- eiskaltA (passive <- active)", ok, detail)

        # 5. an operator: redirect, then kick
        op = await operator(nmdcs if tls else nmdc, tls, password)
        check("the operator logs in with its password", True)
        target = plain_url if tls else tls_url
        where = target if tls else f"nmdcs://127.0.0.1:{nmdcs}"
        op.send(f"$OpForceMove $Who:eiskaltA$Where:{where}$Msg:moving you|")
        e = a.wait(lambda e: e.get("event") == "hub" and e.get("state") == "redirect", 20)
        check("eiskaltA is redirected by $OpForceMove", e is not None, json.dumps(e))
        if e is not None:
            c = a.wait(lambda e: e.get("event") == "hub" and e.get("state") in
                       ("connected", "error"), 30)
            findings.append(f"- **Redirect** ($OpForceMove to `{where}`): `{json.dumps(e)}`, "
                            f"then `{json.dumps(c)}`.")
            # Without the keyprint a redirect to nmdcs:// is accepted with any certificate
            # (the driver's contract), so the client should land there.
            check("eiskaltA follows the redirect and logs in there",
                  bool(c and c["state"] == "connected"), json.dumps(c))
        op.send("$To: eiskaltP From: hubop $<hubop> testing kicks|")
        await asyncio.sleep(0.5)
        op.send("$Kick eiskaltP|")
        e = p.wait(hub_event("disconnected"), 10)

        def kicked() -> int:
            try:
                return json.load(open(stats_path))["closes"].get("kicked by hubop", 0)
            except (OSError, ValueError, KeyError):
                return 0
        for _ in range(30):  # the hub writes its stats every second
            if kicked():
                break
            time.sleep(0.5)
        check("eiskaltP is kicked off the hub (the hub closed it)", kicked() == 1,
              f"client event {json.dumps(e)}")
        back = p.wait(hub_event("connected"), 15)
        shown = [x for x in p.events_so_far() if x.get("event") in ("chat", "pm")
                 and ("kick" in x.get("text", "").lower())]
        findings += [
            "- **Kick** (P6 To capture), the operator's PM then `$Kick`:",
            f"  - what the client reported: {json.dumps(shown) if shown else 'no chat or PM'}",
            f"  - the client's hub event within 10 s: `{json.dumps(e)}`"
            + ("" if e else " (none: it may have reconnected before reporting the loss)"),
            f"  - came back by itself within 15 s: {'yes' if back else 'no'}"
            + (f" (`{json.dumps(back)}`)" if back else ""),
        ]
    finally:
        if op is not None:
            await op.close()
        codes = [(d.nick, d.quit()) for d in drivers]
        for nick, code in codes:
            check(f"{nick}: the driver exits 0 on quit", code == 0, str(code))
        hub.send_signal(signal.SIGTERM)
        try:
            hub.wait(15)
        except subprocess.TimeoutExpired:
            hub.kill()
        hub_log.close()
    passed = sum(1 for _, ok, _ in results if ok)
    findings += ["", f"Checks: {passed}/{len(results)} passed."]
    with open(os.path.join(out, "findings.md"), "w") as f:
        f.write("\n".join(findings) + "\n")
    print(f"eiskaltdcpp: {passed}/{len(results)} passed", flush=True)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--driver", required=True, help="the eiskaltdcpp-interop command")
    ap.add_argument("--tls", action="store_true", help="connect over NMDCS with the keyprint")
    ap.add_argument("--out", default="eiskaltdcpp-out")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    sys.exit(asyncio.run(run(a.driver, a.tls, os.path.abspath(a.out))))
