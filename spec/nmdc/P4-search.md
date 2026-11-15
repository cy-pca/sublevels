# P4. Search and results

Sources: [NMDC] §4.2.1, §4.4.23, §4.4.24, §4.6.2, §4.6.4, §4.6.18; [D-6], [D-7].

## 1. `$Search`

```
$Search <ip>:<port> <query>|        active: results come back by UDP to ip:port
$Search Hub:<nick> <query>|          passive: results come back through the hub
```

- **The query** is five fields separated by `?`:
  `<sizelimit>?<ismax>?<size>?<type>?<pattern>`.

  | Field | Values |
  |---|---|
  | `sizelimit` | `T` if the search limits file size, else `F` |
  | `ismax` | `T` if `size` is a maximum, `F` if a minimum or unused |
  | `size` | bytes, decimal; `0` when `sizelimit` is `F` |
  | `type` | `1` any, `2` audio, `3` compressed, `4` document, `5` executable, `6` picture, `7` video, `8` folder, `9` TTH |
  | `pattern` | search terms with each space written as `$`; for type 9, `TTH:` and a 39-character base32 hash |

- The hub checks the query's shape (five fields; `T`/`F`; decimal size; type 1 to 9; a type-9
  pattern of the right form). A malformed query is dropped and counted (P1 §6). The hub does not
  otherwise read the pattern.
- **Active searches.** The address MUST be the sender's own [NMDC] §4.2.1: otherwise any user could
  aim every client's UDP results at a third party. The hub replaces the address with the one it
  sees for the connection, and keeps the port. A port that is not 1 to 65535 drops the search. A
  rewritten address is counted, not reported to the user.
- **Passive searches.** `<nick>` MUST equal the connection's nick, or the search is dropped.
- **Who receives it.**
  - An active search goes to every online user except the sender.
  - A passive search goes only to users whose mode is active or unknown (P2 §5), except the
    sender. A passive user could answer, but could never connect to the searcher to download, so
    the hub saves the traffic.
  - Users that listed `ChatOnly` receive no searches, and a `$Search` from them closes the
    connection [NMDC] §4.6.2.
  - Bots receive no searches.
- **Limits** (hub settings): one search every 10 s per user, with a burst of 2; the hub drops
  searches over the limit and tells the user once a minute. Operators are exempt.
- **Fan-out at scale.** At 6000+ users [D-7] each search is thousands of writes. The hub encodes a
  search once and writes the same bytes to every receiver. Writes to one connection SHOULD be
  gathered per event-loop turn, not written one message at a time.

## 2. `$SR`

```
$SR <source> <result> <free>/<total><0x05><hubname> (<hubip>[:<port>])[<0x05><target>]|
```

`<result>` is `<path><0x05><size>` for a file, or `<path>` for a directory. For a file with a
known TTH, `<hubname>` is replaced by `TTH:<hash>`. The result's path uses `\` as its separator.

- **Results to an active search** go by UDP, client to client. The hub never sees them.
- **Results to a passive search** come to the hub over the responder's connection, ending in
  `<0x05><target>`. The hub:
  - checks that `<source>` equals the connection's nick, or drops the result;
  - looks up `<target>`; if they are not online, drops the result;
  - removes the final `<0x05><target>` and sends the rest, otherwise unchanged, to the target.
- An `$SR` through the hub with no target is dropped.
- **Limits.** Clients are asked to send at most 5 results to a passive searcher [NMDC] §4.4.24.
  The hub forwards at most 10 results from one responder to one target within 30 s of the
  target's last search, and drops the rest.

## 3. Native users

- A native user's search is sent to NMDC users as a passive search from that user's nick, and the
  passive results come back through the hub to the native user, decoded (P1 §2).
- Native users who share (M3, M4) answer searches through the service. A result to an NMDC user
  is written as an `$SR` from the native user's nick.
- Relay-mode downloads between native users [D-6] are found by the same searches.

## To capture

- Whether each target client sends TTH searches (type 9) to hubs that did not list any feature,
  and what pattern it sends.
- Whether any target client relies on getting its own search back.
- How each client behaves when searches are dropped silently by the rate limit.
- Whether any client sends `$Search` with a host name instead of an IP address.
