"""P2 §5: reading ``$MyINFO``. The hub keeps the bytes verbatim; this reads only the fields it uses."""

from __future__ import annotations

from dataclasses import dataclass

PREFIX = "$MyINFO $ALL "

# The mode letters in a tag's M: (P2 §5): active, passive, SOCKS5; '?' when unknown.
ACTIVE, PASSIVE, SOCKS5, UNKNOWN = "A", "P", "5", "?"


@dataclass
class MyInfo:
    nick: str
    description: str
    tag: str  # "<client V:...,M:...>" with its brackets, or ""
    client: str  # the tag's client name, or ""
    mode: str  # ACTIVE, PASSIVE, SOCKS5 or UNKNOWN
    slots: int | None
    hubs: str  # the H: value as written, or ""
    connection: str  # without the flag byte
    flag: int
    email: str
    share: int


def parse_tag(tag: str, ipv6: bool = False) -> tuple[str, dict[str, str]]:
    """``<name V:1,M:A,...>`` as (name, {"V": "1", "M": "A", ...})."""
    inner = tag[1:-1]
    name, _, rest = inner.partition(" ")
    params = {}
    for part in rest.split(","):
        k, sep, v = part.partition(":")
        if sep:
            params[k.strip()] = v.strip()
    return name, params


def mode_of(params: dict[str, str], ipv6: bool = False) -> str:
    """The M: field as one mode. With IP64 it is two characters, IPv4 then IPv6 (P5 §4); the one
    for the family of the user's hub connection is used."""
    m = params.get("M", "")
    if not m:
        return UNKNOWN
    c = m[1] if ipv6 and len(m) >= 2 else m[0]
    return c if c in (ACTIVE, PASSIVE, SOCKS5) else UNKNOWN


def parse(text: str, ipv6: bool = False) -> MyInfo | None:
    """A decoded ``$MyINFO`` message (without its ``|``), or None when its shape is wrong."""
    if not text.startswith(PREFIX):
        return None
    nick, sep, rest = text[len(PREFIX):].partition(" ")
    if not sep or not nick:
        return None
    fields = rest.split("$")
    # "<description><tag>", " ", "<connection><flag>", "<email>", "<share>", "" (the final "$")
    if len(fields) < 5:
        return None
    description = fields[0]
    tag = client = ""
    params: dict[str, str] = {}
    if description.endswith(">") and "<" in description:
        at = description.rfind("<")
        tag = description[at:]
        description = description[:at]
        client, params = parse_tag(tag)
    conn = fields[2]
    flag = ord(conn[-1]) if conn else 0
    connection = conn[:-1] if conn else ""
    share_text = fields[4].strip()
    share = int(share_text) if share_text.isdigit() else 0
    slots_text = params.get("S", "")
    return MyInfo(nick=nick, description=description, tag=tag, client=client,
                  mode=mode_of(params, ipv6) if tag else UNKNOWN,
                  slots=int(slots_text) if slots_text.isdigit() else None,
                  hubs=params.get("H", ""), connection=connection, flag=flag, email=fields[3],
                  share=share)
