"""Operators (P6): what ``op`` and ``admin`` users may do, and the hub commands.

Every action goes through ``Operators``, whoever asks: an NMDC operator's ``$Kick``, ``$Close``,
``$OpForceMove`` or ``!`` command, a native operator's room-call (``sublevels.kick`` and the rest,
rpc.py), or the admin API. An action the actor may not take raises ``OpError`` with the line the
actor is told. Every action, and every refusal by ban or location policy, is in the operator log
(``Hub.oplog``).

Targets of kicks, redirects and ``!info`` are online NMDC users. Native room users cannot be
targets yet: that needs their principal (ID3's ``server.kick`` and ``server.ban``), which the
room's messages do not carry.
"""

from __future__ import annotations

import re
import time

from .accounts import RANK
from .bans import BanError, parse_duration, parse_target, until_text
from .codec import escape, unescape

KICK_REASON_WINDOW = 10.0  # seconds: the kicker's last private message to the victim (P6 §2)
_ADDRESS = re.compile(r"((dchub|nmdcs|adcs?)://)?(\[[0-9A-Fa-f:.]+\]|[A-Za-z0-9.-]+)(:\d{1,5})?/?")

HELP = [
    ("user", "+help", "this list"),
    ("user", "+motd", "the message of the day"),
    ("user", "+rules", "the hub's rules"),
    ("op", "!kick <nick> [reason]", "disconnect a user (a warning: no ban)"),
    ("op", "!redirect <nick> <address> [reason]", "send a user to another hub"),
    ("op", "!ban <target> <duration> [reason]", "ban nick:, id:, ip:, net: or account: for "
                                                "<n>m, <n>h or <n>d (perm: admins)"),
    ("op", "!unban <target or #id>", "remove a ban (ops: their own)"),
    ("op", "!bans [filter]", "the bans in force"),
    ("op", "!info <nick>", "a user's id, account, address, country, client and connections"),
    ("admin", "!topic <text>", "set the topic"),
    ("admin", "!motd set <text>", "replace the message of the day (\\n for a new line)"),
    ("admin", "!rules set <text>", "replace the rules (\\n for a new line)"),
]


class OpError(Exception):
    pass


def need(role: str, at_least: str) -> None:
    if RANK.get(role, 0) < RANK[at_least]:
        raise OpError("You are not an operator." if at_least == "op"
                      else "Only an admin may do that.")


def valid_address(addr: str) -> bool:
    m = _ADDRESS.fullmatch(addr)
    return m is not None and (m.group(4) is None or 1 <= int(m.group(4)[1:]) <= 65535)


