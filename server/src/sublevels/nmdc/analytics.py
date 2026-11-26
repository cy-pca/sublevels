"""Analytics (roadmap D14): the hub's numbers as time series.

Every 10 s the hub is sampled:
- **gauges**: users, connections by protocol, TLS connections, logins in progress, pingers, bans in
  force, total share, and online users by country and by client;
- **counters**, as the change since the last sample: logins, refusals (all, and by reason), chat
  lines and their fan-out, private messages, searches and their fan-out, connection requests,
  bytes in and out, pinger visits and operator actions.

History is kept at three resolutions: every 10 s for a day, each minute for 30 days, and each hour
after that. A minute or hour holds the mean of a gauge and the sum of a counter. With a connection
log database (``connlog.ConnLog``) the history is stored there and survives restarts; without one it
is kept in memory, at 10 s and 1 min, for the run.

Who sees what: the live user count is public (D14), so ``sublevels.hubs.<id>.series.users`` (the
last hour at 10 s) is published for every native client to plot. Everything else is for operators:
``GET /admin/analytics`` and the ``sublevels.analytics`` room-call for callers with the admin role.
An operator-only live series on the state plane needs a read scope for operators, which needs the
authenticate hook (S5): until then operators read it on demand.
"""

from __future__ import annotations

import logging
import time
from array import array
from collections import Counter, defaultdict

log = logging.getLogger("sublevels.analytics")

STEP = 10.0
RESOLUTIONS = {"10s": 10.0, "1m": 60.0, "1h": 3600.0}
RETENTION = {"10s": 86400.0, "1m": 30 * 86400.0, "1h": None}  # seconds; None: kept
PUBLIC_POINTS = 360  # the published user-count series: an hour at 10 s
TOP = 20  # countries and clients kept as series of their own

COUNTERS = {  # series name -> the hub counters it sums (totals, or "refusals")
    "logins": ("logins",),
    "chat": ("chat",),
    "chat_fanout": ("chat_fanout",),
    "pm": ("pm",),
    "search": ("search_active", "search_passive"),
    "search_fanout": ("search_fanout",),
    "connect_requests": ("ctm", "rctm"),
    "bytes_in": ("bytes_in",),
    "bytes_out": ("bytes_out",),
    "pinger_visits": ("pinger_visits",),
}


def is_counter(metric: str) -> bool:
    return metric in COUNTERS or metric.startswith("refusal") or metric == "op_actions"


class Ring:
    """Fixed-step series in memory: ``capacity`` points per metric, oldest dropped."""

    def __init__(self, step: float, capacity: int):
        self.step, self.capacity = step, capacity
        self.points: dict[str, tuple[array, array]] = {}

    def add(self, t: float, values: dict[str, float]) -> None:
        for m, v in values.items():
            ts, vs = self.points.setdefault(m, (array("d"), array("d")))
            ts.append(t)
            vs.append(v)
            if len(ts) > self.capacity:
                del ts[: len(ts) - self.capacity]
                del vs[: len(vs) - self.capacity]

    def query(self, metric: str, since: float, until: float) -> list[tuple[float, float]]:
        ts, vs = self.points.get(metric, ((), ()))
        return [(t, v) for t, v in zip(ts, vs) if since <= t <= until]

    def metrics(self) -> list[str]:
        return sorted(self.points)


