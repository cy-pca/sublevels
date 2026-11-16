"""The limits tables (P1 §6, P2 §1/§4/§5, P3 §1/§2, P4 §1/§2, P5 §3) and the rate limiter."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Limits:
    """Every per-connection limit, as a hub setting; the defaults are the spec's."""

    # P1 §6
    login_message_max: int = 4 * 1024
    online_message_max: int = 64 * 1024
    malformed_per_minute: int = 20
    received_bytes: int = 256 * 1024  # per received_window, online
    received_window: float = 10.0
    output_queue_max: int = 4 * 1024 * 1024
    # P2 §1, §4, §5
    login_timeout: float = 30.0
    bad_password_count: int = 5
    bad_password_window: float = 600.0
    myinfo_interval: float = 10.0
    # P3 §1, §2
    bad_sender_lines: int = 3
    chat_text_max: int = 2048
    chat_lines: int = 5  # per chat_window, with chat_burst back to back
    chat_window: float = 10.0
    chat_burst: int = 3
    pm_text_max: int = 2048
    pm_count: int = 10  # per pm_window
    pm_window: float = 10.0
    # P4 §1, §2
    search_count: int = 1  # per search_window, with search_burst back to back
    search_window: float = 10.0
    search_burst: int = 2
    search_notice_interval: float = 60.0
    sr_per_target: int = 10  # results from one responder to one target
    sr_window: float = 30.0  # after the target's last search
    # P5 §3
    ctm_count: int = 20  # $ConnectToMe and $RevConnectToMe per ctm_window
    ctm_per_remote: int = 5
    ctm_window: float = 10.0


class Bucket:
    """A token bucket: ``count`` events per ``window`` seconds over time, at most ``burst`` back to
    back. ``allow(now)`` takes a token if there is one."""

    __slots__ = ("rate", "burst", "tokens", "at")

    def __init__(self, count: int, window: float, burst: int | None = None, now: float = 0.0):
        self.rate = count / window
        self.burst = float(burst if burst is not None else count)
        self.tokens = self.burst
        self.at = now

    def allow(self, now: float) -> bool:
        self.tokens = min(self.burst, self.tokens + (now - self.at) * self.rate)
        self.at = now
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False


class Window:
    """At most ``count`` events in any ``window`` seconds (a sliding window of timestamps)."""

    __slots__ = ("count", "window", "times")

    def __init__(self, count: int, window: float):
        self.count = count
        self.window = window
        self.times: list[float] = []

    def allow(self, now: float) -> bool:
        cut = now - self.window
        if self.times and self.times[0] <= cut:
            self.times = [t for t in self.times if t > cut]
        if len(self.times) >= self.count:
            return False
        self.times.append(now)
        return True

    def room(self, now: float) -> bool:
        cut = now - self.window
        return sum(1 for t in self.times if t > cut) < self.count

    def hit(self, now: float) -> int:
        """Record an event regardless, and return how many are in the window now."""
        cut = now - self.window
        self.times = [t for t in self.times if t > cut]
        self.times.append(now)
        return len(self.times)


class Rate:
    """At most ``count`` in ``window`` seconds, with a burst of ``burst``: no more than ``count`` in
    any window (a sliding window), and no more than ``burst`` back to back (a token bucket refilled
    at ``count / window``)."""

    __slots__ = ("window", "bucket")

    def __init__(self, count: int, window: float, burst: int | None = None, now: float = 0.0):
        self.window = Window(count, window)
        self.bucket = Bucket(count, window, burst if burst is not None else count, now)

    def allow(self, now: float) -> bool:
        if not self.window.room(now):
            return False
        if not self.bucket.allow(now):
            return False
        self.window.allow(now)
        return True
