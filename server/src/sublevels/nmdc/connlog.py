"""The connection log (roadmap D13): one row per connection, kept across restarts.

Each row has the times in and out, nick, account, client id, address and port, country, region
and city, client and version, protocol and TLS, bytes each way, whether it was a hub-list pinger,
and why it closed. Rows are written when a connection ends; live connections are the hub's own
(``Hub.sessions``), and the admin API shows both.

Rows go to SQLite through SQLAlchemy (``sqlalchemy-cp312`` on cvcpkg), by default
``<data_dir>/connections.sqlite3``. The hub's loop never waits on the disk: rows are queued, and
a writer thread stores them in batches. Once the registry's record (ARISRV_IDENTITY §2.2) is
available, this log stays: the registry keeps records for one run only.
"""

from __future__ import annotations

import logging
import queue
import threading
from typing import Any

log = logging.getLogger("sublevels.connlog")

COLUMNS = ("hub", "opened_at", "closed_at", "nick", "account", "client_id", "address", "port",
           "country", "region", "city", "client", "version", "protocol", "tls", "bytes_in",
           "bytes_out", "pinger", "close_reason")
BATCH = 5000  # rows a transaction takes: connections, operator-log entries, analytics values


class ConnLog:
    def __init__(self, url: str):
        import sqlalchemy as sa

        self.sa = sa
        self.url = url
        self.engine = sa.create_engine(url)
        if url.startswith("sqlite"):
            @sa.event.listens_for(self.engine, "connect")
            def _pragmas(conn, _record):  # readers while the writer writes
                cur = conn.cursor()
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA synchronous=NORMAL")
                cur.close()
        md = sa.MetaData()
        self.table = sa.Table(
            "connections", md,
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("hub", sa.String(64), nullable=False),
            sa.Column("opened_at", sa.Float, nullable=False, index=True),
            sa.Column("closed_at", sa.Float),
            sa.Column("nick", sa.String(64), index=True),
            sa.Column("account", sa.String(64)),
            sa.Column("client_id", sa.String(64), index=True),
            sa.Column("address", sa.String(64), index=True),
            sa.Column("port", sa.Integer),
            sa.Column("country", sa.String(2)),
            sa.Column("region", sa.String(128)),
            sa.Column("city", sa.String(128)),
            sa.Column("client", sa.String(64)),
            sa.Column("version", sa.String(32)),
            sa.Column("protocol", sa.String(16)),
            sa.Column("tls", sa.Boolean),
            sa.Column("bytes_in", sa.Integer),
            sa.Column("bytes_out", sa.Integer),
            sa.Column("pinger", sa.Boolean),
            sa.Column("close_reason", sa.String(128)),
        )
        self.actions = sa.Table(  # the operator log (P6 §7)
            "operator_log", md,
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("at", sa.Float, nullable=False, index=True),
            sa.Column("hub", sa.String(64)),
            sa.Column("action", sa.String(32), index=True),
            sa.Column("actor", sa.String(128), index=True),
            sa.Column("target", sa.String(256), index=True),
            sa.Column("reason", sa.Text),
            sa.Column("duration", sa.Float),
            sa.Column("extra", sa.JSON),
        )
        self.samples = sa.Table(  # analytics (D14): one value per metric per sample
            "analytics", md,
            sa.Column("hub", sa.String(64), nullable=False),
            sa.Column("res", sa.String(4), nullable=False),
            sa.Column("t", sa.Float, nullable=False),
            sa.Column("metric", sa.String(128), nullable=False),
            sa.Column("value", sa.Float, nullable=False),
            sa.Index("analytics_by_metric", "hub", "res", "metric", "t"),
        )
        md.create_all(self.engine)
        self.q: queue.Queue = queue.Queue()
        self.written = 0
        self.thread = threading.Thread(target=self._writer, name="sublevels-connlog", daemon=True)
        self.thread.start()

    def record(self, row: dict) -> None:
        """Queue one ended connection's row (from the hub's loop: never blocks)."""
        self.q.put((self.table, {c: row.get(c) for c in COLUMNS}))

    def record_samples(self, hub: str, res: str, t: float, values: dict) -> None:
        """Queue one analytics sample (D14)."""
        for m, v in values.items():
            self.q.put((self.samples, {"hub": hub, "res": res, "t": t, "metric": m,
                                       "value": float(v)}))

    def prune_samples(self, now: float) -> None:
        """Queue the removal of samples older than their resolution keeps (analytics.RETENTION)."""
        self.q.put(("prune", now))

    def query_samples(self, hub: str, res: str, metric: str, since: float,
                      until: float) -> list[tuple[float, float]]:
        sa, t = self.sa, self.samples
        q = (sa.select(t.c.t, t.c.value).where(t.c.hub == hub, t.c.res == res,
                                               t.c.metric == metric, t.c.t >= since,
                                               t.c.t <= until).order_by(t.c.t))
        with self.engine.connect() as conn:
            return [(r[0], r[1]) for r in conn.execute(q)]  # (Row.t is the row as a tuple)

    def _prune(self, conn, now: float) -> None:
        from .analytics import RETENTION

        for res, keep in RETENTION.items():
            if keep is not None:
                conn.execute(self.samples.delete().where(self.samples.c.res == res,
                                                         self.samples.c.t < now - keep))

    def record_action(self, entry: dict) -> None:
        """Queue one operator-log entry."""
        self.q.put((self.actions, {k: entry.get(k) for k in ("at", "hub", "action", "actor",
                                                             "target", "reason", "duration",
                                                             "extra")}))

    def _writer(self) -> None:
        while True:
            first = self.q.get()
            if first is None:
                return
            rows, stop = [first], False
            while len(rows) < BATCH:
                try:
                    r = self.q.get_nowait()
                except queue.Empty:
                    break
                if r is None:
                    stop = True
                    break
                rows.append(r)
            try:
                with self.engine.begin() as conn:
                    for table in {id(t): t for t, _ in rows if t != "prune"}.values():
                        conn.execute(table.insert(), [r for t, r in rows if t is table])
                    for t, r in rows:
                        if t == "prune":
                            self._prune(conn, r)
                self.written += len(rows)
            except Exception:
                log.exception("connection log: %d row(s) not written", len(rows))
            if stop:
                return

    def flush(self, timeout: float = 10.0) -> None:
        """Write what is queued and stop the writer (at shutdown)."""
        self.q.put(None)
        self.thread.join(timeout)
        self.engine.dispose()

    def query_actions(self, *, actor: str = "", target: str = "", action: str = "",
                      limit: int = 100) -> list[dict[str, Any]]:
        sa, t = self.sa, self.actions
        q = sa.select(t).order_by(t.c.at.desc(), t.c.id.desc()).limit(max(1, min(limit, 1000)))
        for col, v in (("actor", actor), ("target", target), ("action", action)):
            if v:
                q = q.where(t.c[col] == v)
        with self.engine.connect() as conn:
            return [dict(r._mapping) for r in conn.execute(q)]

    def query(self, *, nick: str = "", address: str = "", client_id: str = "",
              country: str = "", limit: int = 100) -> list[dict[str, Any]]:
        """The newest rows first, filtered by any of nick (ignoring case), address, client id and
        country."""
        sa, t = self.sa, self.table
        q = sa.select(t).order_by(t.c.opened_at.desc(), t.c.id.desc()).limit(max(1, min(limit,
                                                                                         1000)))
        if nick:
            q = q.where(sa.func.lower(t.c.nick) == nick.lower())
        if address:
            q = q.where(t.c.address == address)
        if client_id:
            q = q.where(t.c.client_id == client_id)
        if country:
            q = q.where(t.c.country == country.upper())
        with self.engine.connect() as conn:
            return [dict(r._mapping) for r in conn.execute(q)]
