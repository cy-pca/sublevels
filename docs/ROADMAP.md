# Roadmap

sublevels brings Direct Connect into the Ariadne stack. It has two halves:

- **The hub.** A `sublevels` service in an `arisrvx` host. The same host runs TX.WTF and the
  Ariadne conference rooms, which will later hold 3D scenes, apps and games. The service speaks
  the hub side of NMDC, so DC++ and the clients derived from it connect unchanged. Ariadne
  clients reach the same hub natively over the arisrvx state plane.
- **The client.** The sublevels client, an Ariadne app written as document components. It reaches
  sublevels hubs natively and ordinary NMDC hubs, and builds on
  [nicotina](https://github.com/cy-pca/nicotina)'s TX.WTF components and cyprty's conference
  components. nicotina is only a TX.WTF client and does not depend on sublevels (Joe, 2026-11-26).

Both halves are clean-room implementations; see [CLEANROOM.md](../CLEANROOM.md). Implementers
work from `spec/` and the public documents it cites, never from eiskaltdcpp, DC++, verlihub or
our forks of them.

## Decisions

D1-D5 settled 2026-11-14 (Joe: "Draft the roadmap with those defaults"). D6-D10 are Joe's answers to the first draft's open questions, the same day. D11-D14 are Joe's additions of 2026-11-15 (geolocation, location policy, profiles and the connection log, analytics). D15-D16 are Joe's additions of 2026-11-16 (many hub lists at once, and a hublist mode).

| # | Decision |
|---|----------|
| D1 | **The hub is Python on arisrvx's script loop.** An asyncio NMDC listener (TCP 411, and NMDCS over TLS) runs on the loop arisrvx already gives a program (libcvc ARIADNE_SCRIPTING_HOSTS §6.5; S1 transfix/libcvc#1081, S2 #1122, S5 #1130, S6 #1147, S7). FastAPI runs on the same loop for admin and webhooks. Hub state is published as a `sublevels.*` subtree, and native clients act through `room-call`. The hub carries signalling, chat and search. File transfers are peer to peer, as in NMDC, or for native clients go through the relay (D6). |
| D2 | **The sublevels client reaches sublevels natively over the state plane** in every host: `ari` (FTXUI, tvision), `arigl` and the browser. **It reaches vanilla hubs through generic libcvc host features**: TCP and TLS sockets, Tiger-tree hashing, and host-held share and download folders (the X-items below). This keeps nicotina's rule of no custom host and no compiled-in widget. Browsers cannot open raw TCP, so a browser reaches a vanilla hub through a WebSocket-to-TCP gateway in the sublevels service. Sharing and transfers to vanilla peers may be native-only at first. |
| D3 | **Conference features go native first.** Channels, calls, media and end-to-end encrypted messages reach the sublevels client over arisrvx, not over NMDC. Extensions on the NMDC wire (`$Supports` flags) come last (M6), and only if third-party or forked clients need them. The first clean-room spec is therefore vanilla NMDC. |
| D4 | **NMDC first, ADC later (M5).** Modern DC++ speaks both protocols. |
| D5 | **Clean-room workflow.** One spec-writer session may read the GPL implementations, the forks and the private notes archive (transfix/nmdc-dev). It writes `spec/` in its own words and cites its sources. Implementer sessions see only this repository and the public documents. |
| D6 | **Native clients support every transfer mode:** active (direct), passive, and **relay mode**. In relay mode two clients exchange data through the arisrvx relay, inside an end-to-end encrypted channel. The relay forwards opaque bytes and cannot read them, and neither client learns the other's IP address. Clients behind NAT therefore reach each other without port forwarding. This replaces the abandoned NMDCpb HubRelay extension. Vanilla clients keep NMDC's active and passive modes. |
| D7 | **Scale: Verlihub's, per server: 6000+ active clients.** M1 measures whether Python on one loop reaches it. If not, the hot paths (the broadcast and search fan-out) move into native code in libcvc, and the protocol logic stays in Python. |
| D8 | **Text is Windows-1252 by default**, configurable per hub. This is the code page DC++ and its forks use for NMDC on Western-locale Windows. The hub keeps text as Unicode internally and converts at the NMDC edge. |
| D9 | **Hub policy, as Verlihub has it.** A message of the day and a rules document, which every user can read (native clients as documents, vanilla clients as chat and commands). Operators can kick a live connection as a warning, and ban a user for a time or permanently. **Accounts are optional.** Every connection has a **client id** from libcvc's client registry (ARISRV_IDENTITY ID1, transfix/libcvc#1293), and a user without an account is that id. sublevels keeps no identity scheme of its own. The id is scoped to the server by a salt, so two servers cannot match one user. For native clients it is a salted hash of the device id (the OS machine id, host name and OS user, ARIADNE_CONFERENCE_ROADMAP §5f), and the record says whether the client proved its Ed25519 device key. That makes it hard to spoof. **Addresses are not part of the id** (ARISRV_IDENTITY D1, Joe 2026-11-16): they go in the connection log, so a laptop on a new network keeps its id and name. Vanilla NMDC clients send no device id and hold no key. They register through the connection engine (transfix/libcvc#1294 §2.6) and get the registry's fallback id, a salted hash of their addresses. That is weaker, so bans for them also cover addresses and ranges. Usernames are the registry's (unique among active clients, ignoring case), and NMDC nicks are checked against it, so a native user and an NMDC user cannot share a name. Kicks and bans of native users and engine connections go through ID3's admin calls (`server.kick`, `server.ban`, `server.clients`, `server.client`). Hub operators are connections with the `admin` role, from rcon or from TX.WTF. The hub's own commands (P6) map onto the same calls. Revised 2026-11-16 (Joe: "yes"). |
| D10 | **Public and linked.** sublevels hubs register on public hub lists and answer hub-list pingers. Hubs, conference rooms and holons link into one cluster through libcvc's state federation (cross-node replication: `state_cluster_shard`, hash partitioning, delegation leases). Today that federation trusts every peer (ARIADNE_CONFERENCE_ROADMAP §5c), so linking servers run by different operators needs an authenticated peer model first. |
| D11 | **Geolocation, with MaxMind GeoLite2.** The hub looks up every connection's address in a GeoLite2 City database with `maxminddb` (on cvcpkg as `maxminddb-cp312` 3.1.1, built against cvcpkg's `libmaxminddb`), as the `@host.geolocate` hook of ARISRV_IDENTITY ID4c, which arisrvx calls on the script thread for every connection the registry logs: native clients, and NMDC connections through the engine (before `$Lock`). The result fills the registry's `geo.{country, region, city}`. TX.WTF already does the same with `geoip2` (`txwtf/core/geo.py`), storing the result on its sessions and logs and as `last_login_country`. The hub uses the same database file on a shared host. MaxMind's license does not allow redistribution, so the database is never in this repository or a package: the operator downloads it with their own MaxMind account, the hub reads it from a configured path and reloads it when the file changes, and the hub shows MaxMind's attribution. |
| D12 | **Location policy.** A hub can deny connections from listed countries (a denylist), or admit only listed countries (an allowlist), or neither. Operators, and accounts an operator exempts, bypass it. Addresses the database cannot place (private ranges, unknown) follow a setting, admitted by default. A refused connection gets one line saying why, and is logged. The policy is coarse: a VPN or proxy defeats it, and it is not presented as more than that. |
| D13 | **Country on profiles, and a connection log.** A user's profile shows the country they are connecting from: in native clients as a field of the profile, and to NMDC clients, if the hub enables it, as a `[CC]` prefix on the description in `$MyINFO` (a hub rewrite that P2 §5 allows). Operators can open any user's connection log. Its live part is the registry's record (ARISRV_IDENTITY §2.2: the last 32 connections, each with pid, transport, public and local address, opened and closed, and geo; read with `server.client`, or `host.clients` in Python). The registry keeps records for one run only, so sublevels writes each connection to its own database as well, and adds what the hub knows: nick, account, client and version, protocol (NMDC, NMDCS or native), bytes, and why it closed. The admin document shows both. |
| D14 | **Analytics.** The service keeps global counters and time series: live connections (by protocol, TLS and country), logins and refusals by reason, chat, private messages, searches and their fan-out, connection requests, relay-mode bytes (D6), bytes in and out, and the client mix. Live values are published as a series on the state plane, which an admin document draws with libcvc's chart widgets (in libcvc 4.0.0). History is stored downsampled (10 s for a day, 1 min for a month, 1 h for longer) and is readable over the admin API. Analytics cover every hub on the server, and every hub in the cluster once hubs are linked (D10, M7). The live user count is public; the rest is for operators. |
| D15 | **Publish to many public hub lists at once.** A hub keeps a list of hub-list targets, each with its own method, and registers with all of them on a timer (every 15 minutes by default, with jitter) and on an operator's command. Each attempt's result is logged and shown in the admin document. The methods are: **push**, the NMDC registration exchange on TCP port 2501 by default, which Verlihub uses (P9); **pinger**, for lists that find hubs by connecting to them after the operator submits the hub once on the list's site, where the hub's part is answering pingers correctly (P9); and **API**, for any list found to have one. The default targets are Verlihub's two, `hublist.te-home.net` and `hublist.pwiam.com`. Candidates to check in M2 for reachability and method: dchublist.biz, dchublists.com, adchub.com (the FulDC++ list, which finds hubs by pinging them), and DCNF/Hublist (a GitHub aggregator that merges other lists). dchublist.org marks itself unmaintained and is left out. Each entry carries the public addresses (`dchub://` and `nmdcs://` with the keyprint), and once hubs are linked (D10), every public hub of the cluster. |
| D16 | **A hublist mode.** A sublevels instance can also be a hub list. It accepts push registrations from any hub on port 2501, and verifies each with its own pinger, which logs in as an NMDC client and reads the hub's name, topic, `$HubINFO`, user count and share. It keeps each hub's uptime and reliability, drops hubs that stop answering, and lets operators approve, hide or ban entries. It adds the cluster's own hubs (D10), and may mirror other public lists. It serves the list over HTTP in the formats NMDC clients load into their public-hubs window: `hublist.xml.bz2` (the XML list current clients load; the older `.config` text list is deferred, because current clients don't load it, see P10 §4), at an address such as `https://hublist.sublevels.net/hublist.xml.bz2`. It shows the same list inside its hubs (a `+hublist` command) and to native clients as a document. ADC lists come with M5. |

