"""P7 §3: ZPipe0, the user list at login compressed for clients that list ZPipe0."""

import zlib

from nmdc_fakes import World, myinfo
from sublevels.nmdc import HubConfig


def login_raw(w, nick, supports):
    s, conn = w.connect()
    w.send(s, f"$Supports {supports}|", f"$Key {w.key_for(conn)}|", f"$ValidateNick {nick}|",
           "$Version 1,0091|", "$GetNickList|", myinfo(nick))
    return s, conn


def test_off_by_default_and_not_advertised():
    w = World()
    w.login("alice")
    s, conn = login_raw(w, "bob", "NoHello NoGetINFO ZPipe0")
    data = bytes(conn.data)
    assert b"$ZOn" not in data and b"ZPipe0" not in data.split(b"$Hello")[0]


def test_on_compresses_the_user_list_for_zpipe0_clients():
    w = World(HubConfig(zpipe="on", zpipe_min=0))
    w.login("alice", share=1234)
    s, conn = login_raw(w, "bob", "NoHello NoGetINFO ZPipe0")
    data = bytes(conn.data)
    assert b"$Supports NoGetINFO NoHello UserIP2 BotList HubTopic HubINFO UserCommand ZPipe0|" in data
    head, _, rest = data.partition(b"$ZOn|")
    assert rest, "no $ZOn|"
    d = zlib.decompressobj()
    plain = d.decompress(rest)
    assert myinfo("alice", share=1234).encode() in plain and b"$OpList " in plain
    assert d.eof and d.unused_data.startswith(b"<sublevels> ")  # plain messages again after it
    assert w.hub.counters.snapshot()["totals"]["zpipe_blocks"] == 1


def test_only_for_clients_that_list_it_and_only_when_large():
    w = World(HubConfig(zpipe="on", zpipe_min=0))
    s, conn = login_raw(w, "carol", "NoHello NoGetINFO")
    assert b"$ZOn" not in bytes(conn.data)
    w = World(HubConfig(zpipe="on"))  # the default minimum: 4 KiB
    s, conn = login_raw(w, "dave", "NoHello NoGetINFO ZPipe0")
    assert b"$ZOn" not in bytes(conn.data)


def test_the_probes_other_framing():
    w = World(HubConfig(zpipe="zon-blob", zpipe_min=0))
    s, conn = login_raw(w, "erin", "NoHello NoGetINFO ZPipe0")
    assert b"$ZOn x" in bytes(conn.data)
