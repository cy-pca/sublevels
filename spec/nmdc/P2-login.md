# P2. Hub login, the user list and `$MyINFO`

Sources: [NMDC] §4.4.6–4.4.8, §4.4.17–4.4.19, §4.4.25–4.4.32, §4.4.37, §4.5.1, §4.5.4–4.5.5,
§4.5.8–4.5.9, §4.6.1, §4.6.3, §4.6.11, §5.1; [D-7], [D-9].

## 1. States

Each connection moves through these states. In each one the hub accepts only the messages listed
and ignores the rest (P1 §1).

| State | The hub is waiting for | Accepted |
|---|---|---|
| `lock` | the client's answer to `$Lock` | `$Supports`, `$Key` |
| `nick` | the nick | `$ValidateNick`, `$Supports`, `$Key` |
| `pass` | a password | `$MyPass` |
| `info` | the first `$MyINFO` | `$Version`, `$GetNickList`, `$MyINFO` |
| `online` | — | everything for online users (P3, P4, P5, and this item §5) |

- **Login timeout:** a connection not `online` within 30 s of connecting is closed.
- **Order tolerance.** Clients send `$Supports`, `$Key` and `$ValidateNick` together. Some send
  `$Version`, `$GetNickList` and `$MyINFO` together right after `$Hello`, before the hub's reply to
  them. The hub processes messages in order and never needs a round trip inside these groups.

## 2. The sequence

```
hub     $Lock EXTENDEDPROTOCOL_sublevels_… Pk=sublevels0.1.0|$HubName <name>|
client  $Supports <features>|$Key <key>|$ValidateNick <nick>|
hub     $Supports <hub features>|
hub     $GetPass|                                  (only for a registered nick, §4)
client  $MyPass <password>|
hub     $Hello <nick>|
client  $Version 1,0091|$GetNickList|$MyINFO $ALL <nick> …|
hub     $LogedIn <nick>|                           (operators only)
hub     the user list (§6), $HubTopic, the message of the day (§7)
```

1. **On connect** the hub sends `$Lock` (P1 §4) and `$HubName <name>|`. Before that it checks the
   address against bans [D-9] and capacity; if either refuses, the hub sends one chat line from its
   bot saying why (P3 §4), then `$HubIsFull|` for capacity, and closes.
2. **`$Supports`** lists the client's features, separated by spaces. A trailing space before the
   `|` is allowed [NMDC] §4.5.9. The hub records the set; feature names are case-sensitive.
3. **`$Key`** is checked as P1 §5 says.
4. **`$ValidateNick <nick>|`**. The hub checks the nick (§3). On refusal it sends
   `$ValidateDenide <nick>|` and closes. On success it sends its own `$Supports`:

   `$Supports NoGetINFO NoHello UserIP2 BotList HubTopic|`

   Features are added to this line only when the hub implements them: `UserCommand` (P6),
   `ZPipe0` (P7), `HubINFO` (P9), `MCTo` (P3), `TLS` (P7).
5. **Registered nicks** go to `pass` (§4). Others go straight to step 6.
6. **`$Hello <nick>|`** accepts the nick. The connection is now in `info`.
7. **`$Version`** is accepted and ignored. Its argument is usually `1,0091` or `1.0091`.
8. **`$GetNickList|`** before the first `$MyINFO` is remembered and answered when the user goes
   online.
9. **The first valid `$MyINFO`** (§5) puts the user online. The hub then:
   - sends `$LogedIn <nick>|` if the user is an operator;
   - sends the user list (§6);
   - announces the user to everyone else (§6);
   - sends `$HubTopic <topic>|` to clients that listed `HubTopic`, and the message of the day (§7).

## 3. Nicks

- **Allowed:** 1 to 64 characters after decoding. Not allowed: space, `$`, `|`, `<`, `>`, control
  characters, and leading or trailing dots. Configurable lists can reserve more.
- **Unique:** no two online users, bots included, share a nick case-insensitively (P1 §2).
- **Reserved:** a registered nick (§4), a bot nick, and any nick on the hub's reserved list. A
  user who logs in with a registered nick must give its password.
- **Taken by a live connection:** if the nick belongs to a registered account, and the newcomer
  gives the right password, the hub closes the old connection (the user reconnecting) and lets the
  new one in. Otherwise the newcomer is refused with `$ValidateDenide`.

## 4. Passwords

- `$GetPass|` asks for the account's password. The client answers `$MyPass <password>|`. The
  password is sent in clear inside the connection, so a hub that accepts passwords SHOULD require
  TLS for them (P7). The setting `password_requires_tls` (default on) refuses `$MyPass` over a
  plain connection with a bot message pointing at the `nmdcs://` address.
- **A wrong password:** `$BadPass|`, then close. Repeated failures from one address or identity
  hash are rate-limited (5 per 10 minutes by default), and further attempts are refused before
  `$GetPass`.
- **What the password is.** Accounts are TX.WTF accounts, and the password is a hub password the
  user's TX.WTF account issues for NMDC use. The hub checks it with TX.WTF and never stores it in
  clear. The TX.WTF side is specified in M2; until then the hub runs with no registered accounts.
- **Accounts are optional** [D-9]. A user without an account logs in with any free, unreserved
  nick, and is identified by their client id: for an NMDC connection, the registry's fallback id
  from its addresses (roadmap D9). Nicks are also checked against the registry's usernames, so a
  native user's name is never available to an NMDC login.