## Architecture

```
 DC++, EiskaltDC++, AirDC++, ncdc ──NMDC/NMDCS (TCP 411)──┐
                                                          │
 programs, webhooks, admin ──HTTP/JSON──► uvicorn ──┐     ▼
                                                    ├─► sublevels service (Python, asyncio)
 sublevels client, ari(gl), web ──room-call──► relay ┘     │  hubs, users, chat, search routing
        ▲                                                 │  host.set_tree("sublevels.…")
        └──── replicated "sublevels.*" subtree ◄── relay ◄┘
                                                  │
                         conference rooms, TX.WTF (same arisrvx host)
```

- **One service layer, three front ends.** NMDC sessions, room-call functions and the HTTP API all
  call the same Python functions. A hub user is one record however they connected, and a vanilla
  user and a native user see each other in the user list, chat and search.
- **The state plane carries what native clients render.** Hub metadata, the user list and the main
  chat are published under `sublevels.hubs.<hub>`. Large user lists need scoped reads (S6,
  `host.read_scope`) and the area-of-interest filters, so a client holds only what it views.
- **Private traffic never enters the shared tree.** Private messages and search results go to one
  connection, as the libcvc ARIADNE_CHAT design does for DMs (`ttl_hops = 0` requests and targeted
  pushes).
