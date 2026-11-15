# P7. Transport: TLS, and compression

Sources: [NMDC] §4.4.38, §4.5.16, §4.5.17, §4.6.19; [ADC-EXT] §3.16 (KEYP); [OBS-VH]; [OBS-EI];
the D-items in docs/ROADMAP.md (D1, M2's deployment).

## 1. Hub TLS (`nmdcs://`)

- A client asked to connect to `nmdcs://host:port` opens TCP and starts a TLS handshake at once.
  When it completes, the NMDC session (P1, P2) runs inside TLS, and the hub sends `$Lock` first as
  usual.
- [NMDC] does not specify hub TLS. The behaviour above is what `nmdcs://` means to the clients that
  support it; the details are To capture.
- **Listeners.** The hub listens for plain NMDC on 411 and for TLS on a second port, 4111 by
  default. Both are hub settings.
- **TLS on the plain port.** The hub MAY also accept TLS on the plain port, by telling a TLS
  handshake from plain NMDC by the client's first byte (0x16 is a TLS record). NMDC hubs speak
  first, so a plain client sends nothing until it sees `$Lock`. The hub therefore waits for the
  client's first byte for a short time, 300 ms by default, before sending `$Lock` in clear. This
  adds that wait to every plain login, so it is off by default.
- **Versions.** TLS 1.2 and 1.3. Older versions are refused, though some old clients only have
  them.
- **Certificate.** The hub presents a publicly trusted certificate for its host name (for
  `hub.sublevels.net`, from the arisrvx host). It reloads the certificate when the file changes,
  without a restart and without dropping connections.
- **Keyprints.** [ADC-EXT] §3.16 (KEYP) adds `?kp=SHA256/<base32 of the certificate's SHA-256>` to
  hub addresses, so clients can pin a certificate that no authority signs. The hub publishes its
  keyprint (in its hub-list entry and on its web page) beside the plain address, so clients that
  read `kp` in `nmdcs://` addresses can pin it.
- **Requiring TLS.** With `tls_required` on, a plain connection gets one bot line giving the
  `nmdcs://` address, then `$ForceMove nmdcs://host:port|` and close. Whether clients follow a
  `$ForceMove` to `nmdcs://` is To capture. Passwords are refused over plain connections by default
  (P2 §4).

## 2. Client-to-client TLS

- A client that listed `TLS` in `$Supports` may send `$ConnectToMe` with an `S` after the port,
  meaning its listener speaks TLS [NMDC] §4.6.19. The hub keeps the suffix (P5 §1).
- The `$MyINFO` flag bits 0x10 and 0x20 say the user supports TLS (P2 §5). The hub only reads them.
- The hub's own `$Supports` lists `TLS` once it has checked that clients do not need the hub to do
  anything else for client TLS.

## 3. Compression (M2)

- **`ZPipe0`.** A client that listed `ZPipe0` accepts `$ZOn|` followed by a zlib stream holding
  ordinary NMDC messages; the stream's end returns the connection to plain messages [NMDC]
  §4.5.17. [NMDC] writes it as `$ZOn blob|`, which leaves open whether the `|` comes before or after
  the compressed data (To capture). The hub uses it for large outputs to one client: the user list
  at login (P2 §6) and bursts of searches.
- **`$Z` (`ZLine`).** An older form with its own escaping [NMDC] §4.5.16. The hub does not support it.
- Compression is specified fully, and advertised, in M2.

## 4. TLS hints in the lock

- [OBS-VH]: Verlihub with a TLS port writes `TLS_` or `TLSONLY_` into its lock after
  `EXTENDEDPROTOCOL_NMDC_` (P1 §4).
- [OBS-EI]: EiskaltDC++ only checks that the lock starts with `EXTENDEDPROTOCOL`; it does not act on
  such a hint.
- The hub adds no hint. If M1's captures show a target client switching to TLS because of one, the
  hub adds the same hint.

## To capture

- How DC++, AirDC++, EiskaltDC++ and ncdc each treat `nmdcs://`: TLS versions, SNI, certificate
  checking, and whether each reads `kp`.
- Whether each client follows a `$ForceMove` to an `nmdcs://` address.
- Whether any target client uses a TLS hint in the lock.
- The exact bytes of `$ZOn`, from a client that receives it.
