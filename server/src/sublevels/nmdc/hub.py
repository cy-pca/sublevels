"""The NMDC hub: login (P2), chat (P3), search (P4) and connection signalling (P5).

The hub knows connections only through a small interface (``Conn``: ``write(bytes)`` and
``close()``), so it is tested without sockets; ``listener.py`` puts it on asyncio. Every message
handler runs on the one event loop, so the hub takes no locks.

Text: messages are decoded with the hub's code page on the way in (P1 §2). The decode is one to
one, so encoding the text again gives back the bytes the client sent: the hub forwards what it
must forward verbatim ($MyINFO, $SR, $To) by re-encoding the decoded text, and writes its own text
escaped (P1 §3).
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import logging
import os
import re
import secrets
import time
import zlib
from collections import Counter, deque
from typing import Any, Callable, Protocol

from .. import NAMESPACE, __version__
from . import myinfo as mi
from .codec import CodePage, escape, key_matches, make_lock
from .config import HubConfig
from .counters import Counters
from .analytics import Analytics
from .bans import Bans, until_text
from .documents import Documents
from .geo import UNKNOWN, GeoDB, LocationPolicy
from .limits import Bucket, Rate, Window
from .operators import OpError, Operators

log = logging.getLogger("sublevels.nmdc")

# Connection states (P2 §1).
LOCK, NICK, PASS, INFO, ONLINE, CLOSED = "lock", "nick", "pass", "info", "online", "closed"

HUB_SUPPORTS = "NoGetINFO NoHello UserIP2 BotList HubTopic HubINFO UserCommand"

# Fan-out groups: every online user; those that want $Hello (no NoHello); those that receive
# active searches (not ChatOnly) and passive ones (active or unknown mode, not ChatOnly); the
# operators, and those of them that receive $UserIP for others (UserIP2).
ALL, HELLO, SEARCH, SEARCH_ACTIVE, OPS, OPS_USERIP = (
    "all", "hello", "search", "search_active", "ops", "ops_userip")
GROUPS = (ALL, HELLO, SEARCH, SEARCH_ACTIVE, OPS, OPS_USERIP)
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_NICK_BANNED = set(" $|<>")
_TTH = re.compile(r"TTH:[A-Z2-7]{39}")
_PORT = re.compile(r"(\d{1,5})([A-Za-z]*)")
# What a pinger may not do (P9 §2): dropped.
_PINGER_DROPS = frozenset({"$To:", "$Search", "$SR", "$ConnectToMe", "$RevConnectToMe"})


class Conn(Protocol):
    def write(self, data: bytes) -> None: ...

    def close(self) -> None: ...


class Accounts(Protocol):
    """Registered nicks: ``role(nick)`` (``user``, ``op`` or ``admin``) is optional; without it,
    ``is_operator`` gives ``op``."""

    """Registered nicks (P2 §4). M1 has none; TX.WTF accounts come in M2."""

    def is_registered(self, nick: str) -> bool: ...

    def check(self, nick: str, password: str) -> bool: ...

    def is_operator(self, nick: str) -> bool: ...


class NoAccounts:
    def is_registered(self, nick: str) -> bool:
        return False

    def check(self, nick: str, password: str) -> bool:
        return False

    def is_operator(self, nick: str) -> bool:
        return False


def nick_key(nick: str) -> str:
    """Nicks are unique case-insensitively, as decoded text (P1 §2, P2 §3)."""
    return nick.casefold()


def _plain_ip(ip: str) -> str:
    """An IPv4-mapped IPv6 address as IPv4; anything else as given."""
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    if isinstance(a, ipaddress.IPv6Address) and a.ipv4_mapped is not None:
        return str(a.ipv4_mapped)
    return str(a)


def _is_ipv6(ip: str) -> bool:
    return ":" in ip


def _address(ip: str, port: str) -> str:
    """``ip:port`` as NMDC writes it; an IPv6 address in brackets (P5 §4)."""
    return f"[{ip}]:{port}" if _is_ipv6(ip) else f"{ip}:{port}"


class Session:
    """One connection, from accept to close."""

    __slots__ = ("id", "conn", "ip", "port", "protocol", "tls", "state", "nick", "key", "supports",
                 "lock", "info", "info_text", "info_sent", "info_bytes", "info_due", "info_timer", "is_op",
                 "identity", "connected_at", "online_at", "want_nicklist", "bad_lines",
                 "malformed", "received", "chat_rate", "pm_rate", "search_rate", "search_notice",
                 "last_search", "sr_sent", "ctm_rate", "ctm_remote", "bytes_in", "bytes_out",
                 "close_reason", "login_timer", "fanout", "groups", "pinger", "pinger_timer",
                 "visit", "location", "account", "opened_wall", "role", "last_pm",
                 "location_refusal")

    def __init__(self, sid: int, conn: Conn, ip: str, port: int, protocol: str, tls: bool,
                 now: float, limits):
        self.id = sid
        self.conn = conn
        self.ip = _plain_ip(ip)
        self.port = port
        self.protocol = protocol  # "nmdc" or "nmdcs"
        self.tls = tls
        self.state = LOCK
        self.nick = ""
        self.key = ""
        self.supports: frozenset[str] = frozenset()
        self.lock = b""
        self.info: mi.MyInfo | None = None
        self.info_text = ""  # the latest $MyINFO as decoded, with its '|'
        self.info_sent = ""  # the one others last got
        self.info_bytes = b""  # info_sent, encoded once for every user list and broadcast
        self.info_due = 0.0  # when the next update may go out (P2 §5)
        self.info_timer = None
        self.is_op = False
        self.identity = ""
        self.connected_at = now
        self.online_at = 0.0
        self.want_nicklist = False
        self.bad_lines = 0
        self.malformed = Window(limits.malformed_per_minute, 60.0)
        self.received: list[tuple[float, int]] = []  # (time, bytes) read while online
        self.chat_rate = Rate(limits.chat_lines, limits.chat_window, limits.chat_burst, now)
        self.pm_rate = Rate(limits.pm_count, limits.pm_window, None, now)
        self.search_rate = Bucket(limits.search_count, limits.search_window, limits.search_burst,
                                  now)
        self.search_notice = -1e9
        self.last_search = -1e9
        self.sr_sent: dict[str, int] = {}  # target key -> results sent since its last search
        self.ctm_rate = Window(limits.ctm_count, limits.ctm_window)
        self.ctm_remote: dict[str, Window] = {}
        self.bytes_in = 0
        self.bytes_out = 0
        self.close_reason = ""
        self.login_timer = None
        # The connection's fan-out when the hub's loop does not write it itself (the native
        # engine's, which keeps groups of its own), or None; and the fan-out groups it is in.
        self.fanout = getattr(conn, "fanout", None)
        self.groups: set[str] = set()
        self.location = UNKNOWN  # where the address is (D11), looked up before $Lock
        # The location policy refused it, but an exempt account may still log in (D12): the line
        # it is sent if it does not. "" when the policy admitted it.
        self.location_refusal = ""
        self.account = ""  # the registered nick it logged in with, if any
        self.role = "user"  # P6 §1: from its account
        self.last_pm: dict[str, tuple[float, str]] = {}  # target key -> (time, text): $Kick's reason
        self.opened_wall = time.time()
        self.pinger = False  # a hub list's pinger (P9 §2): BotINFO in $Supports, or $BotINFO
        self.pinger_timer = None
        self.visit: dict | None = None  # its entry in Hub.visits

    @property
    def mode(self) -> str:
        return self.info.mode if self.info else mi.UNKNOWN

    @property
    def passive(self) -> bool:
        return self.mode == mi.PASSIVE

    @property
    def online(self) -> bool:
        return self.state == ONLINE


class Hub:
    """One NMDC hub. ``tree`` is where it publishes ``sublevels.hubs.<hub_id>`` (a
    ``pycvc.ariadne.Host``, or None)."""

    def __init__(self, config: HubConfig | None = None, *, tree: Any = None,
                 accounts: Accounts | None = None, clock: Callable[[], float] = time.monotonic,
                 call_later: Callable[[float, Callable[[], None]], Any] | None = None):
        self.config = config or HubConfig()
        self.limits = self.config.limits
        self.tree = tree
        self.accounts = accounts or NoAccounts()
        self.clock = clock
        self._call_later = call_later
        self.codepage = CodePage(self.config.codepage)
        self.counters = Counters()
        self.salt = self.config.identity_salt or self._stored_salt()
        self.sessions: dict[int, Session] = {}  # every connection
        self.claimed: dict[str, Session] = {}  # nick key -> the session past $ValidateNick
        self.users: dict[str, Session] = {}  # nick key -> online session
        self._ids = 0
        self._publish_timer = None
        self._bad_pass: dict[str, Window] = {}  # identity -> failures
        self.keyprint = ""  # the NMDCS certificate's (P7 §1), once the listener has loaded it
        self.bridge = None  # the room bridge (sublevels.bridge.RoomBridge), when the host has one
        self.geo = GeoDB(self.config.geoip_db, clock=clock)
        self.policy = LocationPolicy.from_config(self.config)
        self.connlog = None  # the connection log (connlog.ConnLog), when the server keeps one
        self.documents = Documents({"motd": self.config.motd, "rules": self.config.rules},
                                   self.config.data_dir)
        self.topic = self.documents.topic(self.config.topic)  # an admin's !topic outlives a restart
        self.bans = Bans(self.config.data_dir)
        self.ops = Operators(self)
        self.hublist = None  # this instance's hub list (hublist mode, D16), for +hublist
        self.analytics = Analytics(self)  # D14; sampling starts with Analytics.start()
        self.actions: deque[dict] = deque(maxlen=1000)  # the operator log, newest last (P6 §7)
        self._ban_hits: dict[str, Window] = {}  # address -> refused-by-ban attempts
        if hasattr(self.accounts, "on_change"):
            self.accounts.on_change = self.refresh_roles
        # Fan-out groups (see _regroup): every member, and the members on the hub's own loop,
        # which it writes to itself. Members on an engine get one broadcast() for the group.
        self.members: dict[str, set[Session]] = {g: set() for g in GROUPS}
        self.loop_members: dict[str, set[Session]] = {g: set() for g in GROUPS}
        self.engines: list = []  # the transports with groups of their own (the native engine)
        # Hub-list pingers online (P9 §2): not users, so in no count, list or group; and their
        # recent visits, newest last, for the admin API.
        self.pingers: dict[int, Session] = {}
        self.visits: deque[dict] = deque(maxlen=200)
        bot = self.config.bot_nick
        self.bot_key = nick_key(bot)
        self.bot_info = self._enc(f"$MyINFO $ALL {bot} {escape(self.config.bot_description)}$ $"
                                  f"Hub\x01$$0$|")

    # -- plumbing ----------------------------------------------------------------------------------

    def call_later(self, delay: float, fn: Callable[[], None]):
        if self._call_later is not None:
            return self._call_later(delay, fn)
        return asyncio.get_running_loop().call_later(delay, fn)

    def _enc(self, text: str) -> bytes:
        return self.codepage.encode(text)

    def _write(self, s: Session, data: bytes) -> None:
        if s.state == CLOSED:
            return
        s.bytes_out += len(data)
        self.counters.add("bytes_out", len(data))
        s.conn.write(data)

    def send(self, s: Session, text: str) -> None:
        self._write(s, self._enc(text))

    # -- fan-out groups ----------------------------------------------------------------------------

    def _want_groups(self, s: Session) -> set[str]:
        """The groups an online user belongs in, from its features and mode. A pinger is in none."""
        if s.pinger:
            return set()
        g = {ALL}
        if "NoHello" not in s.supports:
            g.add(HELLO)
        if "ChatOnly" not in s.supports:
            g.add(SEARCH)  # active searches (P4 §1)
            if s.mode in (mi.ACTIVE, mi.UNKNOWN):
                g.add(SEARCH_ACTIVE)  # passive searches go only to them
        if s.is_op:
            g.add(OPS)
            if "UserIP2" in s.supports:
                g.add(OPS_USERIP)
        return g

    def _regroup(self, s: Session) -> None:
        """Bring ``s``'s group memberships to what it should have now (none once it is offline)."""
        want = self._want_groups(s) if s.state == ONLINE else set()
        for g in s.groups - want:
            self.members[g].discard(s)
            self.loop_members[g].discard(s)
            if s.fanout is not None:
                s.fanout.leave(s, g)
        for g in want - s.groups:
            self.members[g].add(s)
            if s.fanout is None:
                self.loop_members[g].add(s)
            else:
                s.fanout.join(s, g)
        s.groups = want

    def group_send(self, group: str, data: bytes, exclude: Session | None = None) -> int:
        """Send the same bytes to every member of ``group`` but ``exclude``: one broadcast() to
        each engine for its members, and a write to each of the loop's own. Returns how many."""
        members = self.members[group]
        n = len(members) - (1 if exclude is not None and exclude in members else 0)
        if n <= 0:
            return 0
        for t in self.engines:
            t.broadcast(group, data, exclude if exclude is not None and exclude.fanout is t
                        else None)
        for u in self.loop_members[group]:
            if u is not exclude and u.state != CLOSED:
                u.conn.write(data)
        self.counters.add("bytes_out", len(data) * n)
        return n

    def broadcast(self, text: str, exclude: Session | None = None) -> int:
        return self.group_send(ALL, self._enc(text), exclude)

    def bot_line(self, text: str) -> str:
        return f"<{self.config.bot_nick}> {escape(text)}|"

    def bot_say(self, s: Session, text: str) -> None:
        """A main-chat line from the bot that only ``s`` receives (P3 §4)."""
        self.send(s, self.bot_line(text))

    def bot_pm(self, s: Session, text: str) -> None:
        bot = self.config.bot_nick
        self.send(s, f"$To: {s.nick} From: {bot} $<{bot}> {escape(text)}|")

    def _stored_salt(self) -> str:
        """The server's salt for client ids: kept in the data directory, so ids (and the
        connection log's records of them) stay the same across restarts; random per run
        without one."""
        if not self.config.data_dir:
            return secrets.token_hex(16)
        path = os.path.join(self.config.data_dir, "identity_salt")
        try:
            with open(path, encoding="ascii") as f:
                salt = f.read().strip()
            if salt:
                return salt
        except OSError:
            pass
        salt = secrets.token_hex(16)
        os.makedirs(self.config.data_dir, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="ascii") as f:
            f.write(salt + "\n")
        return salt

    def identity_of(self, ip: str) -> str:
        """[D-9]: an NMDC connection's client id, until the registry gives engine connections
        theirs: the shape of the registry's address fallback, a salted hash of the address with
        the salt per server. Every caller goes through here, so it can switch in one place."""
        return hashlib.sha256(f"{self.salt}|{ip}".encode()).hexdigest()[:16]

    def set_keyprint(self, keyprint: str) -> None:
        """The NMDCS listener loaded a certificate: clients pin this keyprint with ``?kp=``."""
        self.keyprint = keyprint
        self._dirty()

    def nmdcs_url(self) -> str:
        """``nmdcs_address`` with the keyprint (``…/?kp=SHA256/…``), so a client that follows it
        pins the certificate; "" when the hub has no NMDCS address."""
        url = self.config.nmdcs_address
        if not url:
            return ""
        if self.keyprint and "kp=" not in url:
            url = url.rstrip("/") + "/?kp=" + self.keyprint
        return url

    # -- connections -------------------------------------------------------------------------------

    def listed_address(self) -> str:
        """``public_address`` as ``host:port`` (port 411 when it names none) for $HubINFO and the
        lists; "" when there is none, or it is a loopback address (P9 §2)."""
        addr = self.config.public_address.strip()
        if not addr:
            return ""
        host, sep, port = addr.rpartition(":")
        if not sep or not port.isdigit() or host.endswith(":"):  # no port, or a bare IPv6
            host, port = addr, "411"
        host = host.strip("[]")
        try:
            if ipaddress.ip_address(host).is_loopback:
                return ""
        except ValueError:
            if host.lower() == "localhost":
                return ""
        return f"[{host}]:{port}" if _is_ipv6(host) else f"{host}:{port}"

    def listed_description(self) -> str:
        return self.config.description or self.topic

    def connect(self, conn: Conn, ip: str, port: int, protocol: str = "nmdc",
                tls: bool = False) -> Session:
        now = self.clock()
        self._ids += 1
        s = Session(self._ids, conn, ip, port, protocol, tls, now, self.limits)
        s.identity = self.identity_of(s.ip)
        self.sessions[s.id] = s
        self.counters.add(f"connections.{protocol}")
        s.location = self.geo.lookup(s.ip)
        # Capacity and the location policy [D-12] before $Lock (P2 §2 step 1). Bans [D-9] are
        # P6's.
        if len(self.sessions) > self.config.max_users:
            self.bot_say(s, "This hub is full. Try again later.")
            self.send(s, "$HubIsFull|")
            self.counters.refuse("full")
            self.close(s, "full")
            return s
        why = self.policy.refuses(s.ip, s.location)
        if why:
            if isinstance(self.accounts, NoAccounts):
                self._refuse_location(s, why)  # nobody could be exempt
                return s
            # Held (D12): only an exempt account, with its password, gets past $ValidateNick.
            s.location_refusal = why
            self.counters.add("location_held")
        ban = self.bans.at_connect(s.ip, s.identity)  # P6 §4 step 1
        if ban is not None:
            self._refuse_ban(s, ban)
            return s
        if self.config.tls_required and not tls:  # P7 §1
            where = self.nmdcs_url()
            self.bot_say(s, f"This hub takes TLS connections only: connect to {where}." if where
                         else "This hub takes TLS connections only (nmdcs://).")
            if where:
                self.send(s, f"$ForceMove {where}|")
            self.counters.refuse("tls_required")
            self.close(s, "plain connection to a TLS-only hub")
            return s
        s.lock = make_lock()
        self._write(s, b"$Lock " + s.lock + f" Pk=sublevels{__version__}|".encode())
        self.send(s, f"$HubName {escape(self.config.name)}|")
        s.login_timer = self.call_later(self.limits.login_timeout, lambda: self._login_timeout(s))
        return s

    def _login_timeout(self, s: Session) -> None:
        if s.state not in (ONLINE, CLOSED):
            self.counters.refuse("login_timeout")
            self.close(s, "login timeout")

    def close(self, s: Session, reason: str) -> None:
        """Close ``s`` from the hub's side: nothing more is sent, and it leaves the hub."""
        if s.state == CLOSED:
            return
        s.close_reason = reason
        self._leave(s)
        s.conn.close()

    def disconnect(self, s: Session, reason: str = "closed by the client") -> None:
        """The connection is gone (the listener calls this from connection_lost)."""
        if s.state != CLOSED:
            s.close_reason = reason
            self._leave(s)
        self.sessions.pop(s.id, None)

    def _leave(self, s: Session) -> None:
        was_online = s.state == ONLINE
        s.state = CLOSED
        for t in (s.login_timer, s.info_timer, s.pinger_timer):
            if t is not None:
                t.cancel()
        self.pingers.pop(s.id, None)
        if self.connlog is not None:
            self.connlog.record(self.log_row(s))
        self.counters.close(s.close_reason)
        if s.nick and self.claimed.get(s.key) is s:
            del self.claimed[s.key]
        if s.groups:
            self._regroup(s)  # state is CLOSED: out of every group, before the $Quit
        if was_online and self.users.get(s.key) is s:
            del self.users[s.key]
            self.broadcast(f"$Quit {s.nick}|")
            if self.bridge is not None:
                self.bridge.offline(s)
            if s.is_op:
                self._send_oplist_all()
            self._dirty()
        if s.close_reason not in ("closed by the client", ""):
            log.info("closed %s (%s, %s): %s", s.nick or "-", s.identity, s.protocol,
                     s.close_reason)

    def max_message(self, s: Session) -> int:
        return self.limits.online_message_max if s.online else self.limits.login_message_max

    def received(self, s: Session, n: int) -> bool:
        """Count ``n`` bytes read from ``s``; False (and ``s`` closed) past the online limit."""
        s.bytes_in += n
        self.counters.add("bytes_in", n)
        if s.online:
            now = self.clock()
            cut = now - self.limits.received_window
            if s.received and s.received[0][0] <= cut:
                s.received = [e for e in s.received if e[0] > cut]
            s.received.append((now, n))
            if sum(b for _, b in s.received) > self.limits.received_bytes:
                self.counters.refuse("flood")
                self.close(s, "received too much")
                return False
        return True

    def too_long(self, s: Session) -> None:
        self.counters.drop("too_long")
        self.close(s, "message too long")

    # -- messages ----------------------------------------------------------------------------------

    def message(self, s: Session, raw: bytes) -> None:
        """One message from ``s``, with its ``|``."""
        if s.state == CLOSED:
            return
        if raw == b"|":
            return  # a keepalive (P1 §1)
        text = self.codepage.decode(raw)
        first = text[0]
        if first == "$":
            sp = text.find(" ")
            name = text[:sp] if sp >= 0 else text[:-1]
            handler = self._handlers.get(name)
            if s.pinger and name in _PINGER_DROPS:
                self.counters.drop("pinger")
                return
            if handler is not None and s.state in handler[0]:
                handler[1](self, s, text)
            return  # unknown, or not valid in this state: ignored (P1 §1, P2 §1)
        if first == "<":
            if s.pinger:
                self.counters.drop("pinger")
            elif s.online:
                self._chat(s, text)
            return
        self._malformed(s, "malformed")

    def _malformed(self, s: Session, reason: str) -> None:
        self.counters.drop(reason)
        if not s.malformed.allow(self.clock()):
            self.close(s, "too many malformed messages")

    # -- login (P2) --------------------------------------------------------------------------------

    def _supports(self, s: Session, text: str) -> None:
        s.supports = frozenset(f for f in text[len("$Supports "):-1].split(" ") if f)
        if "BotINFO" in s.supports:
            s.pinger = True  # P9 §2

    def _key(self, s: Session, text: str) -> None:
        received = self._enc(text[len("$Key "):-1])
        if not key_matches(s.lock, received):
            self.counters.add("key_mismatch")
            if self.config.strict_key:
                self.close(s, "wrong $Key")
                return
        if s.state == LOCK:
            s.state = NICK

    def nick_problem(self, nick: str) -> str:
        """Why ``nick`` cannot be used now ("" when it can)."""
        if not 1 <= len(nick) <= 64:
            return "length"
        if any(c in _NICK_BANNED for c in nick) or _CONTROL.search(nick):
            return "characters"
        if nick.startswith(".") or nick.endswith("."):
            return "dots"
        k = nick_key(nick)
        if k == self.bot_key or k in {nick_key(n) for n in self.config.reserved_nicks}:
            return "reserved"
        if self.bridge is not None and self.bridge.reserves(k):
            return "reserved"  # room users' names (P3 §5)
        if k in self.claimed:
            return "taken"
        return ""

    def _validate_nick(self, s: Session, text: str) -> None:
        if s.nick:
            return  # one nick per connection
        nick = text[len("$ValidateNick "):-1]
        why = self.nick_problem(nick)
        if why == "taken" and self.accounts.is_registered(nick):
            why = ""  # the right password takes the nick over (§3); checked at $MyPass
        if why:
            self.send(s, f"$ValidateDenide {nick}|")
            self.counters.refuse(f"nick_{why}")
            self.close(s, f"nick refused ({why})")
            return
        registered = self.accounts.is_registered(nick)
        if s.location_refusal and not self.policy.exempts(self.accounts, nick):
            s.nick = nick
            self._refuse_location(s, s.location_refusal)
            return
        ban = self.bans.at_nick(nick, registered)  # P6 §4 step 2
        if ban is not None:
            s.nick = nick
            self._refuse_ban(s, ban)
            return
        s.nick, s.key = nick, nick_key(nick)
        self.send(s, f"$Supports {HUB_SUPPORTS}{' ZPipe0' if self.config.zpipe != 'off' else ''}|")
        if registered:
            if self.config.password_requires_tls and not s.tls:
                where = f" at {self.nmdcs_url()}" if self.config.nmdcs_address else ""
                self.bot_say(s, f"{nick} is registered. Log in over TLS (nmdcs://){where}: "
                                "this hub does not take passwords in the clear.")
                self.counters.refuse("password_needs_tls")
                self.close(s, "password over a plain connection")
                return
            fails = self._bad_pass.get(s.identity)
            if fails is not None and not fails.room(self.clock()):
                self.bot_say(s, "Too many wrong passwords. Try again later.")
                self.counters.refuse("bad_password_rate")
                self.close(s, "password retries exhausted")
                return
            s.state = PASS
            self.send(s, "$GetPass|")
            return
        self.claimed[s.key] = s
        self._hello(s)

    def _my_pass(self, s: Session, text: str) -> None:
        password = text[len("$MyPass "):-1]
        if not self.accounts.check(s.nick, password):
            w = self._bad_pass.setdefault(s.identity, Window(self.limits.bad_password_count,
                                                             self.limits.bad_password_window))
            w.hit(self.clock())
            self.send(s, "$BadPass|")
            self.counters.refuse("bad_password")
            self.close(s, "wrong password")
            return
        old = self.claimed.get(s.key)
        if old is not None and old is not s:
            self.close(old, "logged in again elsewhere")  # the user reconnecting (P2 §3)
        ban = self.bans.at_account(s.nick)  # P6 §4 step 3
        if ban is not None:
            self._refuse_ban(s, ban)
            return
        self.claimed[s.key] = s
        s.account = s.nick
        self._set_role(s)
        if s.location_refusal:  # an exempt account, logged in with its password (D12)
            self.counters.add("location_exempted")
            self.oplog("location_exempt", "hub", s.nick, s.location_refusal, address=s.ip,
                       country=s.location.country, role=s.role)
            s.location_refusal = ""
        self._hello(s)

    def _hello(self, s: Session) -> None:
        self.send(s, f"$Hello {s.nick}|")
        s.state = INFO

    def _version(self, s: Session, text: str) -> None:
        pass  # accepted and ignored (P2 §2 step 7)

    def _get_nick_list(self, s: Session, text: str) -> None:
        if s.online:
            self._send_user_list(s)
        else:
            s.want_nicklist = True

    def _my_info(self, s: Session, text: str) -> None:
        info = mi.parse(text[:-1], _is_ipv6(s.ip))
        if info is None:
            self._malformed(s, "bad_myinfo")
            return
        if info.nick != s.nick:
            if s.online:
                self.counters.drop("myinfo_nick")
            else:
                self.counters.refuse("myinfo_nick")
                self.close(s, "$MyINFO for another nick")
            return
        s.info = info
        s.info_text = text
        if not s.online:
            self._go_online(s)
            return
        if s.pinger:
            return  # never announced (P9 §2)
        if text == s.info_sent:
            return  # byte-identical: not sent again (P2 §5)
        now = self.clock()
        if now >= s.info_due and s.info_timer is None:
            self._send_info(s)
        elif s.info_timer is None:
            s.info_timer = self.call_later(s.info_due - now, lambda: self._info_timer(s))
        self._dirty()

    def _info_timer(self, s: Session) -> None:
        s.info_timer = None
        if s.online and s.info_text != s.info_sent:
            self._send_info(s)

    def _info_out(self, s: Session) -> str:
        """The $MyINFO others get: the user's, with "[CC] " before the description when the hub
        shows countries (D13, a rewrite P2 §5 allows)."""
        text = s.info_text
        if self.config.country_prefix and s.location.known:
            head = f"$MyINFO $ALL {s.nick} "
            if text.startswith(head):
                text = f"{head}[{s.location.country}] {text[len(head):]}"
        return text

    def _send_info(self, s: Session) -> None:
        s.info_sent = s.info_text
        s.info_bytes = self._enc(self._info_out(s))
        s.info_due = self.clock() + self.limits.myinfo_interval
        self.counters.add("myinfo_updates")
        self._regroup(s)  # the mode may have changed (P2 §5)
        self.group_send(ALL, s.info_bytes)
        if self.bridge is not None:
            self.bridge.updated(s)

    def _go_online(self, s: Session) -> None:
        if s.location_refusal:  # never reached (only $MyPass clears it), but never let one in
            self._refuse_location(s, s.location_refusal)
            return
        if s.pinger:
            self._pinger_online(s)
            return
        now = self.clock()
        s.state = ONLINE
        s.online_at = now
        s.info_sent = s.info_text
        s.info_bytes = self._enc(self._info_out(s))
        s.info_due = now + self.limits.myinfo_interval
        if s.login_timer is not None:
            s.login_timer.cancel()
            s.login_timer = None
        self.users[s.key] = s
        self._regroup(s)
        self.counters.add("logins")
        if s.is_op:
            self.send(s, f"$LogedIn {s.nick}|")
        self._send_user_list(s)
        # Everyone else (P2 §6).
        self.group_send(HELLO, self._enc(f"$Hello {s.nick}|"), s)
        self.group_send(ALL, s.info_bytes, s)
        if s.is_op:
            self._send_oplist_all()
        self.group_send(OPS_USERIP, self._enc(f"$UserIP {s.nick} {s.ip}|"), s)
        # Hub text (P2 §7).
        if self.topic:
            self.send(s, self._topic_line(s))
        for line in self.documents.lines("motd"):
            self.bot_say(s, line)
        self.bot_say(s, "Type +rules to read the rules, +help for the hub's commands.")
        if "UserCommand" in s.supports:  # P6 §5
            for line in self.ops.menus(s.role):
                self.send(s, line)
        if self.bridge is not None:
            self.bridge.online(s)
        self._dirty()

    # -- hub-list pingers (P9 §2) ------------------------------------------------------------------

    def _pinger_online(self, s: Session) -> None:
        """A pinger's login: it gets the user list and the hub's text, as a user does, but is not
        announced, counted or put in any group, and is closed after ``pinger_time``."""
        s.state = ONLINE
        s.online_at = self.clock()
        if s.login_timer is not None:
            s.login_timer.cancel()
            s.login_timer = None
        self._pinger_start(s)
        self._send_user_list(s)
        if self.topic and "HubTopic" in s.supports:
            self.send(s, f"$HubTopic {escape(self.topic)}|")

    def _pinger_start(self, s: Session) -> None:
        self.pingers[s.id] = s
        self.counters.add("pinger_visits")
        said = s.visit["botinfo"] if s.visit else ""  # a $BotINFO before its $MyINFO
        s.visit = {"at": round(time.time(), 3), "address": s.ip, "nick": s.nick,
                   "protocol": s.protocol, "botinfo": said}
        self.visits.append(s.visit)
        s.pinger_timer = self.call_later(self.config.pinger_time,
                                         lambda: self.close(s, "pinger time limit"))
        log.info("hub-list pinger %s from %s", s.nick, s.ip)

    def _bot_info(self, s: Session, text: str) -> None:
        """``$BotINFO <description>|``: a hub list's pinger, answered with ``$HubINFO``."""
        if not s.pinger:
            s.pinger = True
            if s.online:  # announced as a user before it said: take it back out
                self._unannounce(s)
                self._pinger_start(s)
        said = text[len("$BotINFO "):-1]
        if s.visit is not None:
            s.visit["botinfo"] = said
        else:
            s.visit = {"botinfo": said}  # kept for _pinger_start
        self.send(s, self.hub_info())

    def _unannounce(self, s: Session) -> None:
        if self.users.get(s.key) is s:
            del self.users[s.key]
            self._regroup(s)  # a pinger: out of every group
            self.counters.add("logins", -1)
            self.broadcast(f"$Quit {s.nick}|")
            if self.bridge is not None:
                self.bridge.offline(s)
            if s.is_op:
                self._send_oplist_all()
            self._dirty()

    def hub_info(self) -> str:
        """``$HubINFO`` (P9 §2): name, address, description, max users, min share, min slots, max
        hubs, hub type, owner."""
        c = self.config
        fields = (c.name, self.listed_address(), self.listed_description(), str(c.max_users),
                  str(c.min_share), str(c.min_slots), str(c.max_hubs), f"sublevels {__version__}",
                  c.owner)
        return "$HubINFO " + "$".join(escape(f) for f in fields) + "|"

    def log_row(self, s: Session) -> dict:
        """``s`` as the connection log keeps it (D13)."""
        i = s.info
        return {"hub": self.config.hub_id, "opened_at": round(s.opened_wall, 3),
                "closed_at": round(time.time(), 3) if s.state == CLOSED else None,
                "nick": s.nick, "account": s.account, "client_id": s.identity, "address": s.ip,
                "port": s.port, "country": s.location.country, "region": s.location.region,
                "city": s.location.city, "client": i.client if i else "",
                "version": mi.parse_tag(i.tag)[1].get("V", "") if i and i.tag else "",
                "protocol": s.protocol, "tls": s.tls,
                "bytes_in": s.bytes_in, "bytes_out": s.bytes_out, "pinger": s.pinger,
                "close_reason": s.close_reason}

    def connections(self, **filters) -> dict:
        """The connection log for operators: the live connections that match, and the stored
        rows (newest first)."""
        live = [self.log_row(s) for s in self.sessions.values() if s.state != CLOSED]
        for k, v in filters.items():
            if v and k != "limit":
                live = [r for r in live if (str(r.get(k, "")).lower() == str(v).lower())]
        stored = self.connlog.query(**filters) if self.connlog is not None else []
        return {"live": live, "stored": stored, "kept": self.connlog is not None,
                "attribution": self.geo.status()["attribution"]}

    def set_document(self, name: str, text: str, by: str = "") -> dict:
        """An operator's edit of the message of the day or the rules: stored, and published."""
        doc = self.documents.set(name, text, by)
        self._dirty()
        log.info("document %s edited%s", name, f" by {by}" if by else "")
        return doc

    def _oplist(self) -> str:
        ops = [self.config.bot_nick] + [u.nick for u in self.users.values() if u.is_op]
        return "$OpList " + "".join(f"{n}$$" for n in ops) + "|"

    def _send_oplist_all(self) -> None:
        self.broadcast(self._oplist())

    def _send_user_list(self, s: Session) -> None:
        """The user list for ``s`` (P2 §6), written as one run of bytes."""
        parts: list[bytes] = []
        if "NoHello" in s.supports or "NoGetINFO" in s.supports:
            if "NoHello" not in s.supports:
                parts.append(self._nicklist())
            parts.append(self.bot_info)
            parts.extend(u.info_bytes for u in self.users.values())
        else:
            parts.append(self._nicklist())
        parts.append(self._enc(self._oplist()))
        if "BotList" in s.supports:
            parts.append(self._enc(f"$BotList {self.config.bot_nick}$$|"))
        if "UserIP2" in s.supports:
            if s.is_op:
                ips = "$$".join(f"{u.nick} {u.ip}" for u in self.users.values())
                parts.append(self._enc(f"$UserIP {ips}|"))
            else:
                parts.append(self._enc(f"$UserIP {s.nick} {s.ip}|"))
        self._write(s, self._maybe_zpipe(s, b"".join(parts)))

    def _maybe_zpipe(self, s: Session, data: bytes) -> bytes:
        """``data`` as one ZPipe0 block for a client that listed ZPipe0 (P7 §3), when the hub is
        set to compress and ``data`` is large enough; else as it is."""
        mode = self.config.zpipe
        if mode == "off" or "ZPipe0" not in s.supports or len(data) < self.config.zpipe_min:
            return data
        z = zlib.compress(data, 6)  # one complete stream: its end returns to plain messages
        self.counters.add("zpipe_blocks")
        self.counters.add("zpipe_saved", len(data) - len(z))
        if mode == "zon-blob":  # the probe's other framing: no client takes it
            return b"$ZOn " + z + b"|"
        return b"$ZOn|" + z  # "on" (or "zon")

    def _nicklist(self) -> bytes:
        nicks = [self.config.bot_nick] + [u.nick for u in self.users.values()]
        return self._enc("$NickList " + "".join(f"{n}$$" for n in nicks) + "|")

    def _get_info(self, s: Session, text: str) -> None:
        other = text[len("$GetINFO "):-1].split(" ")[0]
        k = nick_key(other)
        if k == self.bot_key:
            self._write(s, self.bot_info)
            return
        u = self.users.get(k)
        if u is not None:
            self._write(s, u.info_bytes)

    # -- chat (P3) ---------------------------------------------------------------------------------

    def _sender_ok(self, s: Session, nick: str) -> bool:
        if nick == s.nick:
            return True
        s.bad_lines += 1
        self.counters.drop("wrong_sender")
        if s.bad_lines >= self.limits.bad_sender_lines:
            self.close(s, "messages under another nick")
        return False

    def _chat(self, s: Session, text: str) -> None:
        end = text.find("> ")
        if end < 0:
            end = text.find(">")
            if end < 0:
                self._malformed(s, "bad_chat")
                return
        if not self._sender_ok(s, text[1:end]):
            return
        body = text[end + 2:-1] if text[end + 1:end + 2] == " " else text[end + 1:-1]
        if body.startswith("+") or (body.startswith("!") and s.is_op):
            self.counters.add("commands")
            self._command(s, body, private=False)
            return
        if len(self._enc(body)) > self.limits.chat_text_max:
            self.counters.drop("chat_too_long")
            self.bot_say(s, f"Message not sent: longer than {self.limits.chat_text_max} bytes.")
            return
        if not s.chat_rate.allow(self.clock()):
            self.counters.drop("chat_rate")
            self.bot_say(s, "Message not sent: you are sending too fast.")
            return
        self.counters.add("chat")
        n = self.group_send(ALL, self._enc(text))  # the sender too (P3 §1)
        self.counters.add("chat_fanout", n)
        if self.bridge is not None:
            self.bridge.chat(s, body)

    def _to(self, s: Session, text: str) -> None:
        # $To: <target> From: <sender> $<<sender>> text|
        m = re.match(r"\$To: (\S+) From: (\S+) \$<([^>]*)> ?", text)
        if m is None:
            self._malformed(s, "bad_to")
            return
        target, sender, inner = m.group(1), m.group(2), m.group(3)
        if not self._sender_ok(s, sender) or not self._sender_ok(s, inner):
            return
        body = text[m.end():-1]
        k = nick_key(target)
        if k == self.bot_key:
            self.counters.add("commands")
            self._command(s, body, private=True)
            return
        if len(self._enc(body)) > self.limits.pm_text_max:
            self.counters.drop("pm_too_long")
            self.bot_pm(s, f"Message to {target} not sent: longer than "
                           f"{self.limits.pm_text_max} bytes.")
            return
        u = self.users.get(k)
        if u is None and self.bridge is not None and self.bridge.pm(s, target, body):
            return
        if u is None:
            self.counters.drop("pm_offline")
            self.bot_pm(s, f"{target} is not online.")
            return
        if not s.pm_rate.allow(self.clock()):
            self.counters.drop("pm_rate")
            self.bot_pm(s, "Message not sent: you are sending too fast.")
            return
        self.counters.add("pm")
        if s.is_op:  # the reason a $Kick that follows it gives (P6 §2)
            s.last_pm[u.key] = (self.clock(), body)
            if len(s.last_pm) > 64:
                s.last_pm.pop(next(iter(s.last_pm)))
        self.send(u, text)

    def _command(self, s: Session, body: str, private: bool) -> None:
        self.ops.command(s, body, private)  # P6 §3

    # -- operators (P6) ----------------------------------------------------------------------------

    def _set_role(self, s: Session) -> None:
        role_of = getattr(self.accounts, "role", None)
        if role_of is not None:
            s.role = role_of(s.nick)
        else:
            s.role = "op" if self.accounts.is_operator(s.nick) else "user"
        s.is_op = s.role in ("op", "admin")

    def refresh_roles(self) -> None:
        """The accounts changed: online users get their new roles, and, if one changed, the
        operator list and their menus again (P6 §5)."""
        changed = False
        for u in list(self.users.values()):
            if not u.account:
                continue
            old = u.role
            if not self.accounts.is_registered(u.nick):
                u.role, u.is_op = "user", False
            else:
                self._set_role(u)
            if u.role != old:
                changed = True
                self._regroup(u)
                if u.is_op and old == "user":
                    self.send(u, f"$LogedIn {u.nick}|")
                if "UserCommand" in u.supports:
                    for line in self.ops.menus(u.role):
                        self.send(u, line)
                self.oplog("role", "accounts", u.nick, f"{old} -> {u.role}")
        if changed:
            self._send_oplist_all()

    def check_ready(self) -> None:
        """Raise ValueError if the hub would not do what its settings say: a location policy whose
        database did not load would place no address, and so admit (or refuse) everyone. At run
        time a file that goes missing or bad keeps the database already loaded (GeoDB)."""
        if self.policy.on and not self.geo.status()["loaded"]:
            raise ValueError(f"the location policy is {self.policy.mode}, but the GeoLite2 "
                             f"database did not load from {self.config.geoip_db!r} "
                             f"({self.geo.status()['error']})")

    def _refuse_location(self, s: Session, why: str) -> None:
        """D12: one line saying why, the close, and the operator log."""
        self.bot_say(s, why)
        self.counters.refuse("location")
        self.close(s, f"location policy ({s.location.country or 'unknown'})")
        self.oplog("refused_location", "hub", s.nick or s.ip, why, address=s.ip,
                   country=s.location.country)

    def _refuse_ban(self, s: Session, ban) -> None:
        """P6 §4: one line, then the close; past three tries a minute from an address, no line."""
        now = self.clock()
        w = self._ban_hits.get(s.ip)
        if w is None:
            if len(self._ban_hits) > 4096:
                self._ban_hits.clear()
            w = self._ban_hits[s.ip] = Window(3, 60.0)
        if w.allow(now):
            self.bot_say(s, f"You are banned {until_text(ban)} because: {ban.reason}")
        self.counters.refuse("banned")
        self.close(s, f"banned (#{ban.id} {ban.target})")
        self.oplog("refused_ban", "hub", s.nick or s.ip, ban.reason, ban=ban.id, address=s.ip)

    def oplog(self, action: str, actor: str, target: str, reason: str = "", *,
              duration: float | None = None, **extra) -> dict:
        """P6 §7: one operator action, or a refusal by ban or location policy."""
        entry = {"at": round(time.time(), 3), "hub": self.config.hub_id, "action": action,
                 "actor": actor, "target": target, "reason": reason, "duration": duration,
                 "extra": extra}
        self.actions.append(entry)
        self.counters.add(f"op_{action}")
        if self.connlog is not None:
            self.connlog.record_action(entry)
        if not action.startswith("refused_"):
            log.info("operator %s: %s %s%s", actor, action, target,
                     f" ({reason})" if reason else "")
        return entry

    def _topic_line(self, s: Session) -> str:
        if "HubTopic" in s.supports:
            return f"$HubTopic {escape(self.topic)}|"
        return f"$HubName {escape(self.config.name)} - {escape(self.topic)}|"

    def set_topic(self, text: str) -> None:
        """An admin's !topic: kept, and sent to everyone (P6 §3)."""
        self.topic = text
        self.documents.set_topic(text)
        for u in self.users.values():
            self.send(u, self._topic_line(u) if text else f"$HubName {escape(self.config.name)}|")
        self._dirty()

    def _op_command(self, s: Session, text: str, silent: bool) -> None:
        """``$Kick <victim>|`` and ``$Close <victim>|`` (P6 §2)."""
        name = "$Close " if silent else "$Kick "
        victim = text[len(name):-1].strip()
        if not s.is_op:
            self.counters.drop("kick_not_op")
            self.bot_say(s, "You are not an operator.")
            return
        reason = ""
        last = s.last_pm.get(nick_key(victim))
        if last is not None and self.clock() - last[0] <= 10.0:
            from .codec import unescape
            reason = unescape(last[1])
        try:
            self.ops.kick(s.nick, s.role, victim, reason, silent=silent)
        except OpError as e:
            self.bot_say(s, str(e))

    def _kick(self, s: Session, text: str) -> None:
        self._op_command(s, text, silent=False)

    def _close_cmd(self, s: Session, text: str) -> None:
        self._op_command(s, text, silent=True)

    def _op_force_move(self, s: Session, text: str) -> None:
        """``$OpForceMove $Who:<victim>$Where:<address>$Msg:<reason>|`` (P6 §2)."""
        if not s.is_op:
            self.counters.drop("kick_not_op")
            self.bot_say(s, "You are not an operator.")
            return
        fields = {}
        for part in text[len("$OpForceMove "):-1].split("$"):
            k, sep, v = part.partition(":")
            if sep:
                fields[k] = v
        if not fields.get("Who") or not fields.get("Where"):
            self._malformed(s, "bad_opforcemove")
            return
        from .codec import unescape
        try:
            self.ops.redirect(s.nick, s.role, fields["Who"], unescape(fields["Where"]),
                              unescape(fields.get("Msg", "")))
        except OpError as e:
            self.bot_say(s, str(e))

    # -- search (P4) -------------------------------------------------------------------------------

    @staticmethod
    def query_ok(query: str) -> bool:
        parts = query.split("?", 4)
        if len(parts) != 5:
            return False
        limit, ismax, size, kind, pattern = parts
        if limit not in ("T", "F") or ismax not in ("T", "F") or not size.isdigit():
            return False
        if not (len(kind) == 1 and "1" <= kind <= "9"):
            return False
        if kind == "9":
            return _TTH.fullmatch(pattern) is not None
        return True

    def _search(self, s: Session, text: str) -> None:
        if "ChatOnly" in s.supports:
            self.close(s, "$Search from a ChatOnly client")
            return
        rest = text[len("$Search "):-1]
        addr, sep, query = rest.partition(" ")
        if not sep or not self.query_ok(query):
            self._malformed(s, "bad_search")
            return
        now = self.clock()
        if not s.is_op and not s.search_rate.allow(now):
            self.counters.drop("search_rate")
            if now - s.search_notice >= self.limits.search_notice_interval:
                s.search_notice = now
                self.bot_say(s, "Searches are being dropped: you are searching too often.")
            return
        if addr.startswith("Hub:"):
            if addr[4:] != s.nick:
                self.counters.drop("search_nick")
                return
            out = text
            group = SEARCH_ACTIVE
            self.counters.add("search_passive")
        else:
            _, colon, port = addr.rpartition(":")
            if not colon or not port.isdigit() or not 1 <= int(port) <= 65535:
                self.counters.drop("search_port")
                return
            out = f"$Search {_address(s.ip, port)} {query}|"
            if out != text:
                self.counters.add("search_rewritten")
            group = SEARCH
            self.counters.add("search_active")
        s.last_search = now
        s.sr_sent.clear()
        n = self.group_send(group, self._enc(out), s)
        self.counters.add("search_fanout", n)

    def _sr(self, s: Session, text: str) -> None:
        rest = text[len("$SR "):-1]
        source, sep, _ = rest.partition(" ")
        if not sep or source != s.nick:
            self.counters.drop("sr_source")
            return
        cut = rest.rfind("\x05")
        if cut < 0 or rest.count("\x05") < 2 or cut == len(rest) - 1:
            self.counters.drop("sr_no_target")  # an active search's result, or no target
            return
        target = rest[cut + 1:]
        u = self.users.get(nick_key(target))
        if u is None:
            self.counters.drop("sr_offline")
            return
        if self.clock() - u.last_search > self.limits.sr_window:
            self.counters.drop("sr_late")
            return
        sent = u.sr_sent.get(s.key, 0)
        if sent >= self.limits.sr_per_target:
            self.counters.drop("sr_limit")
            return
        u.sr_sent[s.key] = sent + 1
        self.counters.add("sr")
        self.send(u, f"$SR {rest[:cut]}|")

    # -- connection signalling (P5) ----------------------------------------------------------------

    def _remote(self, s: Session, nick: str) -> Session | None:
        k = nick_key(nick)
        if k == s.key or k == self.bot_key:
            return None
        return self.users.get(k)

    def _ctm_allowed(self, s: Session, remote: Session) -> bool:
        now = self.clock()
        per = s.ctm_remote.get(remote.key)
        if per is None:
            per = s.ctm_remote[remote.key] = Window(self.limits.ctm_per_remote,
                                                    self.limits.ctm_window)
            if len(s.ctm_remote) > 256:  # forget remotes with nothing in their window
                cut = now - self.limits.ctm_window
                for k in [k for k, w in s.ctm_remote.items() if not w.times or w.times[-1] <= cut]:
                    del s.ctm_remote[k]
        if not s.ctm_rate.room(now) or not per.room(now):
            self.counters.drop("ctm_rate")
            return False
        s.ctm_rate.allow(now)
        per.allow(now)
        return True

    def _connect_to_me(self, s: Session, text: str) -> None:
        parts = text[len("$ConnectToMe "):-1].split(" ")
        if len(parts) == 2:
            sender, (remote_nick, addr) = None, parts
        elif len(parts) == 3:
            sender, remote_nick, addr = parts
            if sender != s.nick:
                self.counters.drop("ctm_sender")
                return
        else:
            self._malformed(s, "bad_ctm")
            return
        remote = self._remote(s, remote_nick)
        if remote is None:
            self.counters.drop("ctm_remote")
            return
        _, colon, portpart = addr.rpartition(":")
        m = _PORT.fullmatch(portpart) if colon else None
        if m is None or not 1 <= int(m.group(1)) <= 65535:
            self.counters.drop("ctm_port")
            return
        if not self._ctm_allowed(s, remote):
            return
        where = _address(s.ip, m.group(1)) + m.group(2)
        head = f"$ConnectToMe {sender} " if sender else "$ConnectToMe "
        self.counters.add("ctm")
        self.send(remote, f"{head}{remote_nick} {where}|")

    def _rev_connect_to_me(self, s: Session, text: str) -> None:
        parts = text[len("$RevConnectToMe "):-1].split(" ")
        if len(parts) != 2 or parts[0] != s.nick:
            self.counters.drop("rctm_sender")
            return
        remote = self._remote(s, parts[1])
        if remote is None:
            self.counters.drop("rctm_remote")
            return
        if s.passive and remote.passive:
            # NAT traversal between two passive users is To capture (P5 §2): not forwarded yet.
            self.counters.drop("rctm_both_passive")
            self.bot_say(s, f"{remote.nick} is passive too: neither of you can accept a "
                            "connection, so no transfer is possible.")
            return
        if not self._ctm_allowed(s, remote):
            return
        self.counters.add("rctm")
        self.send(remote, text)

    _handlers: dict[str, tuple[frozenset, Callable]] = {}

    # -- state for native clients and operators ----------------------------------------------------

    @property
    def tree_path(self) -> str:
        return f"{NAMESPACE}.hubs.{self.config.hub_id}"

    def _dirty(self) -> None:
        if self.tree is None or self._publish_timer is not None:
            return
        self._publish_timer = self.call_later(self.config.publish_interval, self._publish_now)

    def _publish_now(self) -> None:
        self._publish_timer = None
        self.publish()

    def publish(self) -> None:
        """Write ``sublevels.hubs.<hub_id>``: the hub's name, topic and user list. Addresses are
        never published."""
        if self.tree is None:
            return
        users = {}
        for u in self.users.values():
            i = u.info
            users[f"u{u.id}"] = {"nick": u.nick, "description": i.description if i else "",
                                 "client": i.client if i else "", "mode": u.mode,
                                 "share": i.share if i else 0, "protocol": u.protocol,
                                 "op": u.is_op, "country": u.location.country}
        self.tree.set_tree(self.tree_path, {
            "name": self.config.name, "topic": self.topic, "bot": self.config.bot_nick,
            "nmdcs": {"address": self.nmdcs_url(), "keyprint": self.keyprint},
            "room": self.config.room,
            "documents": self.documents.as_tree(),
            "geo_attribution": self.geo.status()["attribution"] or "",
            "users": {"count": len(self.users), "list": users},
        })

    def stats(self) -> dict:
        """D14's counters and the live values (the admin API's)."""
        live = Counter(s.protocol for s in self.sessions.values() if s.state != CLOSED)
        clients = Counter((u.info.client or "?") if u.info else "?" for u in self.users.values())
        modes = Counter(u.mode for u in self.users.values())
        return {
            "hub": self.config.hub_id,
            "users": len(self.users),
            "pingers": len(self.pingers),
            "bans": len(self.bans.in_force()),
            "nmdcs_keyprint": self.keyprint,
            "connections": dict(live),
            "tls_connections": sum(1 for s in self.sessions.values()
                                   if s.tls and s.state != CLOSED),
            "logging_in": sum(1 for s in self.sessions.values()
                              if s.state not in (ONLINE, CLOSED)),
            "clients": dict(clients.most_common()),
            "modes": dict(modes),
            "countries": dict(Counter(u.location.country or "?" for u in self.users.values())
                              .most_common()),
            "geo": self.geo.status(),
            "share": sum(u.info.share for u in self.users.values() if u.info),
            "transport": self.config.transport,
            **({"engine": self.engines[0].stats()} if self.engines else {}),
            **self.counters.snapshot(),
        }