- **`SaltPass`** (`$GetPass <salt>|`, answered with a hash of password and salt [NMDC] §4.6.16)
  avoids the clear password, but needs the Tiger hash. It is deferred to M2.

## 5. `$MyINFO`

`$MyINFO $ALL <nick> <description><tag>$ $<connection><flag>$<email>$<share>$|`

| Field | Meaning |
|---|---|
| `<nick>` | MUST equal the connection's nick; otherwise the hub drops the message, and during login closes |
| `<description>` | free text, possibly empty |
| `<tag>` | optional, at the end of the description: `<client V:version,M:mode,H:n/r/o,S:slots[,O:n]…>`, the angle brackets literal |
| `<connection>` | free text, a speed or link type |
| `<flag>` | one byte, bit flags (below) |
| `<email>` | free text, possibly empty |
| `<share>` | bytes shared, decimal |

- **Parsing.** The fields between `$ALL <nick> ` and the final `$|` are split on `$`. The literal
  ` $` (the space and empty field after the description) is part of the format. The last byte of
  the connection field is the flag. The hub parses the fields it uses:
  - the **mode** `M:` from the tag: `A` active, `P` passive, `5` SOCKS5; with IPv6, two characters
    (P5 §4);
  - **slots** `S:` and **hubs** `H:` for hub rules (later);
  - the **share** size;
  - the **flag** bits.
  A missing or unparsable tag is allowed. The user's mode is then "unknown", treated as active for
  forwarding (P4, P5).
- **The flag byte** is read as bits [NMDC] §4.4.25: 0x01 normal, 0x02 away, 0x04 server, 0x08
  fireball, 0x10 TLS, 0x20 TLS upload, 0x40 IPv4, 0x80 IPv6. The hub only reads it.
- **Kept verbatim.** The hub keeps the exact bytes of the user's latest `$MyINFO` and sends those
  bytes to others, so nothing a client wrote is changed by passing through the hub. The one
  exception is hub policy that rewrites a field (none in M1).
- **Updates.** An online user sends `$MyINFO` again on any change. The hub broadcasts it to all
  online users, unless it is byte-identical to the previous one. A user may update at most once
  every 10 s by default; within that, the hub keeps only the newest and sends it when the interval
  ends.
- **`$GetINFO <other> <me>|`** (from clients without `NoGetINFO`) is answered with `<other>`'s kept
  `$MyINFO`, to the asker only.

## 6. The user list and its changes

**For the newcomer, when they go online:**

- To a client with `NoHello`: the kept `$MyINFO` of every online user and bot, including the
  newcomer's own.
- To a client without `NoHello`: `$NickList <nick>$$<nick>$$…|` with every online user and bot.
  The client then asks for `$MyINFO`s with `$GetINFO`, unless it also has `NoGetINFO`, in which
  case the hub sends them all as for `NoHello`.
- Then `$OpList <nick>$$…$$|` with the online operators and the bots that act as operators. With
  no operators, `$OpList|`.
- To a client with `BotList`: `$BotList <nick>$$…$$|`.
- To a client with `UserIP2`: `$UserIP <nick> <ip>|` with the newcomer's own address as the hub sees
  it. Clients use it to learn their public address. Other users' addresses go only to operators
  (`$UserIP nick1 ip1$$nick2 ip2…|`), because NMDC users' addresses are personal data, and native
  users in relay mode never have one shown [D-6].

**For everyone else, when a user goes online:** the user's `$MyINFO`; to clients without
`NoHello`, first `$Hello <nick>|`; and if the user is an operator, the updated `$OpList`. Operators
with `UserIP2` also get `$UserIP <nick> <ip>|`.

**When a user leaves:** the hub closes or loses the connection, and sends `$Quit <nick>|` to all
online users. A client that wants to leave just closes; it does not send `$Quit` [NMDC] §4.4.37.

**List order and batching.** Large lists (thousands of users [D-7]) are written as a run of
messages without waiting. The hub SHOULD write them in pieces, so the newcomer's list does not
delay other connections' traffic.

## 7. Hub text after login

- **`$HubName <name>|`** and, for `HubTopic` clients, **`$HubTopic <topic>|`**. Without
  `HubTopic`, the topic is shown in the hub name as `<name> - <topic>`, as hubs commonly do.
- **The message of the day** is sent as chat lines from the hub bot (P3 §4), followed by a line
  that says how to read the rules (`+rules`) [D-9]. Both documents are kept by the hub and shown to
  native clients as documents.

## 8. Native users in the NMDC view

Users who connect natively over arisrvx appear to NMDC users as ordinary users. The hub writes a
`$MyINFO` for each, with the tag `<sublevels V:<version>,M:P,H:1/0/0,S:0>` until native sharing
exists (M3, M4). They take part in chat (P3) and search (P4) through the hub. Relay-mode
transfers are between native clients only [D-6].

## To capture

- The exact login bursts each target client sends: what arrives together, and in what order.
- Whether any target client still lacks `NoHello` or `NoGetINFO` today.
- What each client shows for `$HubIsFull`, `$ValidateDenide` and `$BadPass`, and whether it retries.
- Whether each client accepts `$Hello` for its own nick as the only acceptance signal, or also needs
  its own `$MyINFO` back (§6 sends both).
- How clients react to a `$UserIP` that lists only themselves.
