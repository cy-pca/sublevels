# P1. Framing, text, escaping, `$Lock` and `$Key`

Sources: [NMDC] §4.1, §4.1.3, §4.4.38, §4.4.39, §4.5.9; [D-8]; [OBS-VH] (lock layout).

## 1. Messages on the stream

- A hub connection is one TCP stream (or TLS over TCP, see P7). Each side writes a sequence of
  **messages**. A message is every byte up to and including the next `|` (0x7C).
- A message never contains `|` except as its terminator. A message may contain any other byte
  except 0x00.
- The hub MUST buffer a partial message until its `|` arrives. A message longer than the hub's
  limit (section 6) MUST close the connection.
- There are three kinds of message:
  - **Command:** starts with `$`. The command name runs from the `$` to the first space, or to the
    `|` if there is no space. The rest, up to the `|`, is the argument text. Names are
    case-sensitive (`$MyINFO`, not `$MyInfo`).
  - **Chat line:** starts with `<`. See P3.
  - **Empty:** just `|`. Clients send it as a keepalive. The hub MUST accept it and do nothing.
    The hub MAY send it as its own keepalive.
- Any other first byte is malformed. The hub drops the message and counts it; see section 6.
- Some command names are misspelled on purpose, and the misspelling is the protocol:
  `$LogedIn`, `$ValidateDenide`, `$Canceled`.
- An unknown command from an online user MUST be ignored, not answered and not fatal. Clients
  send extensions the hub does not know. During login (P2), the hub ignores commands that are
  not valid in the current state.
- Arguments are separated by single spaces, and many commands also use `$` as a separator inside
  their argument text. Each command's item gives its own grammar.

## 2. The code page

- NMDC has no code-page negotiation; text is in whatever 8-bit code page each side uses [NMDC]
  §4.1. The original software used Windows-1252, and that is the hub's default [D-8]. A hub's
  code page is a per-hub setting.
- The hub keeps all text as Unicode internally. It decodes bytes from NMDC connections, and
  encodes bytes to them, with the hub's code page, at the connection edge only.
- **Decoding.** Windows-1252 leaves five bytes unassigned: 0x81, 0x8D, 0x8F, 0x90 and 0x9D. The
  hub decodes each one to the Unicode code point of the same number (U+0081 and so on), as web
  browsers do, so that decoding never fails and re-encoding gives back the same byte.
- **Encoding.** A character the code page cannot represent (text from a native client, an emoji,
  most non-Latin scripts in Windows-1252) is written as `?`. The hub MUST NOT write a numeric
  character reference for it (`&#…;`), because clients treat only the escapes in section 3 as
  escapes.
- **What is opaque.** The hub forwards some messages unchanged (P2 `$MyINFO`, P5, P4 `$SR`).
  These stay as the bytes the client sent; the hub decodes them only to read the fields it checks.
- **Nicks** are compared as decoded Unicode text, case-insensitively, for uniqueness and
  reservation (P2 §3). On the wire they are written as the user sent them.

## 3. Escaping

Text that must carry `$` or `|` uses one of two escape schemes [NMDC] §4.1.3.

- **The DCN scheme:** `/%DCNnnn%/`, with nnn the byte's three-digit decimal value. It is used for
  exactly these bytes: 0 (NUL), 5, 36 (`$`), 96 (`` ` ``), 124 (`|`) and 126 (`~`).
  - [NMDC] lists `/%DCN000%/` as 0x0a. That is an error: its own `$Key` section, and the list
    above, give the value 0. The byte is NUL.
  - It is used in `$Key` (section 5). Some clients also show it in chat.
- **The HTML scheme:** `&#36;` for `$`, `&#124;` for `|` and `&amp;` for `&`. DC++ and the clients
  derived from it use it for display text (chat, private messages, descriptions).
- **What the hub sends.** In text the hub writes itself, or translates from a native user, it
  writes `$` as `&#36;`, `|` as `&#124;` and `&` as `&amp;`.
- **What the hub reads.** When the hub decodes display text for a native client or for its own
  use, it replaces `&#36;`, `&#124;` and `&amp;` with the characters, and `/%DCN036%/` and
  `/%DCN124%/` likewise. It does not unescape anything else. Text it forwards to NMDC clients is
  not unescaped (section 2).

## 4. `$Lock`

`$Lock <lock> Pk=<pk>|`

- The hub sends `$Lock` as its first message on every connection (P2 §2), after TLS if TLS is in
  use (P7). The client speaks only after it.
- The hub implements extensions, so its lock MUST begin with `EXTENDEDPROTOCOL` [NMDC] §4.5.9.
- After that prefix, the hub's lock is `_sublevels_` followed by 24 random characters from
  `A-Z a-z 0-9`. A lock MUST NOT contain a space, `$` or `|`, and [NMDC] records the original hub's
  locks as using bytes 37 to 122 only; the hub's alphabet stays inside both rules.
- The lock is chosen afresh for each connection. The hub keeps it, to check the `$Key`.
- `Pk=` carries the hub's name and version, for example `Pk=sublevels0.1.0`. Nothing interprets
  it [NMDC] §4.4.38.
- Other hubs put a TLS hint in the lock. [OBS-VH]: when it has a TLS port, Verlihub's lock is
  `EXTENDEDPROTOCOL_NMDC_`, then `TLS_` (or `TLSONLY_` when it refuses plain connections), then
  random digits. The hub does not add such a hint unless an M1 capture shows a client acting on it
  (P7 §4).

## 5. `$Key`

`$Key <key>|`

- The client answers the hub's `$Lock` with `$Key`, computed from the lock bytes (the part before
  ` Pk=`). The algorithm, from [NMDC] §4.4.39:
  1. The key has as many bytes as the lock. For every position i from 1 to the end, key byte i is
     lock byte i XOR lock byte i−1.
  2. Key byte 0 is lock byte 0 XOR the last lock byte XOR the second-to-last lock byte XOR 5.
  3. Every key byte is nibble-swapped: its high and low four bits trade places.
  4. Each key byte with the value 0, 5, 36, 96, 124 or 126 is written in the DCN scheme (section
     3). Every other byte is written as itself.
- All arithmetic is on unsigned 8-bit values. The result is binary, not text: the hub MUST NOT
  decode it with the code page.
- [NMDC] says hubs need not check the key. The hub computes the expected key, undoes the DCN
  escapes in what it received, and compares them.
  - By default a mismatch is logged and counted, and the login continues.
  - A hub setting turns a mismatch into a disconnect. The default changes if M1's captures show
    every target client computes the key correctly.
- Hub lists and hub-to-hub links use a variant of the constant in step 2. The hub implements it
  only when it needs it (P9).

## 6. Limits and malformed input

The hub enforces these limits per connection. Each one is a hub setting; the defaults are below.

| Limit | Default | When exceeded |
|---|---|---|
| Message length, during login | 4 KiB | close |
| Message length, online | 64 KiB | close |
| Malformed messages (section 1) | 20 per minute | close |
| Bytes received, online | 256 KiB per 10 s | close |
| Unsent output queued for a slow reader | 4 MiB | close |

- "Close" means: the hub sends nothing further, closes the connection, and logs the reason with
  the connection's identity hash [D-9].
- Rate limits on particular commands (chat, search, connection requests, `$MyINFO`) belong to
  their own items.

## To capture

- Whether DC++, EiskaltDC++, AirDC++ and ncdc each send `|` keepalives, and how often.
- Which of these clients use the DCN scheme in chat as well as the HTML scheme.
- Whether every target client's `$Key` matches the algorithm in section 5 (to decide the strict
  default).
- How each client decodes the five unassigned Windows-1252 bytes.
