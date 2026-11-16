"""A hub without sockets: captured connections, a clock and timers that tests move by hand."""

from __future__ import annotations

from sublevels.nmdc import Hub, HubConfig
from sublevels.nmdc.codec import CodePage, lock_to_key

CP = CodePage("cp1252")
ALL = "NoHello NoGetINFO UserIP2 BotList HubTopic"


class Clock:
    def __init__(self):
        self.now = 1000.0
        self.timers: list[list] = []  # [when, fn, cancelled]

    def __call__(self) -> float:
        return self.now

    def call_later(self, delay, fn):
        t = Timer(self, self.now + delay, fn)
        self.timers.append(t)
        return t

    def advance(self, seconds: float) -> None:
        end = self.now + seconds
        while True:
            due = sorted((t for t in self.timers if not t.cancelled and t.when <= end),
                         key=lambda t: t.when)
            if not due:
                break
            t = due[0]
            self.now = max(self.now, t.when)
            t.cancelled = True
            t.fn()
        self.now = end


class Timer:
    def __init__(self, clock, when, fn):
        self.clock, self.when, self.fn, self.cancelled = clock, when, fn, False

    def cancel(self):
        self.cancelled = True


class Conn:
    """What the hub wrote, and whether it closed."""

    def __init__(self):
        self.data = bytearray()
        self.closed = False
        self.seen = 0  # messages already returned by take()

    def write(self, data: bytes) -> None:
        assert not self.closed, "a write after close"
        self.data += data

    def close(self) -> None:
        self.closed = True

    def messages(self) -> list[str]:
        """Every message written, decoded, with its '|'."""
        text = CP.decode(bytes(self.data))
        return [m + "|" for m in text.split("|")[:-1]]

    def take(self) -> list[str]:
        """The messages written since the last take()."""
        ms = self.messages()
        new = ms[self.seen:]
        self.seen = len(ms)
        return new


class World:
    """A hub, its clock, and helpers that act as clients."""

    def __init__(self, config: HubConfig | None = None, tree=None, accounts=None):
        self.clock = Clock()
        self.hub = Hub(config or HubConfig(), tree=tree, accounts=accounts, clock=self.clock,
                       call_later=self.clock.call_later)

    def connect(self, ip="10.0.0.1", tls=False):
        conn = Conn()
        s = self.hub.connect(conn, ip, 40000, "nmdcs" if tls else "nmdc", tls)
        return s, conn

    def send(self, s, *messages: str) -> None:
        for m in messages:
            self.hub.message(s, CP.encode(m))

    def key_for(self, conn) -> str:
        first = bytes(conn.data).split(b"|", 1)[0]
        lock = first[len(b"$Lock "):].split(b" Pk=")[0]
        return CP.decode(lock_to_key(lock))

    def login(self, nick, *, ip="10.0.0.1", supports=ALL, mode="A", share=1000, tls=False,
              description="", client="test"):
        s, conn = self.connect(ip, tls)
        self.send(s, f"$Supports {supports}|", f"$Key {self.key_for(conn)}|",
                  f"$ValidateNick {nick}|")
        if s.state == "closed":
            return s, conn
        self.send(s, "$Version 1,0091|", "$GetNickList|", myinfo(nick, mode=mode, share=share,
                                                                    description=description,
                                                                    client=client))
        return s, conn


def myinfo(nick, *, mode="A", share=1000, description="", client="test", flag="\x01", email=""):
    """A $MyINFO as a client writes it; mode None leaves the tag out."""
    tag = f"<{client} V:1.0,M:{mode},H:1/0/0,S:3>" if mode else ""
    return f"$MyINFO $ALL {nick} {description}{tag}$ $LAN(T3){flag}${email}${share}$|"