class Analytics:
    def __init__(self, hub, *, clock=time.time):
        self.hub = hub
        self.clock = clock
        # The counters as of the last sample. They start with the hub, so the first sample counts
        # everything since the start.
        self.last_totals: dict[str, int] = {}
        self.rings = {"10s": Ring(STEP, int(86400 / STEP)), "1m": Ring(60.0, 30 * 1440)}
        self.buckets: dict[str, tuple[float, dict[str, list[float]]]] = {}  # res -> (start, sums)
        self.timer = None

    # -- sampling ----------------------------------------------------------------------------------

    def sample(self) -> dict[str, float]:
        """One sample: the gauges now, and each counter's change since the last sample."""
        h = self.hub
        st = h.stats()
        snap = h.counters.snapshot()
        totals = dict(snap["totals"])
        refusals = dict(snap["refusals"])
        flat = {**totals, **{f"refusal.{k}": v for k, v in refusals.items()}}
        flat["refusals"] = sum(refusals.values())
        flat["op_actions"] = sum(v for k, v in totals.items() if k.startswith("op_"))
        prev, self.last_totals = self.last_totals, flat
        out: dict[str, float] = {
            "users": st["users"], "pingers": st["pingers"], "bans": st["bans"],
            "share": st["share"], "tls_connections": st["tls_connections"],
            "logging_in": st["logging_in"],
        }
        for proto in ("nmdc", "nmdcs"):
            out[f"connections.{proto}"] = st["connections"].get(proto, 0)
        for cc, n in Counter(u.location.country or "?" for u in h.users.values()).most_common(TOP):
            out[f"country.{cc}"] = n
        for client, n in list(st["clients"].items())[:TOP]:
            out[f"client.{client}"] = n
        for name, parts in COUNTERS.items():
            out[name] = sum(flat.get(p, 0) - prev.get(p, 0) for p in parts)
        for k in [k for k in flat if k.startswith("refusal")]:
            out[k] = flat[k] - prev.get(k, 0)
        out["op_actions"] = flat["op_actions"] - prev.get("op_actions", 0)
        return out

    def tick(self) -> dict[str, float]:
        """Sample, keep it at 10 s, roll it into the minute and the hour, store, publish."""
        now = self.clock()
        t = now - now % STEP
        values = self.sample()
        self.rings["10s"].add(t, values)
        self._store("10s", t, values)
        for res in ("1m", "1h"):
            self._roll(res, t, values)
        self._publish()
        return values

    def _roll(self, res: str, t: float, values: dict[str, float]) -> None:
        size = RESOLUTIONS[res]
        start = t - t % size
        cur = self.buckets.get(res)
        if cur is not None and cur[0] != start:
            self._close(res, *cur)
            cur = None
        if cur is None:
            cur = self.buckets[res] = (start, defaultdict(list))
        for m, v in values.items():
            cur[1][m].append(v)

    def _close(self, res: str, start: float, sums: dict[str, list[float]]) -> None:
        values = {m: (sum(vs) if is_counter(m) else sum(vs) / len(vs)) for m, vs in sums.items()}
        if res in self.rings:
            self.rings[res].add(start, values)
        self._store(res, start, values)
        if res == "1h" and self.hub.connlog is not None:  # once an hour: drop what is too old
            self.hub.connlog.prune_samples(self.clock())

    def _store(self, res: str, t: float, values: dict[str, float]) -> None:
        cl = self.hub.connlog
        if cl is not None and values:
            cl.record_samples(self.hub.config.hub_id, res, t, values)

    def _publish(self) -> None:
        tree = self.hub.tree
        if tree is None or not hasattr(tree, "set_series"):
            return
        users = [v for _, v in self.rings["10s"].query("users", 0, float("inf"))]
        tree.set_series(f"{self.hub.tree_path}.series.users", users[-PUBLIC_POINTS:], fmt=".0f")

    # -- the timer ---------------------------------------------------------------------------------

    def start(self, call_later=None) -> None:
        call_later = call_later or self.hub.call_later

        def again():
            try:
                self.tick()
            except Exception:
                log.exception("analytics: a sample failed")
            self.timer = call_later(STEP, again)

        self.timer = call_later(STEP, again)

    def stop(self) -> None:
        if self.timer is not None:
            self.timer.cancel()
            self.timer = None
        for res, cur in list(self.buckets.items()):  # the partial minute and hour
            self._close(res, *cur)
        self.buckets.clear()

    # -- reading -----------------------------------------------------------------------------------

    def query(self, metric: str, resolution: str = "10s", since: float | None = None,
              until: float | None = None) -> dict:
        if resolution not in RESOLUTIONS:
            raise ValueError(f"resolution {resolution!r}: one of {', '.join(RESOLUTIONS)}")
        now = self.clock()
        until = now if until is None else until
        since = until - 3600.0 if since is None else since
        cl = self.hub.connlog
        if cl is not None:
            points = cl.query_samples(self.hub.config.hub_id, resolution, metric, since, until)
        elif resolution in self.rings:
            points = self.rings[resolution].query(metric, since, until)
        else:
            points = []
        return {"metric": metric, "resolution": resolution, "since": since, "until": until,
                "kind": "counter" if is_counter(metric) else "gauge",
                "points": [[t, v] for t, v in points]}

    def metrics(self) -> list[str]:
        return self.rings["10s"].metrics()