- **Identity is TX.WTF's.** Native clients authenticate through the S5 `authenticate` hook with a
  TX.WTF token. NMDC clients log in by nick and password (`$GetPass`/`$MyPass`); the password is a
  hub password the user's TX.WTF account issues, checked against TX.WTF and never stored in the
  clear. Users without an account connect as their registry client id (D9).
- **Packaging.** The server is a Python package (`sublevels`), run as
  `arisrvx --python -m sublevels.server`, and shipped on cvcpkg with its Python dependencies (all
  on cvcpkg for cp312 already, per §6.5). Under plain `python`, the service and its tests run
  without arisrvx (S7 `ServerHarness`). The client ships as the `sublevels-client` cvcpkg package of `.ari`
  documents, which depends on nicotina's and cyprty's component packages (docs/CLIENT_UI.md CU-D3).

## Layout

| Path | What |
|---|---|
| `spec/nmdc/` | The NMDC spec implementers work from: P1-P10 below |
| `spec/adc/` | The ADC spec (M5) |
| `spec/ext/` | Wire extensions (M6) |
| `server/` | The `sublevels` Python package, the server document, and their tests |
| `client/` | The client component set (`.ari` documents), with `ari snapshot` tests |
| `tests/interop/` | Black-box interop runs against third-party clients and hubs |
| `docs/` | This roadmap and design notes |

## Spec items

