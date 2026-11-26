"""Where a connection comes from (roadmap D11), and the location policy (D12).

The lookup is a plain function of a reader and an address (``lookup``), so the same code can be
arisrvx's ``@host.geolocate`` hook once ARISRV_IDENTITY ID4c exists. ``GeoDB`` holds a MaxMind
GeoLite2 City database (read with ``maxminddb``, on cvcpkg as ``maxminddb-cp312``) from the
operator's path, and reopens it when the file changes. MaxMind's license does not allow
redistribution: the database is never in this repository, a package or CI. The operator downloads
it with their own MaxMind account, and the hub shows MaxMind's attribution (``ATTRIBUTION``).

The policy is coarse, and is not presented as more: a VPN or a proxy defeats it.

How the hub applies it (hub.py): before $Lock it looks the address up. If the policy refuses it and
the hub has no accounts, the connection gets the policy's line and is closed there. If the hub has
accounts, the connection is held instead: it may go on only as a registered nick that the policy
exempts (an ``op`` or ``admin`` by default, ``location_exempt_accounts``, or an account flagged
``location_exempt``), and only by giving that account's password. Any other nick gets the same
line at ``$ValidateNick`` and is closed, before it sees the hub's features or any user. A
held connection cannot skip the nick (the login states do not allow it), and the login timeout
still applies. Admitted and exempted connections and refusals are all in the operator log.

A policy that could not work is refused at start: no database, or one that did not load (the
server checks), no countries, or codes that are not ISO alpha-2. Once loaded, a database file
that goes missing or bad keeps the one already loaded, so the policy does not open by accident.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Callable

log = logging.getLogger("sublevels.geo")

ATTRIBUTION = ("This hub includes GeoLite2 data created by MaxMind, available from "
               "https://www.maxmind.com.")
RECHECK = 30.0  # seconds between looks at the database file's modification time


@dataclass(frozen=True)
class Location:
    country: str = ""  # ISO 3166-1 alpha-2, upper case; "" when unknown
    region: str = ""
    city: str = ""

    @property
    def known(self) -> bool:
        return bool(self.country)

    def as_dict(self) -> dict:
        return {"country": self.country, "region": self.region, "city": self.city}


UNKNOWN = Location()


def _name(rec: Any) -> str:
    names = rec.get("names") if isinstance(rec, dict) else None
    return str(names.get("en", "")) if isinstance(names, dict) else ""


def lookup(reader: Any, ip: str) -> Location:
    """``ip``'s country, region and city from a GeoLite2 City (or Country) ``reader`` (anything
    with ``get(ip) -> dict | None``). Private, loopback and unknown addresses are UNKNOWN."""
    if reader is None:
        return UNKNOWN
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return UNKNOWN
    if addr.version == 6 and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_multicast:
        return UNKNOWN
    try:
        rec = reader.get(str(addr))
    except Exception:  # a damaged database (maxminddb's InvalidDatabaseError) places nothing
        return UNKNOWN
    if not isinstance(rec, dict):
        return UNKNOWN
    country = rec.get("country") or rec.get("registered_country") or {}
    code = str(country.get("iso_code", "")).upper() if isinstance(country, dict) else ""
    subs = rec.get("subdivisions")
    region = _name(subs[0]) if isinstance(subs, list) and subs else ""
    return Location(code, region, _name(rec.get("city") or {}))


class GeoDB:
    """The operator's database at ``path``, reopened when the file changes ("" for none)."""

    def __init__(self, path: str = "", *, clock: Callable[[], float],
                 opener: Callable[[str], Any] | None = None):
        self.path = path
        self.clock = clock
        self.opener = opener or _open_maxminddb
        self.reader = None
        self.mtime = None
        self.checked = -1e18
        self.loaded_at = 0.0
        self.error = ""
        if path:
            self._check()

    @property
    def enabled(self) -> bool:
        return bool(self.path)

    def _check(self) -> None:
        self.checked = self.clock()
        try:
            mtime = os.stat(self.path).st_mtime_ns
        except OSError as e:
            if self.reader is None and self.error != str(e):
                log.warning("geolocation: no database at %s (%s): every address is unknown",
                            self.path, e)
            self.error = str(e)
            return
        if mtime == self.mtime:
            return
        try:
            reader = self.opener(self.path)
        except Exception as e:  # a damaged or half-written file: keep the old reader
            self.error = f"{type(e).__name__}: {e}"
            log.warning("geolocation: could not open %s (%s)", self.path, self.error)
            return
        old, self.reader, self.mtime = self.reader, reader, mtime
        self.loaded_at, self.error = self.clock(), ""
        log.info("geolocation: %s %s", "reloaded" if old is not None else "loaded", self.path)
        if old is not None and hasattr(old, "close"):
            old.close()

    def lookup(self, ip: str) -> Location:
        if not self.path:
            return UNKNOWN
        if self.clock() - self.checked >= RECHECK:
            self._check()
        return lookup(self.reader, ip)

    def status(self) -> dict:
        return {"enabled": self.enabled, "path": self.path, "loaded": self.reader is not None,
                "error": self.error or None, "attribution": ATTRIBUTION if self.enabled else None}


