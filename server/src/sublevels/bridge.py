"""The room bridge: the NMDC hub's users in the relay's conference room, and the two chats joined
(roadmap D3, P2 §8, P3 §5), through arisrvx's bridged-users API (libcvc ARIADNE_CHAT.md "Bridged
users", transfix/libcvc#1302; pycvc.ariadne 4.0.2+):

- Every online NMDC user is a bridged user of the room (``via="nmdc"``): Ariadne clients list them
  in the room's users (no camera, microphone or avatar), with their client and share as the
  detail. They leave the roster when they leave the hub.
- NMDC main chat (users' lines, not hub commands) goes to the room's chat as that user, the text
  unescaped (P1 §3); a participant's room post goes to every NMDC user as ``<[room]name> text``.
- A participant's direct message to an NMDC user arrives as a private message from
  ``[room]name``; an NMDC user's private message to ``[room]name`` goes back to that participant.

Room users appear to NMDC clients under ``[room]name``, which no hub nick may take (the hub
reserves the prefix), so they cannot be confused with hub users (P3 §5). A host without the API
(arisrvx and pycvc before 4.0.2) gets no bridge, and says so.
"""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Any

from .nmdc.codec import escape, unescape

log = logging.getLogger("sublevels.bridge")

VIA = "nmdc"
_NOT_IN_NICK = re.compile(r"[\s$|<>\x00-\x1f\x7f-\x9f]")


def available(host: Any) -> bool:
    """Whether this host has the bridged-users API."""
    return all(hasattr(host, n) for n in ("room_presence", "room_absent", "room_chat_post",
                                          "on_room_chat", "on_room_dm"))


def user_id(nick: str) -> str:
    """The bridged user id for an NMDC nick: the nick itself, or (past the relay's 128 bytes) a
    stable hash of it."""
    if len(nick.encode("utf-8")) <= 128:
        return nick
    return "h" + hashlib.sha256(nick.encode("utf-8")).hexdigest()[:40]


def _human(n: int) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024 or unit == "TiB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n} B"


class RoomBridge:
    """Joins ``hub`` (a sublevels.nmdc.Hub) and ``room`` on ``host`` (a pycvc.ariadne.Host)."""

    def __init__(self, hub, host, room: str):
        self.hub = hub
        self.host = host
        self.room = room
        self.prefix = f"[{room}]"
        self.peers: dict[str, str] = {}  # "[room]name" key (casefolded) -> the participant's pid
        self.present: set[str] = set()  # nick keys we put in the room
        host.on_room_chat(room)(self.heard)
        host.on_room_dm(room)(self.dm)
        hub.bridge = self
        log.info("room bridge: NMDC users join room %r; room users show in NMDC as %sname",
                 room, self.prefix)

    # -- names -------------------------------------------------------------------------------------

    def reserves(self, nick_key: str) -> bool:
        """Hub nicks may not take the room users' prefix."""
        return nick_key.startswith(self.prefix.casefold())

    def shown(self, name: str) -> str:
        """A participant's name as NMDC clients see it: ``[room]name``, nick-safe."""
        clean = _NOT_IN_NICK.sub("_", name.strip()) or "someone"
        return (self.prefix + clean)[:64]

    # -- the hub's side ----------------------------------------------------------------------------

    def _detail(self, s) -> str:
        i = s.info
        if i is None:
            return "NMDC"
        client = i.client or "NMDC"
        return f"{client}, {_human(i.share)} shared"

    def online(self, s) -> None:
        try:
            self.host.room_presence(self.room, user_id(s.nick),
                                    {"name": s.nick, "detail": self._detail(s)}, via=VIA)
            self.present.add(s.key)
            self.hub.counters.add("bridge_presence")
        except ValueError as e:
            log.warning("room bridge: %s could not join the room: %s", s.nick, e)

    def updated(self, s) -> None:
        if s.key in self.present:
            self.online(s)

    def offline(self, s) -> None:
        if s.key in self.present:
            self.present.discard(s.key)
            self.host.room_absent(self.room, user_id(s.nick), via=VIA)

    def chat(self, s, body: str) -> None:
        """A user's main-chat line (already broadcast on the hub): to the room."""
        text = unescape(body)
        if not text.strip():
            return
        try:
            self.host.room_chat_post(self.room, text[:2000], sender_id=user_id(s.nick),
                                     name=s.nick, via=VIA)
            self.hub.counters.add("bridge_to_room")
        except ValueError as e:
            self.hub.counters.drop("bridge_to_room")
            log.info("room bridge: %s's line was refused by the room: %s", s.nick, e)

    def pm(self, s, target: str, body: str) -> bool:
        """A private message to ``target``: True if it is a room user's, and was routed."""
        k = target.casefold()
        if not self.reserves(k):
            return False
        pid = self.peers.get(k)
        if pid is None:
            self.hub.bot_pm(s, f"{target} is not in the room now (reply only to someone who wrote "
                               "to you).")
            return True
        try:
            self.host.room_chat_post(self.room, unescape(body)[:2000], sender_id=user_id(s.nick),
                                     name=s.nick, via=VIA, to=pid)
            self.hub.counters.add("bridge_dm_to_room")
        except ValueError as e:
            self.hub.bot_pm(s, f"Message to {target} not sent: {e}")
        return True

    # -- the room's side ---------------------------------------------------------------------------

    def heard(self, msg) -> None:
        """A participant's room post: to every NMDC user."""
        name = self.shown(msg.name or msg.pid)
        self.peers[name.casefold()] = msg.pid
        self.hub.broadcast(f"<{name}> {escape(msg.text)}|")
        self.hub.counters.add("bridge_from_room")

    def dm(self, msg) -> None:
        """A participant's direct message to a bridged (NMDC) user."""
        target = next((u for u in self.hub.users.values() if user_id(u.nick) == msg.to), None)
        if target is None:
            return
        name = self.shown(msg.name or msg.pid)
        self.peers[name.casefold()] = msg.pid
        self.hub.send(target, f"$To: {target.nick} From: {name} $<{name}> {escape(msg.text)}|")
        self.hub.counters.add("bridge_dm_from_room")
