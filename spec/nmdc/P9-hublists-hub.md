# P9. Hub lists, the hub's side: registration and pingers

Sources: [NMDC] §4.4.39 (the hub-to-list key), §4.5.6 (`$BotINFO`), §4.5.7 (`$HubINFO`); [OBS-VH]
(Verlihub's registration); [OBS-GODCPP] (go-dcpp's registration client and server); [PWIAM];
[D-15].

A hub reaches public hub lists in two ways (roadmap D15). It pushes its entry to lists that run a
registration server, and it answers the pingers that lists send to check hubs. Some lists only
take a one-time submission on their website and then rely on their pinger, so for those the
pinger is the hub's whole part.

## 1. Push registration

- **Targets.** A list of `host[:port]` entries, port 2501 by default ([PWIAM] calls 2501 "the
  classic RegServer port"). The defaults are Verlihub's: `hublist.te-home.net` and
  `hublist.pwiam.com` [OBS-VH]. Each target is registered on a timer (every 15 minutes by
  default, with up to 2 minutes of random jitter), and when an operator asks.
- **The exchange,** one TCP connection per target, 10 s in all:
  1. The hub connects. The list speaks first: `$Lock <lock> Pk=<pk>|` [OBS-VH] [OBS-GODCPP]. The
     hub reads up to the first `|`, at most 512 bytes; more is an error.
  2. The hub answers with one write, then closes:

     ```
     $Key <key>|<name>|<address>|<description>|<users>|<share>|<list host:port>|
     ```

     | Field | Content |
     |---|---|
     | `<key>` | the key for `<lock>`, computed as in P1 §5 |
     | `<name>` | the hub's name |
     | `<address>` | the public address, `host`, or `host:port` when the port is not 411 |
     | `<description>` | the hub's description. With a minimum share set, it is prefixed with `[MINSHARE:<n>MB] `, where n is the minimum in MiB (a hub setting, on by default) [OBS-VH] [OBS-GODCPP] |
     | `<users>` | users online, decimal, without bots and pingers (§2) |
     | `<share>` | the users' total share in bytes, decimal |
     | `<list host:port>` | the target as the hub dialled it [OBS-VH]. go-dcpp's server reads the fields up to the share and ignores the rest [OBS-GODCPP] |

  3. `|` in the name and description is written `&#124;` [OBS-GODCPP]. The hub also writes `$` as
     `&#36;` (P1 §3). Text is in the hub's code page (P1 §2).
- **The key.** P1 §5's algorithm with its usual constant 5 is what Verlihub and go-dcpp send
  [OBS-VH] [OBS-GODCPP], and go-dcpp's server accepts it. [NMDC] §4.4.39 records an older variant
  for hub-to-list connections, where the constant 5 is replaced by
  `(p & 0xFF) + ((p >> 8) & 0xFF)` (8 bits), p being the hub's own local port on that connection.
  go-dcpp's server accepts that too [OBS-GODCPP]. The hub sends the standard key by default. Each
  target can be set to `key: legacy`.
- **Results.** Each attempt records the target, the time, and the outcome: `connect_failed`,
  `no_lock`, `sent`, or `error` with a reason. They are shown in the admin document (roadmap D15).
  A target that fails 10 times in a row backs off to hourly until it succeeds.
- **Several addresses.** The record carries one address. A hub with both plain NMDC and NMDCS
  sends its plain address. A TLS-only hub sends `nmdcs://host:port` (To capture: whether lists
  accept a scheme here; go-dcpp's server parses the field as an address [OBS-GODCPP]).

## 2. Pingers

A pinger is a client a hub list sends to check a hub. It logs in, reads the hub's name, topic,
users and share, then leaves.

- **How the hub tells.** A connection is a pinger if it lists `BotINFO` in `$Supports`, or sends
  `$BotINFO <description>|` [NMDC] §4.5.6. The description usually names the list.
- **The hub's answer.** The hub lists `HubINFO` in its own `$Supports` (P2 §2). It answers
  `$BotINFO` with:

  ```
  $HubINFO <name>$<address:port>$<description>$<max users>$<min share>$<min slots>$<max hubs>$<hub type>$<owner>|
  ```

  - `<hub type>` is `sublevels <version>`.
  - `<owner>` is the operator's contact (a hub setting, empty by default). [NMDC] §4.5.7 says lists
    use it as the hub owner's login on their site.
  - Sizes are in bytes.
  - With `<address>` set to 127.0.0.1, one list removes the hub [NMDC] §4.5.7. The hub never
    sends a loopback address.
- **Treatment.** A pinger:
  - is not announced to users. No `$MyINFO`, `$Hello` or `$Quit` is sent for it, and it is not in
    the user list others receive. It still receives the user list itself (P2 §6), since that is
    what it counts;
  - is left out of user counts: in pushes (§1), in `$HubINFO`, and in analytics (roadmap D14,
    counted separately as pinger visits);
  - is exempt from share, slot and hub-count rules, which a pinger fails by design;
  - may not chat, search or send connection requests. They are dropped;
  - is closed 60 s after it logs in.
  - Location policy (roadmap D12) and bans still apply. Operators can allowlist list addresses.
- **Visits.** Each pinger visit is logged with its address, nick, `$BotINFO` text and time, so the
  admin document shows which lists have seen the hub recently.

## 3. Lists without a push server

Some lists take a hub only through a web form, then check it with their pinger. [PWIAM]'s form asks
for the protocol (NMDC or ADC), TLS (NMDCS or ADCS), a bare host, and a port. For such lists the
hub needs only §2. The admin document shows them as `pinger` targets, with their last visit.

## To capture

- How the pingers of te-home.net, pwiam, dchublist.biz, dchublists.com and adchub.com log in: their
  `$Supports`, nick, whether they send `$BotINFO`, and whether they read `$HubINFO`.
- Which lists check `$Key` on push, and with which constant.
- Whether lists accept a scheme (`nmdcs://`) in the push record's address, and how a list learns a
  hub's NMDCS address and keyprint (the XML list's `KP` attribute, P10 §4).
- Which code page each list expects in the push record.
