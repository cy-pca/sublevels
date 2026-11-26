"""The room bridge end to end, through libcvc's real host code (arisrvx with the bridged-users API,
libcvc 4.0.9+): `sublevels serve` runs the relay and the NMDC hub in one arisrvx; an NMDC client
"zed" logs in to the hub; an Ariadne client (`arigl snapshot` in conference mode, running
interop/bridge_client.ari) joins the relay's room. Between them:

  1. the room lists zed as a bridged NMDC user;
  2. zed's main-chat line reaches the room's chat;
  3. the room's post reaches zed as <[room]Alice> text, and zed's answer reaches the room;
  4. a direct message from the room reaches zed as a private message from [room]Alice, and zed's
     reply reaches the participant's conversation with zed.

    python interop/bridge_e2e.py --prefix PREFIX [--out DIR]

PREFIX holds sublevels' runtime from cvcpkg (arisrvx, pycvc-cp312, fastapi, uvicorn) and arigl; a
display is needed for arigl (DISPLAY, e.g. cvcpkg's Xvfb with Mesa, as nicotina's CI does).
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
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(SERVER, "tools"))

from nmdc_client import Client  # noqa: E402

ROOM = "standup"  # sublevels' default, cyprty's


def free_ports(n):
    socks = [socket.socket() for _ in range(n)]
    for s in socks:
        s.bind(("127.0.0.1", 0))
    out = [s.getsockname()[1] for s in socks]
    for s in socks:
        s.close()
    return out


async def zed(port: int, log: list, done: asyncio.Event) -> None:
    """The NMDC side: log in, say hello, answer the room's post and its direct message."""
    c = Client("zed")
    await c.connect("127.0.0.1", port)
    await c.login()
    log.append("zed: logged in")
    await asyncio.sleep(1.0)
    c.send("<zed> hello from NMDC|")
    while not done.is_set():
        try:
            m = await c.read(timeout=1.0)
        except (TimeoutError, asyncio.TimeoutError):
            continue
        if m == f"<[{ROOM}]Alice> hello from the room|":
            log.append(f"zed: got {m!r}")
            c.send("<zed> zed saw: hello from the room|")
        elif m.startswith(f"$To: zed From: [{ROOM}]Alice $<[{ROOM}]Alice> psst|"):
            log.append(f"zed: got {m!r}")
            c.send(f"$To: [{ROOM}]Alice From: zed $<zed> psst back|")
    await c.close()


async def run(prefix: str, out: str, transport: str) -> int:
    os.makedirs(out, exist_ok=True)
    state, media, ws, http, nmdc = free_ports(5)
    env = dict(os.environ, SUBLEVELS_NMDC_TRANSPORT=transport)
    srv_log = open(os.path.join(out, "sublevels.log"), "w")
    srv = subprocess.Popen(
        [os.path.join(SERVER, "bin", "sublevels"), "serve", "--room", ROOM,
         "--state-listen", f"127.0.0.1:{state}", "--media-listen", f"127.0.0.1:{media}",
         "--ws-listen", f"127.0.0.1:{ws}", "--nmdc", f"127.0.0.1:{nmdc}", "--nmdcs", "off",
         "--http", f"127.0.0.1:{http}", "--no-txwtf"],
        env=dict(env, PATH=os.path.join(prefix, "bin") + os.pathsep + env.get("PATH", "")),
        stdout=srv_log, stderr=subprocess.STDOUT)
    log: list[str] = []
    rc = 1
    try:
        for _ in range(600):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{http}/healthz", timeout=1) as r:
                    json.load(r)
                break
            except OSError:
                if srv.poll() is not None:
                    print("bridge_e2e: sublevels exited before it served", flush=True)
                    return 1
                await asyncio.sleep(0.1)
        done = asyncio.Event()
        z = asyncio.create_task(zed(nmdc, log, done))
        await asyncio.sleep(2.0)
        cenv = dict(os.environ, CVC_ARIADNE_RELAY=f"grpc://127.0.0.1:{state}",
                    CVC_ARIADNE_ROOM=ROOM, CVC_ARIADNE_NAME="Alice")
        client = await asyncio.create_subprocess_exec(
            os.path.join(prefix, "bin", "arigl"), "snapshot", "--frames", "3000", "--png",
            os.path.join(out, "bridge_client.png"), os.path.join(HERE, "bridge_client.ari"),
            env=cenv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        text = (await asyncio.wait_for(client.communicate(), 300))[0].decode(errors="replace")
        with open(os.path.join(out, "arigl.log"), "w") as f:
            f.write(text)
        done.set()
        await asyncio.wait_for(z, 10)
        ok = "bridge_e2e: ok" in text and client.returncode == 0
        for line in log:
            print(line, flush=True)
        print("\n".join(line for line in text.splitlines() if "bridge_e2e" in line), flush=True)
        print(f"bridge_e2e: {'PASS' if ok else 'FAIL'} (arigl exit {client.returncode}, "
              f"hub transport {transport})", flush=True)
        rc = 0 if ok else 1
    finally:
        srv.send_signal(signal.SIGTERM)
        try:
            srv.wait(30)
        except subprocess.TimeoutExpired:
            srv.kill()
        srv_log.close()
    return rc


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--prefix", required=True)
    p.add_argument("--out", default="bridge-e2e-out")
    p.add_argument("--transport", default="asyncio", choices=("asyncio", "engine"))
    a = p.parse_args()
    sys.exit(asyncio.run(run(os.path.abspath(a.prefix), os.path.abspath(a.out), a.transport)))
