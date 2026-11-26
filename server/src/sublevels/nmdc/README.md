# sublevels.nmdc

The hub side of NMDC (roadmap M1). It is written only from [`spec/nmdc/`](../../../../spec/nmdc/)
(P1-P5, P7's TLS part) and the public documents that spec cites; see
[CLEANROOM.md](../../../../CLEANROOM.md). Implementers do not open the source of any other Direct
Connect implementation, and a change here names the spec sections it implements.

| File | Spec | What |
|---|---|---|
| `codec.py` | P1 §2-§5 | The code page edge (Windows-1252 by default, D8), escaping, `$Lock`, `$Key` |
| `limits.py` | P1 §6, P2-P5 limits | Every limit as a setting, and the rate limiters |
| `myinfo.py` | P2 §5 | Reading `$MyINFO`'s fields; the bytes are kept verbatim |
| `hub.py` | P2, P3, P4, P5, P9 §2, D12, D13 | The login state machine, the location policy before `$Lock`, the `[CC]` prefix, the user list, chat, private messages, the bot and its `+help`/`+motd`/`+rules`, search and `$SR` routing, `$ConnectToMe`/`$RevConnectToMe`, hub-list pingers and `$HubINFO` |
| `listener.py` | P1 §1, P7 §1 | The asyncio listeners: NMDC (411) and NMDCS (4111, TLS 1.2+, certificate reloaded on change) |
| `engine.py` | P1 §1 | Plain NMDC on libcvc's native connection engine (ARIADNE_NET_ENGINE N2), the default where pycvc has it |
| `hublists.py` | P9 §1, D15 | Registration with hub lists: push targets on a timer, with jitter, back-off and every result kept for `/admin/hublists` |
| `documents.py` | P2 §7, D9 | The message of the day and the rules: configured text until an operator edits it, kept in the data directory, published for native clients |
| `geo.py` | D11, D12 | GeoLite2 lookups (a plain function, for `@host.geolocate` later), reloaded on change; the location policy |
| `connlog.py` | D13 | The connection log: one row per connection in SQLite (SQLAlchemy), written off the loop |
| `counters.py` | D14 | The counters, in memory; `Hub.stats()` adds the live values |
| `config.py` | | `HubConfig`, from `SUBLEVELS_*` variables |

The hub runs on the arisrvx script loop with the HTTP API and the room-call exports. Plain NMDC
has two transports behind one seam: libcvc's native connection engine, whose I/O threads split
input on `|`, queue output and write it, or the loop's own asyncio sockets. `SUBLEVELS_NMDC_TRANSPORT`
chooses: `auto` (the default) is the engine when pycvc has it (libcvc 4.0.9+) and no capture
directory is set, else asyncio; `engine` requires it; `asyncio` is the fallback. Every fan-out is a send to one of the hub's groups (`all`,
`hello`, `search`, `search_active`, `ops`, `ops_userip`): on the engine, one `broadcast()` for the
group, however large. NMDCS stays on asyncio until the engine has TLS (N3). The hub knows a
connection only by `write()` and `close()`, so its tests (`server/tests/test_nmdc_p*.py`) run
without sockets; `test_nmdc_listener.py` runs it on real ones.

It publishes `sublevels.hubs.<hub_id>` (`name`, `topic`, `bot`, `users.count`, and `users.list`
keyed by connection id, without addresses) at most once a second, for native clients. `GET /stats`
gives the user count, and `GET /admin/stats` (a bearer token) the D14 counters.

Not in M1: accounts and passwords against TX.WTF (the state machine has the `pass` state, with no
accounts behind it), bans and location policy (D9, D11, D12), `$MCTo`, ZLIB, TLS on the plain
port, operator commands (P6), and native users in the NMDC view (P2 §8).