_AFTER_LOCK = frozenset({LOCK, NICK})
Hub._handlers = {
    "$Supports": (_AFTER_LOCK, Hub._supports),
    "$Key": (_AFTER_LOCK, Hub._key),
    "$ValidateNick": (frozenset({NICK}), Hub._validate_nick),
    "$MyPass": (frozenset({PASS}), Hub._my_pass),
    "$Version": (frozenset({INFO}), Hub._version),
    "$GetNickList": (frozenset({INFO, ONLINE}), Hub._get_nick_list),
    "$MyINFO": (frozenset({INFO, ONLINE}), Hub._my_info),
    "$BotINFO": (frozenset({INFO, ONLINE}), Hub._bot_info),
    "$Kick": (frozenset({ONLINE}), Hub._kick),
    "$Close": (frozenset({ONLINE}), Hub._close_cmd),
    "$OpForceMove": (frozenset({ONLINE}), Hub._op_force_move),
    "$GetINFO": (frozenset({ONLINE}), Hub._get_info),
    "$To:": (frozenset({ONLINE}), Hub._to),
    "$Search": (frozenset({ONLINE}), Hub._search),
    "$SR": (frozenset({ONLINE}), Hub._sr),
    "$ConnectToMe": (frozenset({ONLINE}), Hub._connect_to_me),
    "$RevConnectToMe": (frozenset({ONLINE}), Hub._rev_connect_to_me),
}
