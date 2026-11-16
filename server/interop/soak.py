"""A soak of the hub with synthetic NMDC clients (D7: 6000+ per server), measuring what M1 asks
for: memory per user, chat and search fan-out latency, and the hub's CPU.

    python interop/soak.py --users 6000 [--workers 4] [--ramp 400] [--chat 30] [--out DIR]

The hub runs alone (tools/run_hub.py) in its own process, as one asyncio loop, the way it runs on
the arisrvx script loop. Worker processes open the clients: each logs in as P2 says (the bytes are
those of tools/nmdc_client.py), then reads everything the hub sends, as a real client must, and
timestamps the probe's chat lines and searches as they arrive. One probe client in this process
sends a chat line every 2 s (each carrying the time it was sent; the chat limit is 5 in 10 s) and
a search every 10 s. Latency is measured at every receiver, so it includes the receiving worker's
own loop delay.

Results go to stdout and DIR/soak.json: login ramp time, the hub's RSS before and after (and per
user), its CPU over the ramp and the steady phase, and fan-out latency percentiles over every
receiver of every probe message.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import multiprocessing as mp
import os
import re
import resource
import signal
import socket
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(SERVER, "tools"))

from nmdc_client import Client, lock_to_key  # noqa: E402

TICK = os.sysconf("SC_CLK_TCK")
PROBE_RX = re.compile(rb"<probe> (chat|search) ([0-9.]+)\||\$Search [^ ]+ F\?T\?0\?1\?soak(\d+\.\d+)\|")


def raise_fd_limit(want: int) -> int:
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    new = min(hard, max(soft, want))
    resource.setrlimit(resource.RLIMIT_NOFILE, (new, hard))
    return new


def proc_sample(pid: int) -> tuple[int, float]:
    """(RSS bytes, CPU seconds) of a process, from /proc."""
    rss = 0
    with open(f"/proc/{pid}/status") as f:
        for line in f:
            if line.startswith("VmRSS:"):
                rss = int(line.split()[1]) * 1024
    with open(f"/proc/{pid}/stat") as f:
        fields = f.read().rsplit(")", 1)[1].split()
    cpu = (int(fields[11]) + int(fields[12])) / TICK
    return rss, cpu


class SoakClient(asyncio.Protocol):
    """A client that logs in and then reads and drops everything, noting probe messages."""

    def __init__(self, nick: str, mode: str, done: asyncio.Future, stats: dict):
        self.nick = nick.encode()
        self.mode = mode
        self.done = done
        self.stats = stats
        self.buf = b""
        self.state = "lock"
        self.mine = (b"$MyINFO $ALL " + self.nick + b" <soak V:1,M:" + mode.encode() +
                     b",H:1/0/0,S:1>$ $LAN(T3)\x01$$1000$|")
        self.tail = b""
        self.t0 = time.monotonic()

    def connection_made(self, transport):
        self.transport = transport

    def data_received(self, data: bytes) -> None:
        self.stats["bytes"] += len(data)
        if self.state != "online":
            self.buf += data
            self._login()
            return
        chunk = self.tail + data
        if b"<probe>" in chunk or b"soak" in chunk:
            now = time.time()
            old = len(self.tail)
            for m in PROBE_RX.finditer(chunk):
                if m.end() <= old:
                    continue  # matched in the previous chunk already
                kind = m.group(1) or b"search"
                sent = float(m.group(2) or m.group(3))
                self.stats["lat"][kind.decode()].append(now - sent)
        self.tail = chunk[-160:]

    def _login(self) -> None:
        if self.state == "lock" and b"|" in self.buf:
            first, _, self.buf = self.buf.partition(b"|")
            lock = first[len(b"$Lock "):].split(b" Pk=")[0]
            self.transport.write(b"$Supports NoHello NoGetINFO|$Key " + lock_to_key(lock) +
                                 b"|$ValidateNick " + self.nick + b"|")
            self.state = "hello"
        if self.state == "hello" and b"$Hello " + self.nick + b"|" in self.buf:
            self.transport.write(b"$Version 1,0091|$GetNickList|" + self.mine)
            self.state = "info"
        if self.state == "info" and self.mine in self.buf:
            self.state = "online"
            self.tail = self.buf[-160:]
            self.buf = b""
            if not self.done.done():
                self.done.set_result(time.monotonic() - self.t0)
        elif self.state == "info" and len(self.buf) > 1 << 16:
            self.buf = self.buf[-(len(self.mine) + 1024):]  # the list is long: keep only a tail
        if b"$ValidateDenide" in self.buf or b"$HubIsFull" in self.buf:
            if not self.done.done():
                self.done.set_exception(RuntimeError(self.buf[-200:].decode("latin-1")))

    def connection_lost(self, exc):
        self.stats["lost"] += 1
        if not self.done.done():
            self.done.set_exception(ConnectionError(str(exc)))


async def worker_main(wid: int, port: int, nicks: list[str], rate: float, conn, hold: float):
    loop = asyncio.get_running_loop()
    stats = {"bytes": 0, "lost": 0, "lat": {"chat": [], "search": []}}
    logins, errors = [], []
    transports = []

    async def one(nick, mode):
        done = loop.create_future()
        try:
            tr, _ = await loop.create_connection(lambda: SoakClient(nick, mode, done, stats),
                                                 "127.0.0.1", port)
            transports.append(tr)
            logins.append(await asyncio.wait_for(done, 120))
        except Exception as e:  # noqa: BLE001 - counted and reported
            errors.append(f"{type(e).__name__}: {e}"[:200])

    tasks = []
    for i, nick in enumerate(nicks):
        tasks.append(loop.create_task(one(nick, "A" if i % 2 else "P")))
        await asyncio.sleep(1.0 / rate)
    await asyncio.gather(*tasks)
    conn.send({"wid": wid, "phase": "online", "logins": logins, "errors": errors[:20],
               "error_count": len(errors), "lost": stats["lost"]})
    # The steady phase: read until told to stop.
    while not conn.poll():
        await asyncio.sleep(0.2)
    conn.recv()
    conn.send({"wid": wid, "phase": "done", "lat": stats["lat"], "bytes": stats["bytes"],
               "lost": stats["lost"]})
    for tr in transports:
        tr.close()


def worker(wid, port, nicks, rate, conn, hold):
    raise_fd_limit(len(nicks) + 256)
    asyncio.run(worker_main(wid, port, nicks, rate, conn, hold))


def pct(values: list[float], p: float) -> float:
    if not values:
        return float("nan")
    s = sorted(values)
    return s[min(len(s) - 1, int(round(p / 100 * (len(s) - 1))))]


async def probe_phase(port: int, seconds: int, chat_every: float, search_every: float) -> dict:
    p = Client("probe", supports="NoHello NoGetINFO")
    await p.connect("127.0.0.1", port, timeout=30)
    await p.login(timeout=300)
    sent = {"chat": 0, "search": 0}
    end = time.monotonic() + seconds
    next_search = time.monotonic()

    async def drain():
        while True:
            await p.reader.read(1 << 20)

    d = asyncio.create_task(drain())
    while time.monotonic() < end:
        p.send(f"<probe> chat {time.time():.6f}|")
        sent["chat"] += 1
        if time.monotonic() >= next_search:
            p.send(f"$Search 127.0.0.1:9 F?T?0?1?soak{time.time():.6f}|")
            sent["search"] += 1
            next_search += search_every
        await p.writer.drain()
        await asyncio.sleep(chat_every)
    await asyncio.sleep(2.0)
    d.cancel()
    await p.close()
    return sent


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--users", type=int, default=6000)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--ramp", type=float, default=400.0, help="logins per second, in all")
    ap.add_argument("--chat", type=int, default=30, help="seconds of the steady phase")
    ap.add_argument("--out", default="soak-out")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    raise_fd_limit(a.users + 1024)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    stats_path = os.path.join(a.out, "hub-stats.json")
    env = dict(os.environ, SUBLEVELS_NMDC_LISTEN=f"127.0.0.1:{port}", SUBLEVELS_NMDCS_LISTEN="off",
               SUBLEVELS_MAX_USERS=str(a.users + 100), SUBLEVELS_HUB_NAME="soak")
    hub_log = open(os.path.join(a.out, "hub.log"), "w")
    hub = subprocess.Popen([sys.executable, os.path.join(SERVER, "tools", "run_hub.py"),
                            "--stats", stats_path], env=env, stdout=hub_log,
                           stderr=subprocess.STDOUT,
                           preexec_fn=lambda: raise_fd_limit(a.users + 1024))
    samples = []
    try:
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", port), 0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        time.sleep(0.5)
        rss0, cpu0 = proc_sample(hub.pid)
        t0 = time.monotonic()
        per = [[f"s{w}x{i}" for i in range(w, a.users, a.workers)] for w in range(a.workers)]
        pipes, procs = [], []
        for w in range(a.workers):
            parent, child = mp.Pipe()
            pr = mp.Process(target=worker, args=(w, port, per[w], a.ramp / a.workers, child, 0))
            pr.start()
            pipes.append(parent)
            procs.append(pr)
        online = []
        while len(online) < a.workers:
            for pipe in pipes:
                if pipe.poll():
                    online.append(pipe.recv())
            rss, cpu = proc_sample(hub.pid)
            samples.append((time.monotonic() - t0, rss, cpu - cpu0))
            time.sleep(0.5)
        t_ramp = time.monotonic() - t0
        rss1, cpu1 = proc_sample(hub.pid)
        logins = [x for o in online for x in o["logins"]]
        errors = sum(o["error_count"] for o in online)
        print(f"soak: {len(logins)}/{a.users} online in {t_ramp:.1f} s ({errors} errors); hub RSS "
              f"{rss0 / 2**20:.0f} -> {rss1 / 2**20:.0f} MiB "
              f"({(rss1 - rss0) / max(1, len(logins)) / 1024:.1f} KiB/user); CPU {cpu1 - cpu0:.1f} s "
              f"over the ramp", flush=True)
        c0 = proc_sample(hub.pid)[1]
        ts = time.monotonic()
        sent = asyncio.run(probe_phase(port, a.chat, 2.0, 10.0))  # the chat limit: 5 in 10 s
        steady = time.monotonic() - ts
        c1 = proc_sample(hub.pid)[1]
        rss2 = proc_sample(hub.pid)[0]
        for pipe in pipes:
            pipe.send("stop")
        done = [pipe.recv() for pipe in pipes]
        for pr in procs:
            pr.join(30)
    finally:
        hub.send_signal(signal.SIGTERM)
        try:
            hub.wait(30)
        except subprocess.TimeoutExpired:
            hub.kill()
        hub_log.close()
    lat = {k: [x for d in done for x in d["lat"][k]] for k in ("chat", "search")}
    hub_stats = json.load(open(stats_path)) if os.path.exists(stats_path) else {}
    result = {
        "users": a.users, "online": len(logins), "login_errors": errors,
        "login_error_samples": sorted({e for o in online for e in o["errors"]})[:10],
        "lost": sum(d["lost"] for d in done),
        "ramp_seconds": round(t_ramp, 2), "ramp_rate": a.ramp,
        "login_seconds": {"p50": round(pct(logins, 50), 3), "p95": round(pct(logins, 95), 3),
                          "max": round(max(logins, default=float("nan")), 3)},
        "hub_rss_mib": {"start": round(rss0 / 2**20, 1), "online": round(rss1 / 2**20, 1),
                        "after_steady": round(rss2 / 2**20, 1)},
        "kib_per_user": round((rss1 - rss0) / max(1, len(logins)) / 1024, 2),
        "hub_cpu_seconds": {"ramp": round(cpu1 - cpu0, 2), "steady": round(c1 - c0, 2),
                            "steady_share": round((c1 - c0) / steady, 3)},
        "probe_sent": sent,
        "fanout_latency_ms": {k: {"n": len(v), "p50": round(pct(v, 50) * 1e3, 2),
                                  "p95": round(pct(v, 95) * 1e3, 2),
                                  "p99": round(pct(v, 99) * 1e3, 2),
                                  "max": round(max(v, default=float("nan")) * 1e3, 2)}
                              for k, v in lat.items()},
        "client_bytes_read": sum(d["bytes"] for d in done),
        "hub": {k: hub_stats.get(k) for k in ("totals", "refusals", "drops", "closes")},
        "samples": [(round(t, 1), round(r / 2**20, 1), round(c, 2)) for t, r, c in samples],
    }
    with open(os.path.join(a.out, "soak.json"), "w") as f:
        json.dump(result, f, indent=1)
    print(json.dumps({k: v for k, v in result.items() if k not in ("samples", "hub")}, indent=1))
    ok = result["online"] == a.users and result["lost"] == 0
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
