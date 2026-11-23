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


@dataclass(frozen=True)
class HubConfig:
    hub_id: str = "main"  # its node under sublevels.hubs
    room: str = "standup"  # the conference room this hub's users belong to (the relay's --room)
    name: str = "sublevels"
    topic: str = ""
    bot_nick: str = "sublevels"
    bot_description: str = "the hub"
    motd: tuple[str, ...] = DEFAULT_MOTD
    rules: tuple[str, ...] = DEFAULT_RULES
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
    # Plain NMDC's transport: "asyncio" (the hub's loop reads and writes every socket) or "engine"
    # (libcvc's native connection engine, ARIADNE_NET_ENGINE N2: its I/O threads do, and a fan-out
    # is one broadcast). NMDCS stays on asyncio until the engine has TLS (N3).
    transport: str = "asyncio"
    net_threads: int = 2  # the engine's I/O threads, when the hub makes its own (plain python)
    # Interop runs: write each connection's messages, both ways, to <capture_dir>/<id>-<protocol>
    # .log. It records what clients send in clear, passwords included: for test hubs only.
    capture_dir: str = ""
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
                "SUBLEVELS_NMDC_TRANSPORT": "transport"}
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
        if env.get("SUBLEVELS_MAX_USERS"):
            kw["max_users"] = int(env["SUBLEVELS_MAX_USERS"])
        for var, name in {"SUBLEVELS_STRICT_KEY": "strict_key",
                          "SUBLEVELS_TLS_REQUIRED": "tls_required"}.items():
            if env.get(var):
                kw[name] = env[var].strip().lower() in ("1", "true", "yes", "on")
        if env.get("SUBLEVELS_RESERVED_NICKS"):
            kw["reserved_nicks"] = tuple(n for n in env["SUBLEVELS_RESERVED_NICKS"].split(",") if n)
        return cls(**kw)
