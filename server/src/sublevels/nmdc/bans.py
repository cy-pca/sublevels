"""Bans (P6 §4): who may not connect, until when, and why.

A ban's target is ``nick:<nick>``, ``id:<client id>``, ``ip:<address>``, ``net:<cidr>`` or
``account:<name>`` (P6 §3). A ``nick:`` ban made while the user is online also records its client
id, so it holds at connect too. Bans have a start, an end or none (permanent), a reason and who
made it. With a data directory they are kept in ``<data_dir>/bans.json`` and survive restarts; an
expired ban stops applying and stays in the file, marked ``expired`` in listings, until it is
removed.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import re
import tempfile
import time
from dataclasses import asdict, dataclass, field

log = logging.getLogger("sublevels.bans")

KINDS = ("nick", "id", "ip", "net", "account")
_DURATION = re.compile(r"(\d+)([mhd])")
_UNIT = {"m": 60, "h": 3600, "d": 86400}


class BanError(ValueError):
    pass


def parse_target(text: str) -> tuple[str, str]:
    """``kind:value`` (a bare nick is ``nick:``), checked and normalised."""
    kind, sep, value = text.partition(":")
    if not sep or kind not in KINDS:
        kind, value = "nick", text
    value = value.strip()
    if not value:
        raise BanError("no target")
    if kind == "ip":
        try:
            value = str(ipaddress.ip_address(value))
        except ValueError:
            raise BanError(f"{value!r} is not an address") from None
    elif kind == "net":
        try:
            value = str(ipaddress.ip_network(value, strict=False))
        except ValueError:
            raise BanError(f"{value!r} is not a network") from None
    return kind, value


def parse_duration(text: str) -> float | None:
    """``<n>m``, ``<n>h``, ``<n>d`` as seconds, or None for ``perm``."""
    if text == "perm":
        return None
    m = _DURATION.fullmatch(text)
    if m is None or int(m.group(1)) == 0:
        raise BanError(f"{text!r} is not a duration (<n>m, <n>h, <n>d or perm)")
    return float(int(m.group(1)) * _UNIT[m.group(2)])


@dataclass
class Ban:
    id: int
    kind: str
    value: str
    start: float
    end: float | None  # None: permanent
    reason: str
    by: str
    client_id: str = ""  # for a nick: ban, the user's client id when it was made
    extra: dict = field(default_factory=dict)

    @property
    def target(self) -> str:
        return f"{self.kind}:{self.value}"

    def active(self, now: float) -> bool:
        return self.end is None or now < self.end

    def as_dict(self, now: float) -> dict:
        return {**asdict(self), "target": self.target, "permanent": self.end is None,
                "expired": not self.active(now)}


def until_text(ban: Ban) -> str:
    """The refusal line's ``until <time>`` or ``permanently`` (P6 §4)."""
    if ban.end is None:
        return "permanently"
    return "until " + time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(ban.end))


class Bans:
    def __init__(self, data_dir: str = "", *, wall=time.time):
        self.wall = wall
        self.path = os.path.join(data_dir, "bans.json") if data_dir else ""
        self.bans: dict[int, Ban] = {}
        self.next_id = 1
        if self.path and os.path.exists(self.path):
            try:
                with open(self.path, encoding="utf-8") as f:
                    data = json.load(f)
                for b in data.get("bans", []):
                    ban = Ban(**b)
                    self.bans[ban.id] = ban
                self.next_id = max([int(data.get("next_id", 1))] + [b + 1 for b in self.bans])
            except (OSError, ValueError, TypeError) as e:
                log.error("bans: could not read %s (%s): starting with none", self.path, e)

    def _save(self) -> None:
        if not self.path:
            return
        d = os.path.dirname(self.path)
        os.makedirs(d, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".bans.", dir=d)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"next_id": self.next_id, "bans": [asdict(b) for b in self.bans.values()]},
                      f, indent=1)
        os.replace(tmp, self.path)

    def add(self, kind: str, value: str, seconds: float | None, reason: str, by: str,
            client_id: str = "", addresses: list[str] | None = None) -> Ban:
        """``addresses``: for an ``id:`` or ``nick:`` ban, where the id was last seen; the ban
        covers them too (IDENTITY.md 3.8, D9: an NMDC id is only a hash of its addresses)."""
        now = self.wall()
        ban = Ban(self.next_id, kind, value, round(now, 3),
                  None if seconds is None else round(now + seconds, 3), reason, by, client_id,
                  {"addresses": list(addresses)} if addresses else {})
        self.next_id += 1
        self.bans[ban.id] = ban
        self._save()
        return ban

    def remove(self, ban_id: int) -> Ban | None:
        ban = self.bans.pop(ban_id, None)
        if ban is not None:
            self._save()
        return ban

    def find(self, kind: str, value: str) -> list[Ban]:
        key = value.casefold() if kind in ("nick", "account") else value
        return [b for b in self.bans.values() if b.kind == kind
                and (b.value.casefold() if kind in ("nick", "account") else b.value) == key]

    def in_force(self, text: str = "") -> list[Ban]:
        now = self.wall()
        return [b for b in self.bans.values() if b.active(now)
                and (not text or text.casefold() in (b.target + " " + b.reason).casefold())]

    # -- the checks (P6 §4) ------------------------------------------------------------------------

    def at_connect(self, ip: str, client_id: str) -> Ban | None:
        """``ip:``, ``net:`` and ``id:`` bans (and ``nick:`` bans that recorded this id)."""
        now = self.wall()
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            addr = None
        for b in self.bans.values():
            if not b.active(now):
                continue
            if b.kind == "ip" and b.value == ip:
                return b
            if b.kind == "net" and addr is not None and addr in ipaddress.ip_network(b.value):
                return b
            if b.kind == "id" and b.value == client_id:
                return b
            if b.kind == "nick" and b.client_id and b.client_id == client_id:
                return b
            if b.kind in ("id", "nick") and ip in b.extra.get("addresses", ()):
                return b  # an id's ban covers the addresses it was last seen at (D9)
        return None

    def at_nick(self, nick: str, registered: bool) -> Ban | None:
        """``nick:`` bans, and ``account:`` bans for a registered nick."""
        now = self.wall()
        k = nick.casefold()
        for b in self.bans.values():
            if b.active(now) and (b.kind == "nick" or (registered and b.kind == "account")) \
                    and b.value.casefold() == k:
                return b
        return None

    def at_account(self, account: str) -> Ban | None:
        now = self.wall()
        k = account.casefold()
        return next((b for b in self.bans.values() if b.active(now) and b.kind == "account"
                     and b.value.casefold() == k), None)

    def matches(self, ban: Ban, s) -> bool:
        """Whether ``ban`` covers session ``s`` (to close its connections when it is made)."""
        if ban.kind in ("id", "nick") and s.ip in ban.extra.get("addresses", ()):
            return True
        if ban.kind == "nick":
            return s.nick.casefold() == ban.value.casefold() or \
                bool(ban.client_id and ban.client_id == s.identity)
        if ban.kind == "account":
            return bool(s.account) and s.account.casefold() == ban.value.casefold()
        if ban.kind == "id":
            return s.identity == ban.value
        if ban.kind == "ip":
            return s.ip == ban.value
        try:
            return ipaddress.ip_address(s.ip) in ipaddress.ip_network(ban.value)
        except ValueError:
            return False