def _open_maxminddb(path: str):
    import maxminddb

    return maxminddb.open_database(path)


@dataclass(frozen=True)
class LocationPolicy:
    """D12: deny the listed countries, or admit only them, or neither. Exempt networks bypass it;
    addresses the database cannot place follow ``unknown`` (admitted by default)."""

    mode: str = "off"  # "off", "deny" or "allow"
    countries: frozenset = frozenset()
    exempt: tuple = ()  # ipaddress networks
    unknown: str = "admit"  # or "refuse"
    exempt_roles: frozenset = frozenset({"op", "admin"})
    exempt_accounts: frozenset = frozenset()  # casefolded

    @property
    def on(self) -> bool:
        return self.mode != "off"

    def exempts(self, accounts, nick: str) -> bool:
        """Whether the registered ``nick`` may log in from a refused location: by its role, by
        name, or by the accounts file's ``location_exempt`` flag."""
        if not accounts.is_registered(nick):
            return False
        role_of = getattr(accounts, "role", None)
        role = role_of(nick) if role_of is not None else (
            "op" if accounts.is_operator(nick) else "user")
        flag = getattr(accounts, "location_exempt", None)
        return (role in self.exempt_roles or nick.casefold() in self.exempt_accounts
                or bool(flag is not None and flag(nick)))

    @classmethod
    def from_config(cls, cfg) -> "LocationPolicy":
        """The policy ``cfg`` sets. A policy that could not do what it says is a configuration
        error, so the hub does not start with it: one without a database to place addresses (it
        would admit or refuse everyone), a deny- or allowlist without countries, or a code that
        is not ISO 3166-1 alpha-2."""
        if cfg.location_policy not in ("off", "deny", "allow"):
            raise ValueError(f"SUBLEVELS_LOCATION_POLICY: {cfg.location_policy!r} is not off, "
                             "deny or allow")
        if cfg.location_unknown not in ("admit", "refuse"):
            raise ValueError(f"SUBLEVELS_LOCATION_UNKNOWN: {cfg.location_unknown!r} is not admit "
                             "or refuse")
        countries = frozenset(c.strip().upper() for c in cfg.location_countries if c.strip())
        bad = sorted(c for c in countries if not re.fullmatch(r"[A-Z]{2}", c))
        if bad:
            raise ValueError(f"SUBLEVELS_LOCATION_COUNTRIES: {', '.join(bad)} not two-letter "
                             "ISO 3166-1 codes")
        roles = frozenset(r.strip() for r in cfg.location_exempt_roles if r.strip())
        if roles - {"user", "op", "admin"}:
            raise ValueError(f"SUBLEVELS_LOCATION_EXEMPT_ROLES: {sorted(roles)} are not roles")
        if cfg.location_policy != "off":
            if not cfg.geoip_db:
                raise ValueError(f"SUBLEVELS_LOCATION_POLICY={cfg.location_policy} needs "
                                 "SUBLEVELS_GEOIP_DB: without a database no address can be "
                                 "placed")
            if not countries:
                raise ValueError(f"SUBLEVELS_LOCATION_POLICY={cfg.location_policy} needs "
                                 "SUBLEVELS_LOCATION_COUNTRIES")
        try:
            nets = tuple(ipaddress.ip_network(n.strip(), strict=False)
                         for n in cfg.location_exempt if n.strip())
        except ValueError as e:
            raise ValueError(f"SUBLEVELS_LOCATION_EXEMPT: {e}") from None
        return cls(cfg.location_policy, countries, nets, cfg.location_unknown, roles,
                   frozenset(a.strip().casefold() for a in cfg.location_exempt_accounts
                             if a.strip()))

    def refuses(self, ip: str, loc: Location) -> str:
        """Why the connection is refused ("" when it is not): the line its client is sent."""
        if self.mode == "off":
            return ""
        try:
            addr = ipaddress.ip_address(ip)
            if addr.version == 6 and addr.ipv4_mapped is not None:  # ::ffff:a.b.c.d
                addr = addr.ipv4_mapped
            if any(addr in n for n in self.exempt):
                return ""
        except ValueError:
            pass
        if not loc.known:
            return ("This hub does not take connections from addresses it cannot place."
                    if self.unknown == "refuse" else "")
        listed = loc.country in self.countries
        if (self.mode == "deny" and listed) or (self.mode == "allow" and not listed):
            return f"This hub does not take connections from your country ({loc.country})."
        return ""