class Operators:
    def __init__(self, hub):
        self.hub = hub

    # -- targets -----------------------------------------------------------------------------------

    def _user(self, nick: str):
        from .hub import nick_key

        k = nick_key(nick)
        if k == self.hub.bot_key:
            raise OpError("The hub bot cannot be the target.")
        if self.hub.bridge is not None and self.hub.bridge.reserves(k):
            raise OpError(f"{nick} is in the conference room: room users cannot be kicked from "
                          "the hub yet.")
        u = self.hub.users.get(k)
        if u is None:
            raise OpError(f"{nick} is not online.")
        return u

    @staticmethod
    def _outranks(role: str, target, what: str) -> None:
        if RANK.get(role, 0) <= RANK.get(target.role, 0):
            raise OpError(f"You cannot {what} {target.nick}: their role is "
                          f"{target.role}, the same as or above yours.")

    # -- §2: kick, close, redirect -----------------------------------------------------------------

    def kick(self, actor: str, role: str, nick: str, reason: str = "", *,
             silent: bool = False) -> dict:
        need(role, "op")
        v = self._user(nick)
        self._outranks(role, v, "kick")
        reason = reason.strip() or f"kicked by {actor}"
        hub = self.hub
        if not silent:
            hub.bot_say(v, f"You were kicked by {actor} because: {reason}")
        status = hub._enc(hub.bot_line(f"{actor} is kicking {v.nick} because: {reason}"))
        from .hub import ALL, OPS
        hub.group_send(ALL if hub.config.kick_notice_all else OPS, status)
        block = hub.config.kick_reconnect_block
        if block > 0:
            hub.bans.add("ip", v.ip, block, f"kicked: {reason}", actor)
        hub.close(v, f"{'closed' if silent else 'kicked'} by {actor}")
        hub.oplog("close" if silent else "kick", actor, v.nick, reason,
                  duration=block or None, address=v.ip)
        return {"ok": True, "nick": v.nick, "reason": reason}

    def redirect(self, actor: str, role: str, nick: str, address: str, reason: str = "") -> dict:
        need(role, "op")
        if not valid_address(address):
            raise OpError(f"{address!r} is not a hub address (host[:port], dchub://, nmdcs://, "
                          "adc:// or adcs://).")
        v = self._user(nick)
        self._outranks(role, v, "redirect")
        reason = reason.strip() or f"redirected by {actor}"
        hub = self.hub
        hub.bot_say(v, f"You are being redirected to {address} because: {reason}")
        hub.send(v, f"$ForceMove {escape(address)}|")
        hub.close(v, f"redirected by {actor} to {address}")
        hub.oplog("redirect", actor, v.nick, reason, address=v.ip, where=address)
        return {"ok": True, "nick": v.nick, "where": address}

    # -- §3, §4: bans ------------------------------------------------------------------------------

    def ban(self, actor: str, role: str, target: str, duration: str, reason: str = "") -> dict:
        need(role, "op")
        try:
            kind, value = parse_target(target)
            seconds = parse_duration(duration)
        except BanError as e:
            raise OpError(str(e)) from None
        hub = self.hub
        if seconds is None:
            need(role, "admin")
        elif RANK[role] < RANK["admin"] and seconds > hub.config.op_max_ban:
            raise OpError(f"An op's bans last at most {_span(hub.config.op_max_ban)}.")
        client_id = ""
        addresses: list[str] = []
        if kind == "id":
            addresses = hub.addresses_of(value)[:16]
        if kind == "nick":
            from .hub import nick_key
            if nick_key(value) == hub.bot_key:
                raise OpError("The hub bot cannot be banned.")
            u = hub.users.get(nick_key(value))
            if u is not None:
                self._outranks(role, u, "ban")
                client_id = u.identity
                addresses = hub.addresses_of(u.identity)[:16]
        reason = reason.strip() or f"banned by {actor}"
        b = hub.bans.add(kind, value, seconds, reason, actor, client_id, addresses)
        closed = []
        for s in list(hub.sessions.values()):
            if s.state != "closed" and hub.bans.matches(b, s) \
                    and RANK.get(s.role, 0) < RANK.get(role, 0):
                hub.bot_say(s, f"You are banned {until_text(b)} because: {b.reason}")
                hub.close(s, f"banned by {actor}")
                closed.append(s.nick or s.ip)
        hub.oplog("ban", actor, b.target, reason, duration=seconds, ban=b.id, closed=closed)
        return {"ok": True, "ban": b.as_dict(hub.bans.wall()), "closed": closed}

    def unban(self, actor: str, role: str, target: str) -> dict:
        need(role, "op")
        hub = self.hub
        t = target.strip()
        if t.lstrip("#").isdigit():
            found = [b for b in [hub.bans.bans.get(int(t.lstrip("#")))] if b is not None]
        else:
            try:
                found = hub.bans.find(*parse_target(t))
            except BanError as e:
                raise OpError(str(e)) from None
        if not found:
            raise OpError(f"No ban on {t}.")
        if RANK[role] < RANK["admin"]:
            mine = [b for b in found if b.by == actor]
            if not mine:
                raise OpError("An op may remove only their own bans.")
            found = mine
        for b in found:
            hub.bans.remove(b.id)
            hub.oplog("unban", actor, b.target, b.reason, ban=b.id)
        return {"ok": True, "removed": [b.id for b in found]}

    def bans(self, text: str = "") -> list[dict]:
        now = self.hub.bans.wall()
        return [b.as_dict(now) for b in self.hub.bans.in_force(text)]

    # -- !info -------------------------------------------------------------------------------------

    def info(self, nick: str) -> dict:
        from .hub import nick_key

        hub = self.hub
        u = hub.users.get(nick_key(nick))
        out = {"nick": nick, "online": u is not None}
        if u is not None:
            row = hub.log_row(u)
            out.update({k: row[k] for k in ("client_id", "account", "address", "country",
                                            "region", "city", "client", "version", "protocol",
                                            "tls")},
                       role=u.role, online_for=round(hub.clock() - u.online_at))
        out["recent"] = hub.connlog.query(nick=nick, limit=5) if hub.connlog is not None else []
        return out

    # -- admin: topic and documents ----------------------------------------------------------------

    def topic(self, actor: str, role: str, text: str) -> dict:
        need(role, "admin")
        self.hub.set_topic(text.strip())
        self.hub.oplog("topic", actor, "", text.strip())
        return {"ok": True, "topic": self.hub.topic}

    def document(self, actor: str, role: str, name: str, text: str) -> dict:
        need(role, "admin")
        try:
            doc = self.hub.set_document(name, text, by=actor)
        except ValueError as e:
            raise OpError(f"{name}: {e}") from None
        self.hub.oplog(f"edit_{name}", actor, "", "")
        return {"ok": True, **doc}

    # -- the hub commands (P6 §3) ------------------------------------------------------------------

    def command(self, s, body: str, private: bool) -> None:
        hub = self.hub
        say = hub.bot_pm if private else hub.bot_say
        word, _, rest = body.partition(" ")
        cmd = word[1:].lower()
        rest = unescape(rest).strip()
        try:
            for line in self._run(s, word[0], cmd, rest):
                say(s, line)
        except OpError as e:
            say(s, str(e))

    def _run(self, s, sigil: str, cmd: str, rest: str) -> list[str]:
        hub, role, me = self.hub, s.role, s.nick
        if sigil == "+" and cmd == "help" or sigil == "!" and cmd == "help":
            lines = ["Commands:"]
            lines += [f"{c}  {what}" for r, c, what in HELP if RANK[role] >= RANK[r]]
            if hub.hublist is not None:
                lines.insert(4, "+hublist [filter]  the hubs this server lists")
            if hub.geo.enabled:
                lines.append(hub.geo.status()["attribution"])
            return lines
        if sigil == "+" and cmd in ("motd", "rules"):
            return hub.documents.lines(cmd) or ["(none yet)"]
        if sigil == "+" and cmd == "hublist" and hub.hublist is not None:
            hubs = hub.hublist.listed(rest, limit=20)
            if not hubs:
                return ["No listed hubs" + (f" match {rest!r}." if rest else " yet.")]
            return [f"{hub.hublist.cfg.name}: {len(hubs)} hub(s), most users first"] + [
                f"{r.get('name') or '?'}  {r['address']}  {int(r.get('users') or 0)} users"
                + (f"  {r['country']}" if r.get("country") else "") for r in hubs]
        if sigil == "!":
            args = rest.split(" ")
            if cmd == "kick":
                if not rest:
                    return ["Usage: !kick <nick> [reason]"]
                self.kick(me, role, args[0], " ".join(args[1:]))
                return [f"Kicked {args[0]}."]
            if cmd == "redirect":
                if len(args) < 2:
                    return ["Usage: !redirect <nick> <address> [reason]"]
                self.redirect(me, role, args[0], args[1], " ".join(args[2:]))
                return [f"Redirected {args[0]} to {args[1]}."]
            if cmd == "ban":
                if len(args) < 2:
                    return ["Usage: !ban <target> <duration> [reason]: target nick, nick:, id:, "
                            "ip:, net: or account:; duration <n>m, <n>h, <n>d or perm"]
                r = self.ban(me, role, args[0], args[1], " ".join(args[2:]))
                b = r["ban"]
                return [f"Ban #{b['id']} on {b['target']} "
                        f"{'permanently' if b['permanent'] else 'for ' + args[1]}"
                        + (f"; closed {', '.join(r['closed'])}" if r["closed"] else "") + "."]
            if cmd == "unban":
                if not rest:
                    return ["Usage: !unban <target or #id>"]
                r = self.unban(me, role, rest)
                return [f"Removed ban {', '.join('#' + str(i) for i in r['removed'])}."]
            if cmd == "bans":
                need(role, "op")
                bans = self.bans(rest)
                if not bans:
                    return ["No bans in force" + (f" matching {rest!r}." if rest else ".")]
                return [f"#{b['id']} {b['target']} "
                        f"{'permanent' if b['permanent'] else 'until ' + _when(b['end'])} "
                        f"by {b['by']}: {b['reason']}" for b in bans]
            if cmd == "info":
                need(role, "op")
                if not rest:
                    return ["Usage: !info <nick>"]
                i = self.info(args[0])
                lines = []
                if i["online"]:
                    where = ", ".join(x for x in (i["city"], i["region"], i["country"]) if x)
                    lines.append(f"{i['nick']}: id {i['client_id']}, account "
                                 f"{i['account'] or '(none)'}, role {i['role']}, {i['address']}"
                                 f"{' (' + where + ')' if where else ''}, {i['client'] or '?'} "
                                 f"{i['version']}, {i['protocol']}, online for "
                                 f"{_span(i['online_for'])}")
                else:
                    lines.append(f"{args[0]} is not online.")
                for r in i["recent"]:
                    lines.append(f"  {_when(r['opened_at'])} from {r['address']} "
                                 f"{r['country'] or ''} {r['client'] or ''}: {r['close_reason']}")
                return lines
            if cmd == "topic":
                self.topic(me, role, rest)
                return ["Topic set."]
            if cmd in ("motd", "rules"):
                sub, _, text = rest.partition(" ")
                if sub != "set":
                    return [f"Usage: !{cmd} set <text> (\\n for a new line)"]
                self.document(me, role, cmd, text.replace("\\n", "\n"))
                return [f"The {'message of the day' if cmd == 'motd' else 'rules'} set."]
        return [f"No such command: {sigil}{cmd}. Type +help."]

    # -- $UserCommand menus (P6 §5) ----------------------------------------------------------------

    def menus(self, role: str) -> list[str]:
        """The ``$UserCommand`` lines for ``role``: clear, then the items it may use."""
        def item(context: int, title: str, command: str) -> str:
            raw = "<%[mynick]> " + command + "|"
            return f"$UserCommand 2 {context} {title}${escape(raw)}|"

        out = ["$UserCommand 255 15|",
               item(1, "Message of the day", "+motd"), item(1, "Rules", "+rules")]
        if RANK.get(role, 0) >= RANK["op"]:
            out += [item(6, "Kick", "!kick %[nick] %[line:Reason]"),
                    item(6, "Redirect", "!redirect %[nick] %[line:Address] %[line:Reason]"),
                    item(6, "Ban 1 hour", "!ban %[nick] 1h %[line:Reason]"),
                    item(6, "Ban 1 day", "!ban %[nick] 1d %[line:Reason]")]
            if RANK[role] >= RANK["admin"]:
                out.append(item(6, "Ban permanently", "!ban %[nick] perm %[line:Reason]"))
            out.append(item(6, "Info", "!info %[nick]"))
        return out


def _span(seconds: float) -> str:
    seconds = int(seconds)
    for unit, n in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= n:
            return f"{seconds // n}{unit}"
    return f"{seconds}s"


def _when(ts: float | None) -> str:
    return time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(ts)) if ts else "?"
