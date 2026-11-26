"""The list DC clients load (P10 §4): XML 1.0 in UTF-8, as [DCNF]'s generator lays it out, plain
and bz2-compressed, and one per country with listed hubs."""

from __future__ import annotations

import bz2
import hashlib
import time
from xml.sax.saxutils import quoteattr

# The attributes, in [DCNF]'s order with KP last, and the column type each is declared with.
COLUMNS = (("Address", "string"), ("Name", "string"), ("Description", "string"),
           ("Users", "int"), ("Country", "string"), ("Shared", "bytes"), ("Minshare", "bytes"),
           ("Minslots", "int"), ("Maxhubs", "int"), ("Maxusers", "int"),
           ("Reliability", "string"), ("Rating", "string"), ("Encoding", "string"),
           ("Software", "string"), ("Website", "string"), ("Email", "string"),
           ("Operators", "int"), ("Bots", "int"), ("Status", "string"), ("Failover", "string"),
           ("KP", "string"))


def _attrs(e: dict, reliability: float | None) -> dict[str, str]:
    def n(v):
        return str(int(v or 0))

    return {"Address": e["address"], "Name": e.get("name") or "",
            "Description": e.get("description") or e.get("topic") or "",
            "Users": n(e.get("users")), "Country": e.get("country") or "",
            "Shared": n(e.get("share")), "Minshare": n(e.get("min_share")),
            "Minslots": n(e.get("min_slots")), "Maxhubs": n(e.get("max_hubs")),
            "Maxusers": n(e.get("max_users")),
            "Reliability": "" if reliability is None else f"{reliability:.1f}%",
            "Rating": "", "Encoding": e.get("encoding") or "",
            "Software": e.get("software") or "", "Website": "", "Email": e.get("owner") or "",
            "Operators": n(e.get("operators")), "Bots": n(e.get("bots")),
            "Status": "Full" if e.get("errcode") == 226 else "Online", "Failover": "",
            "KP": e.get("kp") or ""}


def render(entries: list[dict], reliability, *, name: str, url: str, now: float) -> bytes:
    """The XML for ``entries`` (listed ones); ``reliability(address)`` gives each one's."""
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           f"<Hublist Name={quoteattr(name)} Address={quoteattr(url)} "
           f"Generated={quoteattr(time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(now)))}>",
           "  <Hubs>", "    <Columns>"]
    out += [f'      <Column Name="{c}" Type="{t}"/>' for c, t in COLUMNS]
    out.append("    </Columns>")
    for e in entries:
        a = _attrs(e, reliability(e["address"]))
        out.append("    <Hub " + " ".join(f"{c}={quoteattr(a[c])}" for c, _ in COLUMNS) + "/>")
    out += ["  </Hubs>", "</Hublist>", ""]
    return "\n".join(out).encode("utf-8")


class Lists:
    """The rendered lists, rebuilt at most every ``every`` seconds: ``hublist.xml``,
    ``hublist.xml.bz2`` and ``hublist-<cc>.xml.bz2`` for each country with listed hubs."""

    def __init__(self, store, *, name: str, url: str, every: float = 300.0, clock=time.time):
        self.store, self.name, self.url, self.every, self.clock = store, name, url, every, clock
        self.built_at = -1e18
        self.files: dict[str, tuple[bytes, str, float]] = {}  # name -> (body, etag, mtime)

    def rebuild(self) -> None:
        now = self.clock()
        listed = self.store.all("listed")
        files = {}
        rel = self.store.reliability
        xml = render(listed, rel, name=self.name, url=self.url, now=now)
        files["hublist.xml"] = xml
        files["hublist.xml.bz2"] = bz2.compress(xml)
        for cc in sorted({(e.get("country") or "").lower() for e in listed} - {""}):
            mine = [e for e in listed if (e.get("country") or "").lower() == cc]
            files[f"hublist-{cc}.xml.bz2"] = bz2.compress(
                render(mine, rel, name=f"{self.name} ({cc.upper()})", url=self.url, now=now))
        self.files = {k: (v, '"' + hashlib.sha256(v).hexdigest()[:32] + '"', now)
                      for k, v in files.items()}
        self.built_at = now

    def get(self, name: str) -> tuple[bytes, str, float] | None:
        if self.clock() - self.built_at >= self.every:
            self.rebuild()
        return self.files.get(name)
