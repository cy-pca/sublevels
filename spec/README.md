# sublevels protocol spec

This is what implementers work from (see [CLEANROOM.md](../CLEANROOM.md)). It describes behaviour
on the wire and what a sublevels hub does. It contains no code, identifiers or structure from
any Direct Connect implementation.

## Conventions

- **MUST**, **SHOULD** and **MAY** are used as in RFC 2119.
- Wire examples are literal bytes in the hub's code page, except that `<0x05>` stands for the byte
  0x05 and `…` for elided data. Every NMDC message ends with `|`.
- **Hub**, below, means the sublevels hub. Where other hubs behave differently, the text says so.
- **To capture** marks behaviour that the sources leave open or get wrong. Each one is settled by a
  black-box packet capture against real clients during M1's interop runs (ncdc, AirDC++ web,
  EiskaltDC++, DC++). The result is written into the item, which then names the capture as a
  source.

## Sources

Every file lists the sources it was written from, by these tags.

| Tag | Source |
|---|---|
| [NMDC] | Fredrik Ullner, *NMDC Protocol*, version 1.3 (March 2013), New BSD License. https://nmdc.sourceforge.io/NMDC.html, read 2026-11-15 |
| [ADC-EXT] | *ADC Extensions*. https://adc.sourceforge.io/ADC-EXT.html, read 2026-11-15 |
| [D-n] | Decision n in [docs/ROADMAP.md](../docs/ROADMAP.md) |
| [OBS-VH] | Behaviour of the Verlihub hub (upstream source), observed by the spec writer and stated here as behaviour only |
| [OBS-EI] | Behaviour of the EiskaltDC++ client (upstream source), observed the same way |
| [CAP-ncdc-1.23.1] | Black-box captures of ncdc 1.23.1 (Ubuntu package) against the hub, from M1's interop and transfer runs, 2026-11-16 (cy-pca/sublevels#8, #10) |
| [CAP-airdcpp-2.14.0] | Black-box captures of the AirDC++ web client 2.14.0 (portable build, driven through its documented HTTP API), from the same runs |
| [CAP-…] | Further captures, named by client and version |

[NMDC] is the base for all of `nmdc/`. It is a community reconstruction and has gaps and some
errors; where this spec departs from it, the item says why.

## NMDC items

| # | File | Status |
|---|------|--------|
| P1 | [nmdc/P1-framing.md](nmdc/P1-framing.md): framing, text, escaping, `$Lock`/`$Key` | Draft (M0) |
| P2 | [nmdc/P2-login.md](nmdc/P2-login.md): login, the user list, `$MyINFO` | Draft (M0) |
| P3 | [nmdc/P3-chat.md](nmdc/P3-chat.md): main chat and private messages | Draft (M0) |
| P4 | [nmdc/P4-search.md](nmdc/P4-search.md): search and results | Draft (M0) |
| P5 | [nmdc/P5-connect.md](nmdc/P5-connect.md): connection signalling | Draft (M0) |
| P6 | Operator commands | M2 |
| P7 | [nmdc/P7-transport.md](nmdc/P7-transport.md): TLS (M0); ZLIB (M2) | Draft (M0, TLS part) |
| P8 | The client-to-client protocol | M4 |
| P9 | Hub lists and pingers | M2 |
