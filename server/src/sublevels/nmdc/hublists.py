"""Registration with public hub lists (P9 §1, roadmap D15).

Each target is a hub list. Its method is ``push`` (the registration exchange on TCP 2501 by
default) or ``pinger`` (the list was told about the hub once, on its site, and checks it with its
pinger, which the hub answers itself: ``Hub._bot_info``). Push targets are registered on a timer,
every ``hublist_interval`` seconds with up to ``hublist_jitter`` of random delay, and when an
operator asks (``Publisher.register_now``). Every attempt is recorded for the admin API; a target
that fails 10 times in a row backs off to hourly until it succeeds.

Nothing is pushed until the hub has a public address (``HubConfig.public_address``): a hub
without one has nothing a list could show, and test and CI hubs never set one.

A target is written ``host[:port][;key=standard|legacy][;method=push|pinger]``, for example
``hublist.te-home.net``, ``hublist.pwiam.com:2501;key=legacy`` or ``adchub.com;method=pinger``.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable

from .codec import escape, legacy_magic, lock_to_key

log = logging.getLogger("sublevels.hublists")

PORT = 2501  # "the classic RegServer port" [PWIAM]
TIMEOUT = 10.0  # the whole exchange (P9 §1)
LOCK_MAX = 512  # bytes before the lock's '|'
BACKOFF_AFTER = 10  # failures in a row
BACKOFF_INTERVAL = 3600.0


@dataclass(frozen=True)
class Target:
    host: str
    port: int = PORT
    key: str = "standard"  # or "legacy" (P9 §1)
    method: str = "push"  # or "pinger"

    @classmethod
    def parse(cls, text: str) -> "Target":
        where, *opts = [p.strip() for p in text.split(";")]
        host, port = where, PORT
        if where.count(":") == 1:
            host, _, p = where.partition(":")
            if not p.isdigit() or not 1 <= int(p) <= 65535:
                raise ValueError(f"hub list {text!r}: bad port")
            port = int(p)
        if not host:
            raise ValueError(f"hub list {text!r}: no host")
        kw = {}
        for o in opts:
            name, _, value = o.partition("=")
            if name == "key" and value in ("standard", "legacy"):
                kw["key"] = value
            elif name == "method" and value in ("push", "pinger"):
                kw["method"] = value
            else:
                raise ValueError(f"hub list {text!r}: unknown option {o!r}")
        return cls(host, port, **kw)

    @property
    def name(self) -> str:
        return f"{self.host}:{self.port}"


def push_address(hub) -> str:
    """The address in the record (P9 §1): ``host``, or ``host:port`` when the port is not 411; a
    TLS-only hub sends its ``nmdcs://`` address. "" when the hub has no public address."""
    plain = hub.listed_address()
    if not plain:
        return ""
    if hub.config.tls_required and hub.config.nmdcs_address:
        return hub.config.nmdcs_address.split("?")[0].rstrip("/")
    return plain[:-len(":411")] if plain.endswith(":411") else plain


def push_description(hub) -> str:
    c = hub.config
    desc = hub.listed_description()
    if c.minshare_prefix and c.min_share > 0:
        desc = f"[MINSHARE:{c.min_share // (1024 * 1024)}MB] {desc}"
    return desc


def record_fields(hub, target: Target) -> list[str]:
    """Everything after ``$Key <key>|``: name, address, description, users, share, the list."""
    users = list(hub.users.values())  # pingers and the bot are not users
    share = sum(u.info.share for u in users if u.info)
    return [escape(hub.config.name), push_address(hub), escape(push_description(hub)),
            str(len(users)), str(share), target.name]


async def _read_lock(reader: asyncio.StreamReader) -> bytes | None:
    """The lock bytes of the list's ``$Lock <lock> Pk=…|``; None if it sends anything else."""
    buf = b""
    while b"|" not in buf:
        if len(buf) > LOCK_MAX:
            raise ValueError(f"no '|' in the first {LOCK_MAX} bytes")
        chunk = await reader.read(LOCK_MAX + 1 - len(buf))
        if not chunk:
            return None
        buf += chunk
    first = buf.split(b"|", 1)[0]
    if len(first) > LOCK_MAX:
        raise ValueError(f"a $Lock longer than {LOCK_MAX} bytes")
    if not first.startswith(b"$Lock "):
        return None
    lock = first[len(b"$Lock "):].split(b" Pk=", 1)[0]
    return lock if len(lock) >= 2 else None


