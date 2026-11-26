"""The hub's settings, with the spec's defaults, and how they are read from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace

from .limits import Limits

DEFAULT_MOTD = ("Welcome to this sublevels hub.",
                "This is a test hub: the message of the day is a placeholder.")
DEFAULT_RULES = ("1. Be civil.",
                 "2. No flooding of chat, private messages or searches.",
                 "These rules are a placeholder until the operator writes the hub's own.")
# P9 §1: Verlihub's two push targets [OBS-VH].
DEFAULT_HUBLISTS = ("hublist.te-home.net", "hublist.pwiam.com")


@dataclass(frozen=True)
class HubConfig:
    hub_id: str = "main"  # its node under sublevels.hubs
    room: str = "standup"  # the conference room this hub's users belong to (the relay's --room)
    name: str = "sublevels"
    topic: str = ""
    bot_nick: str = "sublevels"
    bot_description: str = "the hub"
    # The documents' text until an operator edits them (documents.py, D9).
    motd: tuple[str, ...] = DEFAULT_MOTD
    rules: tuple[str, ...] = DEFAULT_RULES
    # Where the hub keeps what must outlive a restart (the edited documents); "" keeps nothing.
    data_dir: str = ""
    codepage: str = "cp1252"  # [D-8]
    max_users: int = 10000  # online and logging in; past it, $HubIsFull
    reserved_nicks: tuple[str, ...] = ()
    strict_key: bool = False  # P1 §5: a wrong $Key closes the connection
    password_requires_tls: bool = True  # P2 §4
    tls_required: bool = False  # P7 §1
    nmdcs_address: str = ""  # the nmdcs:// address the bot names, e.g. nmdcs://hub.example:4111
    # Listeners (P7 §1). "off" for none. Port 411 needs privileges a service may not have.
    nmdc_listen: str = "0.0.0.0:411"
    nmdcs_listen: str = "0.0.0.0:4111"
    tls_cert: str = ""
    tls_key: str = ""
    identity_salt: str = ""  # [D-9]: "" picks a random one per run
    publish_interval: float = 1.0  # seconds between writes of the sublevels.hubs subtree
    # The listen backlog: connections waiting for the loop to accept them. A burst of logins at
    # 6000+ users [D-7] overflows asyncio's default of 100.
    listen_backlog: int = 1024
    # Plain NMDC's transport: "engine" (libcvc's native connection engine, ARIADNE_NET_ENGINE N2:
    # its I/O threads read and write, and a fan-out is one broadcast), "asyncio" (the hub's loop
    # reads and writes every socket: the fallback), or "auto" (the default: the engine when this
    # pycvc has it and no capture_dir is set, else asyncio). NMDCS stays on asyncio until the
    # engine has TLS (N3).
    transport: str = "auto"
    net_threads: int = 2  # the engine's I/O threads, when the hub makes its own (plain python)
    # Interop runs: write each connection's messages, both ways, to <capture_dir>/<id>-<protocol>
    # .log. It records what clients send in clear, passwords included: for test hubs only.
    capture_dir: str = ""
    # Hub lists (P9, roadmap D15). The hub is registered only once it has a public address: the
    # plain-NMDC address users reach it at, "host" or "host:port". Without one, nothing is pushed
    # and $HubINFO gives no address (it never gives a loopback one).
    public_address: str = ""
    description: str = ""  # for lists and $HubINFO; "" uses the topic
    owner: str = ""  # $HubINFO's owner: the operator's contact
    # What the hub asks of users, as lists and $HubINFO advertise it. Enforcing them is the share
    # and slot rules' (D9, later); pingers will be exempt from them (P9 §2).
    min_share: int = 0  # bytes
    min_slots: int = 0
    max_hubs: int = 0
    minshare_prefix: bool = True  # "[MINSHARE:<n>MB] " before a pushed description (P9 §1)
    # Targets: "host[:port][;key=standard|legacy][;method=push|pinger]" (see hublists.Target).
    hublists: tuple[str, ...] = DEFAULT_HUBLISTS
    hublist_interval: float = 900.0  # seconds between pushes to each target
    hublist_jitter: float = 120.0  # up to this much random delay on each
    pinger_time: float = 60.0  # a pinger is closed this long after it logs in (P9 §2)
    # Geolocation (D11): a GeoLite2 City database the operator downloaded ("" for none).
    geoip_db: str = ""
    # Location policy (D12), applied before $Lock: "off", "deny" (the countries) or "allow"
    # (only the countries); exempt networks bypass it; unplaceable addresses "admit" or "refuse".
    location_policy: str = "off"
    location_countries: tuple[str, ...] = ()
    location_exempt: tuple[str, ...] = ()
    location_unknown: str = "admit"
    country_prefix: bool = False  # "[CC] " before descriptions in $MyINFO (D13, P2 §5)
    # The connection log (D13): a SQLAlchemy URL; "" is <data_dir>/connections.sqlite3 when there
    # is a data directory, and "off" keeps none.
    connlog: str = ""
    limits: Limits = field(default_factory=Limits)

    def with_(self, **kw) -> "HubConfig":
        return replace(self, **kw)

    @classmethod
    def from_env(cls, env=None) -> "HubConfig":
        """Settings from ``SUBLEVELS_*`` variables; anything unset keeps its default."""
        env = os.environ if env is None else env
        kw = {}
        text = {"SUBLEVELS_HUB_NAME": "name", "SUBLEVELS_HUB_TOPIC": "topic",
                "SUBLEVELS_BOT_NICK": "bot_nick", "SUBLEVELS_CODEPAGE": "codepage",
                "SUBLEVELS_NMDC_LISTEN": "nmdc_listen", "SUBLEVELS_NMDCS_LISTEN": "nmdcs_listen",
                "SUBLEVELS_TLS_CERT": "tls_cert", "SUBLEVELS_TLS_KEY": "tls_key",
                "SUBLEVELS_NMDCS_ADDRESS": "nmdcs_address",
                "SUBLEVELS_IDENTITY_SALT": "identity_salt",
                "SUBLEVELS_NMDC_CAPTURE_DIR": "capture_dir", "SUBLEVELS_ROOM": "room",
                "SUBLEVELS_NMDC_TRANSPORT": "transport",
                "SUBLEVELS_PUBLIC_ADDRESS": "public_address",
                "SUBLEVELS_HUB_DESCRIPTION": "description", "SUBLEVELS_HUB_OWNER": "owner",
                "SUBLEVELS_DATA_DIR": "data_dir", "SUBLEVELS_GEOIP_DB": "geoip_db",
                "SUBLEVELS_LOCATION_POLICY": "location_policy",
                "SUBLEVELS_LOCATION_UNKNOWN": "location_unknown", "SUBLEVELS_CONNLOG": "connlog"}
        for var, name in text.items():
            if env.get(var):
                kw[name] = env[var]
        if env.get("SUBLEVELS_LIMITS"):
            # "name=value,name=value" over the Limits defaults (soaks and tests).
            from dataclasses import fields as _fields
            types = {f.name: f.type for f in _fields(Limits)}
            over = {}
            for part in env["SUBLEVELS_LIMITS"].split(","):
                name, _, value = part.partition("=")
                name = name.strip()
                if name not in types:
                    raise ValueError(f"SUBLEVELS_LIMITS: no limit {name!r}")
                over[name] = float(value) if types[name] in (float, "float") else int(value)
            kw["limits"] = Limits(**over)
        if env.get("SUBLEVELS_NET_THREADS"):
            kw["net_threads"] = int(env["SUBLEVELS_NET_THREADS"])
        for var, name in {"SUBLEVELS_MOTD_FILE": "motd", "SUBLEVELS_RULES_FILE": "rules"}.items():
            if env.get(var):  # the text until an operator edits it
                with open(env[var], encoding="utf-8") as f:
                    kw[name] = tuple(f.read().rstrip("\n").split("\n"))
        for var, name in {"SUBLEVELS_LOCATION_COUNTRIES": "location_countries",
                          "SUBLEVELS_LOCATION_EXEMPT": "location_exempt"}.items():
            if env.get(var):
                kw[name] = tuple(v.strip() for v in env[var].split(",") if v.strip())
        if env.get("SUBLEVELS_HUBLISTS"):
            v = env["SUBLEVELS_HUBLISTS"].strip()
            kw["hublists"] = () if v == "off" else tuple(t.strip() for t in v.split(",")
                                                        if t.strip())
        for var, name in {"SUBLEVELS_MIN_SHARE": "min_share", "SUBLEVELS_MIN_SLOTS": "min_slots",
                          "SUBLEVELS_MAX_HUBS": "max_hubs"}.items():
            if env.get(var):
                kw[name] = int(env[var])
        if env.get("SUBLEVELS_HUBLIST_INTERVAL"):
            kw["hublist_interval"] = float(env["SUBLEVELS_HUBLIST_INTERVAL"])
        if env.get("SUBLEVELS_MAX_USERS"):
            kw["max_users"] = int(env["SUBLEVELS_MAX_USERS"])
        for var, name in {"SUBLEVELS_STRICT_KEY": "strict_key",
                          "SUBLEVELS_TLS_REQUIRED": "tls_required",
                          "SUBLEVELS_COUNTRY_PREFIX": "country_prefix"}.items():
            if env.get(var):
                kw[name] = env[var].strip().lower() in ("1", "true", "yes", "on")
        if env.get("SUBLEVELS_RESERVED_NICKS"):
            kw["reserved_nicks"] = tuple(n for n in env["SUBLEVELS_RESERVED_NICKS"].split(",") if n)
        return cls(**kw)
