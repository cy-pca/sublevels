"""The hub list's entries (P10 §3): one per hub, keyed by its normalised address, with its state
and what registrations and pings said about it, and the pings' history for reliability.

States: ``pending`` (registered, not yet seen by the pinger), ``listed``, ``hidden`` (by an
operator), ``banned`` (its registrations are refused) and ``dropped`` (unreachable for 48 hours;
re-listed by its next successful ping). Only ``listed`` entries are in the list clients load.
"""

from __future__ import annotations

import re
import time
from typing import Any

STATES = ("pending", "listed", "hidden", "banned", "dropped")
DROP_AFTER = 48 * 3600.0
RELIABILITY_WINDOW = 7 * 86400.0
_ADDR = re.compile(r"(?:(dchub|nmdcs)://)?(\[[0-9A-Fa-f:.]+\]|[A-Za-z0-9.-]+)(?::(\d{1,5}))?/?")

FIELDS = ("name", "description", "users", "share", "min_share", "min_slots", "max_hubs",
          "max_users", "software", "owner", "operators", "bots", "country", "encoding", "kp",
          "topic", "errcode")


def normalise(address: str) -> str:
    """``host``, ``host:port`` or a ``dchub://`` / ``nmdcs://`` URI as ``scheme://host:port``:
    ``dchub`` without a scheme and 411 without a port, as [DCNF] does; the host in lower case.
    Raises ValueError for anything else."""
    a = address.strip()
    a = a.split("?", 1)[0]  # no ?kp= in the key: the keyprint is a field
    m = _ADDR.fullmatch(a)
    if m is None:
        raise ValueError(f"{address!r} is not a hub address")
    port = int(m.group(3) or 411)
    if not 1 <= port <= 65535:
        raise ValueError(f"{address!r}: bad port")
    return f"{m.group(1) or 'dchub'}://{m.group(2).lower()}:{port}"


class Store:
    def __init__(self, url: str, *, clock=time.time):
        import sqlalchemy as sa

        self.sa, self.clock = sa, clock
        self.engine = sa.create_engine(url)
        if url.startswith("sqlite"):
            @sa.event.listens_for(self.engine, "connect")
            def _pragmas(conn, _record):  # as the connection log's: no sync on every commit
                cur = conn.cursor()
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA synchronous=NORMAL")
                cur.close()
        md = sa.MetaData()
        cols = [sa.Column("address", sa.String(300), primary_key=True),
                sa.Column("state", sa.String(16), nullable=False, index=True),
                sa.Column("source", sa.String(64)),
                sa.Column("registered_at", sa.Float), sa.Column("updated_at", sa.Float),
                sa.Column("last_ping_at", sa.Float), sa.Column("last_ok_at", sa.Float),
                sa.Column("failing_since", sa.Float), sa.Column("fails", sa.Integer, default=0),
                sa.Column("next_ping_at", sa.Float, index=True),
                sa.Column("last_error", sa.Text)]
        for f in FIELDS:
            kind = sa.BigInteger if f in ("users", "share", "min_share", "min_slots", "max_hubs",
                                          "max_users", "operators", "bots", "errcode") else sa.Text
            cols.append(sa.Column(f, kind))
        self.entries = sa.Table("hublist_entries", md, *cols)
        self.pings = sa.Table("hublist_pings", md,
                              sa.Column("id", sa.Integer, primary_key=True),
                              sa.Column("address", sa.String(300), nullable=False, index=True),
                              sa.Column("at", sa.Float, nullable=False),
                              sa.Column("ok", sa.Boolean, nullable=False))
        md.create_all(self.engine)

    # -- entries -----------------------------------------------------------------------------------

    def get(self, address: str) -> dict | None:
        e = self.entries
        with self.engine.connect() as c:
            r = c.execute(self.sa.select(e).where(e.c.address == address)).first()
        return dict(r._mapping) if r is not None else None

    def all(self, state: str | None = None) -> list[dict]:
        e = self.entries
        q = self.sa.select(e).order_by(e.c.address)
        if state:
            q = q.where(e.c.state == state)
        with self.engine.connect() as c:
            return [dict(r._mapping) for r in c.execute(q)]

    def register(self, address: str, source: str, fields: dict) -> dict:
        """A hub's push (§1): created ``pending``, or updated; a banned one is left alone."""
        now = self.clock()
        e = self.entries
        old = self.get(address)
        vals = {k: v for k, v in fields.items() if k in FIELDS}
        with self.engine.begin() as c:
            if old is None:
                c.execute(e.insert().values(address=address, state="pending", source=source,
                                            registered_at=now, updated_at=now, next_ping_at=now,
                                            fails=0, **vals))
            else:
                c.execute(e.update().where(e.c.address == address).values(
                    source=source, updated_at=now, **vals))
        return self.get(address)

    def set_state(self, address: str, state: str) -> dict | None:
        if state not in STATES:
            raise ValueError(f"state {state!r}: one of {', '.join(STATES)}")
        e = self.entries
        with self.engine.begin() as c:
            n = c.execute(e.update().where(e.c.address == address).values(
                state=state, updated_at=self.clock())).rowcount
        return self.get(address) if n else None

    def delete(self, address: str) -> bool:
        with self.engine.begin() as c:
            c.execute(self.pings.delete().where(self.pings.c.address == address))
            return bool(c.execute(self.entries.delete().where(
                self.entries.c.address == address)).rowcount)

    # -- pings -------------------------------------------------------------------------------------

    def due(self, now: float, limit: int) -> list[dict]:
        e = self.entries
        q = (self.sa.select(e).where(e.c.state.in_(("pending", "listed", "dropped")),
                                     e.c.next_ping_at <= now)
             .order_by(e.c.next_ping_at).limit(limit))
        with self.engine.connect() as c:
            return [dict(r._mapping) for r in c.execute(q)]

    def record_ping(self, address: str, ok: bool, fields: dict, next_at: float,
                    error: str = "") -> dict | None:
        """One ping's outcome: the fields it read, the next ping, and the state it implies."""
        now = self.clock()
        old = self.get(address)
        if old is None:
            return None
        vals: dict[str, Any] = {k: v for k, v in fields.items() if k in FIELDS}
        vals.update(last_ping_at=now, next_ping_at=next_at, last_error=error or None)
        state = old["state"]
        if ok:
            vals.update(last_ok_at=now, failing_since=None, fails=0)
            if state in ("pending", "dropped"):
                state = "listed"
        else:
            since = old["failing_since"] or now
            vals.update(failing_since=since, fails=(old["fails"] or 0) + 1)
            if state == "listed" and now - since >= DROP_AFTER:
                state = "dropped"
        vals["state"] = state
        e = self.entries
        with self.engine.begin() as c:
            c.execute(e.update().where(e.c.address == address).values(**vals))
            c.execute(self.pings.insert().values(address=address, at=now, ok=ok))
            c.execute(self.pings.delete().where(self.pings.c.at < now - RELIABILITY_WINDOW))
        return self.get(address)

    def reliability(self, address: str) -> float | None:
        """The share of the last 7 days' pings that succeeded, 0-100; None with no pings."""
        p = self.pings
        since = self.clock() - RELIABILITY_WINDOW
        with self.engine.connect() as c:
            rows = c.execute(self.sa.select(p.c.ok).where(p.c.address == address,
                                                          p.c.at >= since)).all()
        if not rows:
            return None
        return 100.0 * sum(1 for (ok,) in rows if ok) / len(rows)

    def close(self) -> None:
        self.engine.dispose()
