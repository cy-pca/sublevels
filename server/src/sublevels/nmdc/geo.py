"""Where a connection comes from (roadmap D11), and the location policy (D12).

The lookup is a plain function of a reader and an address (``lookup``), so the same code can be
arisrvx's ``@host.geolocate`` hook once ARISRV_IDENTITY ID4c exists. ``GeoDB`` holds a MaxMind
GeoLite2 City database (read with ``maxminddb``, on cvcpkg as ``maxminddb-cp312``) from the
operator's path, and reopens it when the file changes. MaxMind's license does not allow
redistribution: the database is never in this repository, a package or CI. The operator downloads
it with their own MaxMind account, and the hub shows MaxMind's attribution (``ATTRIBUTION``).

The policy is coarse, and is not presented as more: a VPN or a proxy defeats it.
"""

from __future__ import annotations

import ipaddress
import logging
import os
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

    @classmethod
    def from_config(cls, cfg) -> "LocationPolicy":
        if cfg.location_policy not in ("off", "deny", "allow"):
            raise ValueError(f"SUBLEVELS_LOCATION_POLICY: {cfg.location_policy!r} is not off, "
                             "deny or allow")
        if cfg.location_unknown not in ("admit", "refuse"):
            raise ValueError(f"SUBLEVELS_LOCATION_UNKNOWN: {cfg.location_unknown!r} is not admit "
                             "or refuse")
        return cls(cfg.location_policy, frozenset(c.strip().upper() for c in cfg.location_countries
                                                  if c.strip()),
                   tuple(ipaddress.ip_network(n.strip(), strict=False)
                         for n in cfg.location_exempt if n.strip()), cfg.location_unknown)

    def refuses(self, ip: str, loc: Location) -> str:
        """Why the connection is refused ("" when it is not): the line its client is sent."""
        if self.mode == "off":
            return ""
        try:
            addr = ipaddress.ip_address(ip)
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
