"""The pinger (P10 §2): it logs in to a listed hub as an NMDC client and reads what it advertises.

It sends ``$Supports NoGetINFO NoHello BotINFO BotList HubTopic`` (P10 §2 lists the first three;
``BotList`` is how a hub says which nicks are bots, which the count leaves out, and ``HubTopic``
gets the topic as ``$HubTopic`` rather than folded into the hub name), the nick ``sublevels-pinger`` (a setting) and a
``$MyINFO`` with no share, then ``$BotINFO <list name> <list URL>|`` once it is in. It reads
``$Lock``'s ``Pk``, ``$HubName``, ``$HubTopic``, ``$HubINFO``, the users' ``$MyINFO``s (their count
and total share, leaving out nicks in ``$BotList``) and ``$OpList``, then disconnects. It never
chats or searches. For an ``nmdcs://`` address it logs in over TLS and records the certificate's
keyprint. ``$HubIsFull`` counts as reachable but full (``ErrCode`` 226).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import ssl

from ..nmdc.codec import CodePage, lock_to_key, unescape

ERR_FULL = 226
SETTLE = 1.5  # seconds of quiet after the user list and $HubINFO before it leaves


class PingError(Exception):
    pass


def _split_addr(address: str) -> tuple[str, str, int]:
    scheme, rest = address.split("://", 1)
    host, _, port = rest.rpartition(":")
    return scheme, host.strip("[]"), int(port)


def keyprint_of(der: bytes) -> str:
    return "SHA256/" + base64.b32encode(hashlib.sha256(der).digest()).decode().rstrip("=")


async def ping(address: str, *, nick: str = "sublevels-pinger", list_name: str = "sublevels",
               list_url: str = "", timeout: float = 20.0, codepage: str = "cp1252") -> dict:
    """Log in to ``address`` (normalised, as store.normalise makes it) and return what the hub
    advertised: an entry's fields, plus ``peer`` (the address connected to). Raises PingError
    when the hub cannot be reached or refuses the login."""
    return await asyncio.wait_for(_ping(address, nick, list_name, list_url, codepage, timeout),
                                  timeout)


async def _ping(address, nick, list_name, list_url, codepage, timeout) -> dict:
    cp = CodePage(codepage)
    scheme, host, port = _split_addr(address)
    ctx = None
    if scheme == "nmdcs":
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE  # hubs' certificates are self-signed: the keyprint pins
    try:
        reader, writer = await asyncio.open_connection(host, port, ssl=ctx)
    except (OSError, ssl.SSLError) as e:
        raise PingError(f"connect: {e}") from None
    out: dict = {"peer": (writer.get_extra_info("peername") or ("", 0))[0], "encoding":
                 codepage.upper()}
    if ctx is not None:
        der = writer.get_extra_info("ssl_object").getpeercert(binary_form=True)
        out["kp"] = keyprint_of(der) if der else ""
    users: dict[str, int] = {}
    bots: set[str] = set()
    ops: set[str] = set()
    buf = b""
    online = False
    loop = asyncio.get_running_loop()
    last_info = None

    def send(text: str | bytes) -> None:
        writer.write(text if isinstance(text, bytes) else cp.encode(text))

    try:
        while True:
            wait = None
            if online and last_info is not None:
                wait = max(0.05, SETTLE - (loop.time() - last_info))
            try:
                chunk = await asyncio.wait_for(reader.read(65536), wait)
            except asyncio.TimeoutError:
                break  # quiet: it has what the hub sends at login
            if not chunk:
                if online:
                    break
                raise PingError("the hub closed the connection during login")
            buf += chunk
            *msgs, buf = buf.split(b"|")
            for raw in msgs:
                if not raw:
                    continue
                if raw.startswith(b"$Lock "):
                    lock = raw[len(b"$Lock "):].split(b" Pk=")[0]
                    if b" Pk=" in raw:
                        out.setdefault("software", cp.decode(raw.split(b" Pk=", 1)[1]))
                    send(b"$Supports NoGetINFO NoHello BotINFO BotList HubTopic|$Key "
                         + lock_to_key(lock) + b"|")
                    send(f"$ValidateNick {nick}|")
                    continue
                m = cp.decode(raw)
                if m.startswith("$HubName "):
                    out["name"] = unescape(m[len("$HubName "):])
                elif m.startswith("$HubTopic "):
                    out["topic"] = unescape(m[len("$HubTopic "):])
                elif m == "$HubIsFull":
                    out["errcode"] = ERR_FULL
                    return out
                elif m.startswith("$ValidateDenide") or m in ("$GetPass", "$BadPass"):
                    raise PingError(f"login refused ({m.split(' ')[0]})")
                elif m.startswith("$Hello ") and m[len("$Hello "):] == nick and not online:
                    online = True
                    send("$Version 1,0091|$GetNickList|")
                    send(f"$MyINFO $ALL {nick} hub-list pinger$ $\x01$$0$|")
                    send(f"$BotINFO {list_name} {list_url}".rstrip() + "|")
                    last_info = loop.time()
                elif m.startswith("$MyINFO $ALL "):
                    rest = m[len("$MyINFO $ALL "):]
                    who = rest.split(" ", 1)[0]
                    fields = rest.split("$")  # …$<email>$<share>$
                    share = fields[-2] if len(fields) >= 2 else ""
                    if who != nick:
                        users[who] = int(share) if share.isdigit() else 0
                    last_info = loop.time()
                elif m.startswith("$BotList "):
                    bots |= {n for n in m[len("$BotList "):].split("$$") if n}
                elif m.startswith("$OpList "):
                    ops |= {n for n in m[len("$OpList "):].split("$$") if n}
                elif m.startswith("$HubINFO "):
                    _hub_info(m[len("$HubINFO "):], out)
                    last_info = loop.time()
    finally:
        writer.close()
    if not online:
        raise PingError("no $Hello")
    people = {n: s for n, s in users.items() if n not in bots}
    out["users"] = len(people)
    out["share"] = sum(people.values())
    out["operators"] = len(ops - bots)
    out["bots"] = len(bots)
    out["errcode"] = 0
    return out


def _hub_info(text: str, out: dict) -> None:
    """``name$address:port$description$max users$min share$min slots$max hubs$type$owner``."""
    f = [unescape(x) for x in text.split("$")]
    f += [""] * (9 - len(f))
    if f[0]:
        out["name"] = f[0]
    if f[2]:
        out["description"] = f[2]
    for key, value in (("max_users", f[3]), ("min_share", f[4]), ("min_slots", f[5]),
                       ("max_hubs", f[6])):
        if value.isdigit():
            out[key] = int(value)
    if f[7]:
        out["software"] = f[7]
    if f[8]:
        out["owner"] = f[8]