Each spec file starts with a Sources list. The base documents are the public NMDC protocol
documentation (https://nmdc.sourceforge.io/NMDC.html) and the ADC specification and extensions
(https://adc.sourceforge.io/ADC.html, https://adc.sourceforge.io/ADC-EXT.html). Behaviour these
documents leave open (quirks, ordering, what real clients send) is written from black-box captures
against real clients and hubs, or by the spec writer from the reference implementations, in their
own words.

| # | Area | Needed by |
|---|------|-----------|
| P1 | Framing and text: the `|` delimiter, `$` commands, escaping, `$Lock`/`$Key`, Windows-1252 and other code pages (D8), limits | M1 |
| P2 | Hub login: `$Supports`, `$ValidateNick`, `$GetPass`/`$MyPass`/`$BadPass`, `$Hello`, `$Version`, `$MyINFO`, `$GetNickList`/`$NickList`/`$OpList`/`$BotList`, `$UserIP`, `$HubName`, `$HubTopic`, `$Quit` | M1 |
| P3 | Chat and private messages: main chat, `$To`, bots, `$MCTo`-style messages if real clients use them | M1 |
| P4 | Search: `$Search` active (UDP) and passive (`Hub:`), `$SR` routing back through the hub, TTH searches, flood limits | M1 |
| P5 | Connection signalling: `$ConnectToMe`, `$RevConnectToMe`, passive-to-passive refusal, NAT-T if clients use it | M1 |
| P6 | Operator commands: `$Kick`, `$OpForceMove`, `$Close`, redirects, `$UserCommand`; how real clients show a kick, a ban and its reason | M2 |
| P7 | Transport: NMDCS (TLS) and how clients find it, `ZPipe0`/ZLIB compression | M1 (TLS), M2 (ZLIB) |
| P8 | The client-to-client protocol: `$MyNick`, `$Lock`, `$Direction`, `$ADCGET`/`$ADCSND`, file lists (`files.xml.bz2`), Tiger and TTH, segments, slots | M4 |
| P9 | Hub-list registration and pinger behaviour, the hub's side: the push exchange (port 2501, `$Lock`/`$Key`, the pipe-separated record), what pingers send and read (`$BotINFO`, `$HubINFO`), and keeping pingers out of the user count | M2 |
| P10 | The hub list's side (D16): accepting push registrations, the pinger, and the XML list clients load (`hublist.xml.bz2`) | M2b |

## libcvc features the client needs

Generic host features, for the vanilla path (D2). Each is off unless the host enables it, and is
tested in `ari` with both terminal backends as well as `arigl`.

| # | Feature | Needed by |
|---|---------|-----------|
| X1 | TCP client sockets with optional TLS, behind a deny-by-default host allowlist (as `--net-allow` is for HTTP). Lines or frames arrive as signals, and writes are a verb | M4 |
| X2 | Listening sockets (TCP and UDP) for active mode: the host picks or is told the ports, and a document cannot open one unless the host allows it | M4 |
| X3 | Streaming zlib and bzip2 decompression verbs (ZLIB transfers, `files.xml.bz2`) | M4 |
| X4 | An XML parse verb, or a streaming file-list reader, for file lists | M4 |
| X5 | Host-held share folders: the user picks a folder, the host keeps the handle, and documents see only names, sizes and hashes | M4 |
| X6 | Tiger-tree (TTH) hashing of host-held files, off the frame loop, with a persistent hash cache | M4 |
| X7 | A host-held download folder, written in chunks at offsets (segmented downloads) | M4 |
| X8 | Relay streams: an opaque, flow-controlled byte stream between two connections through the relay, addressed by the relay and not by IP. Neither end learns the other's address (D6) | M3 (relay mode) |
| X9 | End-to-end encryption of a relay stream in the host: a key agreement bound to both device keys, then an authenticated stream cipher (libsodium, as nicotina's L21 uses). The keys stay in the host, never in state | M3 (relay mode) |

The native path (M3) needs no new libcvc features for chat, search and the user list: room-call,
server-published subtrees, scoped reads and the chat widgets exist today. Relay-mode transfers
need X8 and X9, and sharing needs X5-X7. Any gap found while building M3 is added here.

## Milestones

Every milestone ends with tests in CI: pytest and the S7 `ServerHarness` for the service, and
`ari snapshot` for client documents. This repository is public, so CI runs on GitHub-hosted
runners only.

- **M0, groundwork.** Write P1-P5 and P7 (TLS). Create the `server/` package and CI (pytest,
  llm-gate). Run a `ServerHarness` test of an empty service under plain `python`, and an `arisrvx`
  smoke run from cvcpkg.
- **M1, a vanilla hub.** An NMDC hub (P1-P5, P7 TLS) on arisrvx that third-party clients use
  unchanged:
  - log in, see the user list, chat, private messages, search, and a transfer between two
    clients that the hub signals;
  - interop runs in CI with ncdc (terminal) and the AirDC++ web client (headless), and by hand with
    EiskaltDC++ and DC++. All are run as black boxes;
  - measured: memory per user, chat and search fan-out latency, and a soak at 6000+ synthetic
    clients (D7). If Python on one loop falls short, plan the native fan-out before M2.
  - the D14 counters, in memory and over the admin API, since the soak needs them. Charts and
    stored history come in M2.
  - Starts on libcvc 4.0.0 from cvcpkg (arisrvx and pycvc-cp312), with the `ServerHarness` tests
    required in CI.
- **M2, part of the host.**
  - Identity: registry client ids for every connection (D9), NMDC ones through the engine; TX.WTF accounts through S5 for native
    clients and hub passwords for NMDC clients.
  - Policy (D9): message of the day and rules documents, kick as a warning, temporary and
    permanent bans by hash, account, address or range.
  - Public hub lists (D15, P9): push and pinger targets, all at once.
  - Geolocation (D11), location policy (D12), country on profiles and the operators' connection
    log (D13).
  - Analytics (D14): the stored series, the admin document with live and historical charts.
  - Operator commands (P6), ZLIB (P7).
  - An admin API (FastAPI) and an admin document.
  - The `sublevels.*` subtree with scoped reads.
  - A bridge between a hub's main chat and a conference room's chat.
  - Deploy: `hub.sublevels.net` in the sublevels.net zone (transfix/dns-zones), on the arisrvx host
    with TX.WTF and the rooms. TLS is terminated in the service, with the host's certificate,
    because NMDC is raw TCP and the web reverse proxy does not carry it.
- **M2b, a hublist** (D16, P10): registrations on 2501, the pinger, `hublist.xml.bz2` over HTTP, operator moderation, the cluster's hubs, and the `+hublist` view. It is
  tested with DC++, AirDC++ and ncdc loading the list as black boxes.
- **M3, the sublevels client, native.** The client UI plan is [CLIENT_UI.md](CLIENT_UI.md): views, the state model, libcvc widgets U1-U7, and order C0-C5. Components in `client/` for the hub list, user list, main chat,
  private messages, search, the message of the day and rules, and relay-mode transfers (X8, X9)
  between native clients. The sublevels client mounts them and connects to a sublevels hub in `arigl`,
  `ari` (FTXUI, tvision) and the browser, with `ari snapshot` tests of each view. Vanilla and
  native users see each other.
- **M4, the sublevels client on vanilla hubs.** The X-items in libcvc, then P8. The client connects to a vanilla
  hub natively: chat, search, file lists, downloads and uploads with TTH, active and passive mode.
  It is tested against our hub and black-box third-party hubs (uhub, Verlihub, PtokaX). The
  browser reaches vanilla hubs through the service's WebSocket-to-TCP gateway, for chat and search
  first.
- **M5, ADC.** The ADC spec (`spec/adc/`), the hub side in the service, and the client side in the
  components.
- **M6, extensions on the wire.** Only if a third-party or forked client needs conference features
  over NMDC or ADC: a spec in `spec/ext/` that maps channels, calls, media and E2E messages to
  `$Supports` (NMDC) or feature flags (ADC), written fresh from the conference suite's behaviour.
  The earlier NMDCpb design is input for the spec writer only.
- **M7, linked.** Hubs, conference rooms and holons in one cluster through libcvc state
  federation (D10), after an authenticated peer model for servers run by different operators.

## Open questions

- **The forks.** When to archive transfix/eiskaltdcpp, transfix/eiskaltdcpp-py and
  transfix/verlihub, and retire their catx-03 runners: proposed at M4, once the sublevels client replaces
  the forked client for our own use.
- **Relay bandwidth.** Relay mode (D6) puts transfers on the host's uplink. Per-user and per-server
  limits, and whether relay mode is offered to every user or only some, are decided before M3
  ships it.
- **Retention and privacy.** The client id leaves addresses out, except for NMDC clients' fallback
  id (D9), and the connection log (D13) keeps addresses and locations. How long each is kept, what an operator can see, and what
  a user can see of their own log (TX.WTF shows users their own sessions with locations) are
  written down before M2 deploys.
