"""The service on a real pycvc.ariadne Host under plain python: what it publishes, and the
program run end to end (``python -m sublevels.server``: HTTP up, an NMDC login and chat, SIGTERM,
a clean exit)."""

import asyncio
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request

from nmdc_client import Client
from sublevels import __version__


def test_publishes_the_server_subtree(server):
    tree = server.host.get_tree("sublevels.server")
    assert tree["version"] == __version__
    assert tree["status"] == "up"
    assert tree["started"].endswith("+00:00")


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _nmdc_login(port):
    c = Client("probe")
    await c.connect("127.0.0.1", port)
    await c.login()
    c.send("<probe> hi|")
    assert await c.read_until(lambda m: m.startswith("<probe>")) == "<probe> hi|"
    await c.close()


def test_python_m_sublevels_server(tmp_path, server):
    # `server` only proves pycvc is here (else this skips); the program is its own process.
    port, nmdc_port = _free_port(), _free_port()
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(p for p in sys.path if p))
    log = tmp_path / "server.log"
    with open(log, "wb") as out:
        proc = subprocess.Popen(
            [sys.executable, "-m", "sublevels.server", "--http-listen", f"127.0.0.1:{port}",
             "--nmdc-listen", f"127.0.0.1:{nmdc_port}", "--nmdcs-listen", "off"],
            stdout=out, stderr=subprocess.STDOUT, env=env)
    try:
        body, deadline = None, time.monotonic() + 30
        while body is None and time.monotonic() < deadline and proc.poll() is None:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=2) as r:
                    body = json.load(r)
            except OSError:
                time.sleep(0.1)
        assert body is not None, log.read_text()
        assert body["version"] == __version__
        asyncio.run(_nmdc_login(nmdc_port))
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=30) == 0, log.read_text()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    assert "Traceback" not in log.read_text()
