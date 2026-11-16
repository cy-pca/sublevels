"""D14's counters, in memory (M1). Stored history and the admin charts are M2's."""

from __future__ import annotations

import time
from collections import Counter


class Counters:
    """Totals since the hub started. Live values (connections, users, the client mix) are the hub's
    own and are added by ``Hub.stats()``."""

    def __init__(self, clock=time.time):
        self.started = clock()
        self.totals: Counter[str] = Counter()
        self.refusals: Counter[str] = Counter()  # by reason
        self.drops: Counter[str] = Counter()  # messages dropped, by reason
        self.closes: Counter[str] = Counter()  # connections closed, by reason

    def add(self, name: str, n: int = 1) -> None:
        self.totals[name] += n

    def refuse(self, reason: str) -> None:
        self.refusals[reason] += 1

    def drop(self, reason: str) -> None:
        self.drops[reason] += 1

    def close(self, reason: str) -> None:
        self.closes[reason] += 1

    def snapshot(self) -> dict:
        return {
            "started": self.started,
            "totals": dict(sorted(self.totals.items())),
            "refusals": dict(sorted(self.refusals.items())),
            "drops": dict(sorted(self.drops.items())),
            "closes": dict(sorted(self.closes.items())),
        }