async def push(hub, target: Target, *, timeout: float = TIMEOUT) -> dict:
    """One registration exchange with ``target``. Returns the attempt: its ``outcome`` is
    ``connect_failed``, ``no_lock``, ``sent`` or ``error`` (with a ``reason``)."""
    attempt = {"target": target.name, "at": round(time.time(), 3), "key": target.key}
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(target.host, target.port), timeout)
    except (OSError, asyncio.TimeoutError) as e:
        attempt.update(outcome="connect_failed", reason=str(e) or type(e).__name__)
        return attempt
    try:
        lock = await asyncio.wait_for(_read_lock(reader), max(0.0, end - loop.time()))
        if lock is None:
            attempt["outcome"] = "no_lock"
            return attempt
        magic = 5
        if target.key == "legacy":
            magic = legacy_magic(writer.get_extra_info("sockname")[1])
        fields = record_fields(hub, target)
        writer.write(b"$Key " + lock_to_key(lock, magic) + b"|"
                     + hub.codepage.encode("".join(f + "|" for f in fields)))
        await asyncio.wait_for(writer.drain(), max(0.0, end - loop.time()))
        attempt.update(outcome="sent", users=int(fields[3]), share=int(fields[4]))
    except asyncio.TimeoutError:
        attempt.update(outcome="error", reason="timed out")
    except (OSError, ValueError) as e:
        attempt.update(outcome="error", reason=str(e) or type(e).__name__)
    finally:
        writer.close()
        try:
            await asyncio.wait_for(writer.wait_closed(), 1.0)
        except (OSError, asyncio.TimeoutError):
            pass
    return attempt


class TargetState:
    def __init__(self, target: Target):
        self.target = target
        self.attempts: deque[dict] = deque(maxlen=20)
        self.failures = 0  # in a row
        self.next_at = 0.0  # wall time of the next push, 0 when none is planned

    def as_dict(self, visits) -> dict:
        t = self.target
        out = {"target": t.name, "method": t.method}
        if t.method == "push":
            out.update(key=t.key, failures=self.failures, backed_off=self.failures >= BACKOFF_AFTER,
                       next_at=round(self.next_at, 3) or None,
                       last=self.attempts[-1] if self.attempts else None,
                       attempts=list(self.attempts))
        else:  # the list's pinger names it in its $BotINFO, usually (P9 §2)
            mine = [v for v in visits if t.host.lower() in v.get("botinfo", "").lower()]
            out["last_visit"] = mine[-1] if mine else None
        return out


class Publisher:
    """The hub's hub-list targets, their pushes and their results."""

    def __init__(self, hub, *, rand: Callable[[], float] = random.random,
                 sleep: Callable[[float], object] = asyncio.sleep,
                 pusher: Callable | None = None):
        self.hub = hub
        self.rand = rand
        self.sleep = sleep
        self.pusher = pusher or push
        self.targets = [TargetState(Target.parse(t)) for t in hub.config.hublists]
        self.tasks: list[asyncio.Task] = []

    @property
    def push_targets(self) -> list[TargetState]:
        return [t for t in self.targets if t.target.method == "push"]

    @property
    def enabled(self) -> bool:
        """Whether there is anything to push, and an address to push."""
        return bool(self.push_targets and push_address(self.hub))

    def why_off(self) -> str:
        if not self.push_targets:
            return "no push targets (SUBLEVELS_HUBLISTS)"
        if not push_address(self.hub):
            return "no public address (SUBLEVELS_PUBLIC_ADDRESS)"
        return ""

    async def register(self, ts: TargetState) -> dict:
        attempt = await self.pusher(self.hub, ts.target)
        ts.attempts.append(attempt)
        if attempt["outcome"] == "sent":
            ts.failures = 0
            log.info("hub list %s: registered (%s users)", ts.target.name, attempt["users"])
        else:
            ts.failures += 1
            log.warning("hub list %s: %s%s", ts.target.name, attempt["outcome"],
                        f" ({attempt['reason']})" if attempt.get("reason") else "")
        self.hub.counters.add(f"hublist_{attempt['outcome']}")
        return attempt

    async def register_now(self) -> list[dict]:
        """Push to every push target now (an operator's command)."""
        if not self.enabled:
            return []
        return list(await asyncio.gather(*(self.register(t) for t in self.push_targets)))

    def interval(self, ts: TargetState) -> float:
        c = self.hub.config
        base = BACKOFF_INTERVAL if ts.failures >= BACKOFF_AFTER else c.hublist_interval
        return base + self.rand() * c.hublist_jitter

    async def _run_one(self, ts: TargetState) -> None:
        delay = self.rand() * self.hub.config.hublist_jitter
        while True:
            ts.next_at = time.time() + delay
            await self.sleep(delay)
            await self.register(ts)
            delay = self.interval(ts)

    def start(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        """Start each push target's timer on ``loop`` (the running one by default); nothing when
        not enabled."""
        if not self.enabled:
            log.info("hub lists: not registering: %s", self.why_off())
            return
        log.info("hub lists: registering with %s every %.0f s",
                 ", ".join(t.target.name for t in self.push_targets),
                 self.hub.config.hublist_interval)
        loop = loop or asyncio.get_running_loop()
        self.tasks = [loop.create_task(self._run_one(t)) for t in self.push_targets]

    async def close(self) -> None:
        for t in self.tasks:
            t.cancel()
        for t in self.tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self.tasks = []

    def status(self) -> dict:
        """For the admin API: each target's state and results, and the pingers' recent visits."""
        visits = list(self.hub.visits)
        return {"enabled": self.enabled, "off": self.why_off() or None,
                "address": push_address(self.hub), "interval": self.hub.config.hublist_interval,
                "targets": [t.as_dict(visits) for t in self.targets],
                "pinger_visits": visits[-50:]}
