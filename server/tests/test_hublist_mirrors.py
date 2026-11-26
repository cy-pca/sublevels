"""P10 §3, §4 follow-ups: mirrors (only a local HTTP server, never a real list), the instance's own
hub, the listed hubs for native clients, and +hublist in the hub."""

import asyncio
import bz2
import functools
import http.server
import threading

import pytest

pytest.importorskip("sqlalchemy")

from nmdc_fakes import World  # noqa: E402
from sublevels.hublist import mirror  # noqa: E402
from sublevels.hublist.listxml import render  # noqa: E402
from sublevels.hublist.service import Hublist, HublistConfig  # noqa: E402
from sublevels.nmdc import HubConfig  # noqa: E402

LIST = b"""<?xml version="1.0" encoding="UTF-8"?>
<Hublist Name="other"><Hubs><Columns/>
  <Hub Address="dchub://one.example:411" Name="One" Users="40" Shared="1000" Country="de"/>
  <Wrapper><Hub Address="nmdcs://two.example:4111" Name="Two" Users="7" Shared="5.0" KP="SHA256/X"/></Wrapper>
  <Hub Address="not a hub" Name="Bad"/>
</Hubs></Hublist>"""


@pytest.fixture
def web(tmp_path):
    (tmp_path / "list.xml").write_bytes(LIST)
    (tmp_path / "list.xml.bz2").write_bytes(bz2.compress(LIST))
    (tmp_path / "dtd.xml").write_bytes(b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><x/>')
    (tmp_path / "junk.xml").write_bytes(b"<Hublist><Hub")
    (tmp_path / "big.xml.bz2").write_bytes(bz2.compress(b" " * (mirror.MAX_XML + 10)))
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(tmp_path))
    handler.log_message = lambda *a: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def hublist(tmp_path, **kw):
    cfg = HublistConfig(enabled=True, db=f"sqlite:///{tmp_path / 'h.sqlite3'}", name="ours",
                        **{k: v for k, v in kw.items() if k == "mirrors"})
    return Hublist(cfg, **{k: v for k, v in kw.items() if k != "mirrors"})


def test_fetch_and_parse(web):
    for name in ("list.xml", "list.xml.bz2"):
        hubs = mirror.parse(mirror.fetch(f"{web}/{name}"))
        assert [h["address"] for h in hubs] == ["dchub://one.example:411",
                                                "nmdcs://two.example:4111"]
    one, two = hubs
    assert one["country"] == "DE" and one["users"] == 40 and two["share"] == 5
    assert two["kp"] == "SHA256/X"
    with pytest.raises(mirror.MirrorError, match="DTD"):
        mirror.parse(mirror.fetch(f"{web}/dtd.xml"))
    with pytest.raises(mirror.MirrorError, match="not XML"):
        mirror.parse(mirror.fetch(f"{web}/junk.xml"))
    with pytest.raises(mirror.MirrorError, match="bytes of XML"):
        mirror.fetch(f"{web}/big.xml.bz2")
    with pytest.raises(mirror.MirrorError):
        mirror.fetch(f"{web}/missing.xml")


def test_our_own_list_parses_back(tmp_path):
    xml = render([{"address": "dchub://a.example:411", "name": "A & <b>", "users": 3}],
                 lambda a: 99.0, name="n", url="u", now=0)
    assert mirror.parse(xml)[0]["name"] == "A & <b>"


def test_mirror_merges_new_hubs_as_pending(tmp_path, web):
    h = hublist(tmp_path, mirrors=(f"{web}/list.xml.bz2", f"{web}/missing.xml"))
    h.store.register("dchub://one.example:411", "10.0.0.1", {"name": "One (pushed)"})
    h.store.set_state("dchub://one.example:411", "banned")
    got = asyncio.run(h.mirror_once())
    ok = got[f"{web}/list.xml.bz2"]
    assert ok["ok"] and ok["added"] == 1 and ok["known"] == 1
    assert got[f"{web}/missing.xml"]["ok"] is False
    two = h.store.get("nmdcs://two.example:4111")
    assert two["state"] == "pending" and two["source"] == f"mirror:{web}/list.xml.bz2"
    one = h.store.get("dchub://one.example:411")
    assert one["state"] == "banned" and one["name"] == "One (pushed)"  # untouched
    assert h.stats()["mirrors"][f"{web}/list.xml.bz2"]["added"] == 1
    h.store.close()


def test_own_hub_listed_published_and_plus_hublist(tmp_path):
    class Tree:
        values = {}

        def set_tree(self, key, value):
            self.values[key] = value

    tree = Tree()
    h = hublist(tmp_path, tree=tree, own="hub.example.org:4012")
    h.include_own()
    me = h.store.get("dchub://hub.example.org:4012")
    assert me["state"] == "pending" and me["source"] == "self"
    for addr, users, cc in (("dchub://small.example:411", 2, "FR"),
                            ("dchub://big.example:411", 90, "DE")):
        h.store.register(addr, "10.0.0.1", {"name": addr.split("//")[1].split(".")[0]})
        h.store.record_ping(addr, True, {"users": users, "country": cc}, 0)
    assert [r["address"] for r in h.listed()] == ["dchub://big.example:411",
                                                  "dchub://small.example:411"]
    assert [r["address"] for r in h.listed("small")] == ["dchub://small.example:411"]
    h.publish()
    pub = tree.values["sublevels.hublist"]
    assert pub["name"] == "ours" and pub["count"] == 2
    assert pub["hubs"]["h0"]["address"] == "dchub://big.example:411"
    assert pub["hubs"]["h0"]["reliability"] == 100.0 and pub["hubs"]["h0"]["country"] == "DE"

    w = World(HubConfig())
    w.hub.hublist = h
    s, conn = w.login("alice")
    conn.take()
    w.send(s, "<alice> +hublist|")
    out = [m for m in conn.take() if m.startswith("<sublevels> ")]
    assert out[0] == "<sublevels> ours: 2 hub(s), most users first|"
    assert out[1] == "<sublevels> big  dchub://big.example:411  90 users  DE|"
    w.send(s, "<alice> +hublist nothing-matches|")
    assert conn.take()[-1] == "<sublevels> No listed hubs match 'nothing-matches'.|"
    w.send(s, "<alice> +help|")
    assert any("+hublist" in m for m in conn.take())
    h.store.close()


def test_without_hublist_mode_there_is_no_plus_hublist():
    w = World()
    s, conn = w.login("alice")
    conn.take()
    w.send(s, "<alice> +hublist|")
    assert conn.take()[-1] == "<sublevels> No such command: +hublist. Type +help.|"
