# Roadmap

sublevels brings Direct Connect into the Ariadne stack. It has two halves:

- **The hub.** A `sublevels` service in an `arisrvx` host. The same host runs TX.WTF and the
  Ariadne conference rooms, which will later hold 3D scenes, apps and games. The service speaks
  the hub side of NMDC, so DC++ and the clients derived from it connect unchanged. Ariadne
  clients reach the same hub natively over the arisrvx state plane.
- **The client.** An Ariadne document component set. [nicotina](https://github.com/cy-pca/nicotina)
  uses it to reach sublevels hubs natively, and to reach ordinary NMDC hubs.

Both halves are clean-room implementations; see [CLEANROOM.md](../CLEANROOM.md). Implementers
work from `spec/` and the public documents it cites, never from eiskaltdcpp, DC++, verlihub or
our forks of them.

## Decisions

Settled 2026-11-14 (Joe: "Draft the roadmap with those defaults").

| # | Decision |
|---|----------|
| D1 | **The hub is Python on arisrvx's script loop.** An asyncio NMDC listener (TCP 411, and NMDCS over TLS) runs on the loop arisrvx already gives a program (libcvc ARIADNE_SCRIPTING_HOSTS §6.5; S1 transfix/libcvc#1081, S2 #1122, S5 #1130, S6 #1147, S7). FastAPI runs on the same loop for admin and webhooks. Hub state is published as a `sublevels.*` subtree, and native clients act through `room-call`. The hub carries signalling, chat and search only; file transfers stay peer to peer, as in NMDC. |
| D2 | **nicotina reaches sublevels natively over the state plane** in every host: `ari` (FTXUI, tvision), `arigl` and the browser. **It reaches vanilla hubs through generic libcvc host features**: TCP and TLS sockets, Tiger-tree hashing, and host-held share and download folders (the X-items below). This keeps nicotina's rule of no custom host and no compiled-in widget. Browsers cannot open raw TCP, so a browser reaches a vanilla hub through a WebSocket-to-TCP gateway in the sublevels service. Sharing and transfers to vanilla peers may be native-only at first. |
| D3 | **Conference features go native first.** Channels, calls, media and end-to-end encrypted messages reach nicotina over arisrvx, not over NMDC. Extensions on the NMDC wire (`$Supports` flags) come last (M6), and only if third-party or forked clients need them. The first clean-room spec is therefore vanilla NMDC. |
| D4 | **NMDC first, ADC later (M5).** Modern DC++ speaks both protocols. |
| D5 | **Clean-room workflow.** One spec-writer session may read the GPL implementations, the forks and the private notes archive (transfix/nmdc-dev). It writes `spec/` in its own words and cites its sources. Implementer sessions see only this repository and the public documents. |

## Architecture

```
 DC++, EiskaltDC++, AirDC++, ncdc ──NMDC/NMDCS (TCP 411)──┐
                                                          │
 programs, webhooks, admin ──HTTP/JSON──► uvicorn ──┐     ▼
                                                    ├─► sublevels service (Python, asyncio)
 nicotina, arigl, ari, browser ──room-call──► relay ┘     │  hubs, users, chat, search routing
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
  clear. Unregistered guests are a hub setting.
- **Packaging.** The server is a Python package (`sublevels`), run as
  `arisrvx --python -m sublevels.server`, and shipped on cvcpkg with its Python dependencies (all
  on cvcpkg for cp312 already, per §6.5). Under plain `python`, the service and its tests run
  without arisrvx (S7 `ServerHarness`). The client components ship as a cvcpkg package of `.ari`
  documents that the `nicotina` recipe depends on.

## Layout

| Path | What |
|---|---|
| `spec/nmdc/` | The NMDC spec implementers work from: P1-P8 below |
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
| P1 | Framing and text: the `|` delimiter, `$` commands, escaping, `$Lock`/`$Key`, legacy code pages and UTF-8, limits | M1 |
| P2 | Hub login: `$Supports`, `$ValidateNick`, `$GetPass`/`$MyPass`/`$BadPass`, `$Hello`, `$Version`, `$MyINFO`, `$GetNickList`/`$NickList`/`$OpList`/`$BotList`, `$UserIP`, `$HubName`, `$HubTopic`, `$Quit` | M1 |
| P3 | Chat and private messages: main chat, `$To`, bots, `$MCTo`-style messages if real clients use them | M1 |
| P4 | Search: `$Search` active (UDP) and passive (`Hub:`), `$SR` routing back through the hub, TTH searches, flood limits | M1 |
| P5 | Connection signalling: `$ConnectToMe`, `$RevConnectToMe`, passive-to-passive refusal, NAT-T if clients use it | M1 |
| P6 | Operator commands: `$Kick`, `$OpForceMove`, `$Close`, redirects, `$UserCommand` | M2 |
| P7 | Transport: NMDCS (TLS) and how clients find it, `ZPipe0`/ZLIB compression | M1 (TLS), M2 (ZLIB) |
| P8 | The client-to-client protocol: `$MyNick`, `$Lock`, `$Direction`, `$ADCGET`/`$ADCSND`, file lists (`files.xml.bz2`), Tiger and TTH, segments, slots | M4 |

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

The native path (M3) needs no new libcvc features: room-call, server-published subtrees, scoped
reads and the chat widgets exist today. Any gap found while building M3 is added here.

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
  - measured: memory per user, chat and search fan-out latency, and a soak with a synthetic
    1000-user load. Set the scale target from these numbers.
- **M2, part of the host.**
  - TX.WTF identity: S5 for native clients, hub passwords for NMDC clients.
  - Operator commands (P6), ZLIB (P7).
  - An admin API (FastAPI) and an admin document.
  - The `sublevels.*` subtree with scoped reads.
  - A bridge between a hub's main chat and a conference room's chat.
  - Deploy: `hub.sublevels.net` in the sublevels.net zone (transfix/dns-zones), on the arisrvx host
    with TX.WTF and the rooms. TLS is terminated in the service, with the host's certificate,
    because NMDC is raw TCP and the web reverse proxy does not carry it.
- **M3, nicotina native.** Components in `client/` for the hub list, user list, main chat,
  private messages and search. nicotina imports them and connects to a sublevels hub in `arigl`,
  `ari` (FTXUI, tvision) and the browser, with `ari snapshot` tests of each view. Vanilla and
  native users see each other.
- **M4, nicotina on vanilla hubs.** The X-items in libcvc, then P8. nicotina connects to a vanilla
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

## Open questions

- **Transfers between native clients.** Peer to peer over X1/X2 (native hosts only), relayed
  through the service (works in the browser, costs the host bandwidth), or both. Decide before M3.
- **Scale target.** Set from M1's numbers; Python fan-out of search to thousands of users is the
  part to watch.
- **Text encoding.** Which legacy code page a hub assumes for clients that don't send UTF-8, and
  whether that is a per-hub setting.
- **Abuse and content policy.** A public hub carries users' file sharing. Before a public listing:
  registration rules, operator tooling, and a stated policy for the operator.
- **Hub lists and linking.** Whether to register on public hub lists, and whether sublevels hubs
  link with each other the way TX.WTF instances federate.
- **The forks.** When to archive transfix/eiskaltdcpp, transfix/eiskaltdcpp-py and
  transfix/verlihub, and retire their catx-03 runners: proposed at M4, once nicotina replaces the
  forked client for our own use.
