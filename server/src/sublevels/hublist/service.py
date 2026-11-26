"""Hublist mode (D16): its settings, its timers, and what the HTTP API calls.

Off by default. ``SUBLEVELS_HUBLIST=on`` turns it on; it then needs somewhere to keep its entries
(``SUBLEVELS_HUBLIST_DB``, a SQLAlchemy URL, else ``<data_dir>/hublist.sqlite3``).

- ``SUBLEVELS_HUBLIST_LISTEN``: the registration server (default 0.0.0.0:2501; ``off`` for none)
- ``SUBLEVELS_HUBLIST_NAME``, ``SUBLEVELS_HUBLIST_URL``: what the list calls itself, in the XML
  and in its pinger's ``$BotINFO``
- ``SUBLEVELS_HUBLIST_CHECK_KEY=0``: accept registrations whatever their ``$Key``
- ``SUBLEVELS_HUBLIST_PINGER_NICK``: the pinger's nick (default sublevels-pinger)
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import time
from dataclasses import dataclass

from . import pinger
from .listxml import Lists
from .register import RegistrationServer
from .store import STATES, Store, normalise

log = logging.getLogger("sublevels.hublist")

PING_EVERY = 1800.0
PING_JITTER = 300.0
BACKOFF = (3600.0, 7200.0, 14400.0)  # 1, 2, then 4 hours (P10 §2)
CONCURRENCY = 8
SCAN_EVERY = 15.0


@dataclass(frozen=True)
class HublistConfig:
    enabled: bool = False
    listen: str = "0.0.0.0:2501"
    db: str = ""
    name: str = "sublevels"
    url: str = ""
    check_key: bool = True
    pinger_nick: str = "sublevels-pinger"

    @classmethod
    def from_env(cls, env=None, data_dir: str = "") -> "HublistConfig":
        env = os.environ if env is None else env

        def on(var, default):
            v = env.get(var)
            return default if v is None or v == "" else v.strip().lower() in ("1", "on", "true",
                                                                              "yes")
        db = env.get("SUBLEVELS_HUBLIST_DB", "")
        if not db and data_dir:
            db = "sqlite:///" + os.path.join(os.path.abspath(data_dir), "hublist.sqlite3")
        return cls(enabled=on("SUBLEVELS_HUBLIST", False),
                   listen=env.get("SUBLEVELS_HUBLIST_LISTEN") or cls.listen, db=db,
                   name=env.get("SUBLEVELS_HUBLIST_NAME") or cls.name,
                   url=env.get("SUBLEVELS_HUBLIST_URL", ""),
                   check_key=on("SUBLEVELS_HUBLIST_CHECK_KEY", True),
                   pinger_nick=env.get("SUBLEVELS_HUBLIST_PINGER_NICK") or cls.pinger_nick)


class Hublist:
    def __init__(self, cfg: HublistConfig, *, geo=None, clock=time.time, rand=random.random,
                 pinger_fn=None):
        if not cfg.db:
            raise ValueError("SUBLEVELS_HUBLIST=on needs SUBLEVELS_HUBLIST_DB or a data "
                             "directory for its entries")
        if cfg.db.startswith("sqlite:///"):
            os.makedirs(os.path.dirname(cfg.db[len("sqlite:///"):]) or ".", exist_ok=True)
        self.cfg, self.geo, self.clock, self.rand = cfg, geo, clock, rand
        self.store = Store(cfg.db, clock=clock)
        self.lists = Lists(self.store, name=cfg.name, url=cfg.url, clock=clock)
        self.registrar = RegistrationServer(self.store, check_key=cfg.check_key,
                                            on_registered=lambda e: None)
        self.pinger_fn = pinger_fn or pinger.ping
        self.inflight: set[str] = set()
        self.sem = asyncio.Semaphore(CONCURRENCY)
        self.tasks: list[asyncio.Task] = []
        self.port = 0

    # -- pings -------------------------------------------------------------------------------------

    def next_after(self, ok: bool, fails: int) -> float:
        now = self.clock()
        if ok:
            return now + PING_EVERY + self.rand() * PING_JITTER
        return now + BACKOFF[min(fails, len(BACKOFF) - 1)]

    async def ping_one(self, entry: dict) -> dict | None:
        """Ping one entry now and record what it found."""
        address = entry["address"]
        if address in self.inflight:
            return None
        self.inflight.add(address)
        try:
            async with self.sem:
                try:
                    got = await self.pinger_fn(address, nick=self.cfg.pinger_nick,
                                               list_name=self.cfg.name, list_url=self.cfg.url)
                except (pinger.PingError, asyncio.TimeoutError, OSError) as e:
                    msg = str(e) or type(e).__name__
                    log.info("hublist: %s did not answer: %s", address, msg)
                    return self.store.record_ping(address, False, {},
                                                  self.next_after(False, entry["fails"] or 0),
                                                  msg)
                peer = got.pop("peer", "")
                if self.geo is not None and peer:
                    cc = self.geo(peer).country
                    if cc:
                        got["country"] = cc
                return self.store.record_ping(address, True, got, self.next_after(True, 0))
        finally:
            self.inflight.discard(address)

    async def run_pings(self) -> None:
        while True:
            for e in self.store.due(self.clock(), 4 * CONCURRENCY):
                if e["address"] not in self.inflight:
                    asyncio.get_running_loop().create_task(self.ping_one(e))
            await asyncio.sleep(SCAN_EVERY)

    # -- lifecycle ---------------------------------------------------------------------------------

    async def start(self) -> None:
        if self.cfg.listen != "off":
            host, _, port = self.cfg.listen.rpartition(":")
            self.port = await self.registrar.start(host.strip("[]") or "0.0.0.0", int(port))
            log.info("hublist: taking registrations on %s:%d", host or "0.0.0.0", self.port)
        self.tasks.append(asyncio.get_running_loop().create_task(self.run_pings()))
        log.info("hublist: %s, %d entries", self.cfg.name, len(self.store.all()))

    async def close(self) -> None:
        for t in self.tasks:
            t.cancel()
        for t in self.tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        await self.registrar.close()
        self.store.close()

    # -- what the API calls ------------------------------------------------------------------------

    def entries(self, state: str = "") -> list[dict]:
        rows = self.store.all(state or None)
        for r in rows:
            r["reliability"] = self.store.reliability(r["address"])
        return rows

    def set_state(self, address: str, state: str) -> dict | None:
        if state not in STATES:
            raise ValueError(f"state {state!r}: one of {', '.join(STATES)}")
        e = self.store.set_state(normalise(address), state)
        self.lists.built_at = -1e18  # the next request rebuilds
        return e

    def delete(self, address: str) -> bool:
        ok = self.store.delete(normalise(address))
        self.lists.built_at = -1e18
        return ok

    def stats(self) -> dict:
        by = {}
        for e in self.store.all():
            by[e["state"]] = by.get(e["state"], 0) + 1
        return {"name": self.cfg.name, "entries": by,
                "registrations": dict(self.registrar.counts), "port": self.port}
