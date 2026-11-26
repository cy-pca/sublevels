# P10. The hub list's side: registrations, the pinger, and the list clients load

Sources: [NMDC] §4.4.39, §4.5.6, §4.5.7; [OBS-GODCPP] (go-dcpp's registration server and its XML
list types); [OBS-EI] (how EiskaltDC++ loads a list); [DCNF] (DCNF's list generator); [PWIAM];
[D-10], [D-16].

In hublist mode (roadmap D16), a sublevels instance is a hub list. It takes registrations from any
hub, checks each hub with its own pinger, and serves the list over HTTP in the format DC clients
load into their public-hubs window.

## 1. The registration server

- **Listener:** TCP port 2501 by default. Each connection gets 10 s and at most 2 KiB.
- **The exchange** is P9 §1 from the other end:
  1. The list sends `$Lock <lock> Pk=sublevels-hublist <version>|`. The lock is random for each
     connection, under P1 §4's rules. go-dcpp's server sends a fixed lock [OBS-GODCPP]; ours does
     not.
  2. It reads `$Key <key>|`, then the fields up to the share, each ending in `|`: name, address,
     description, users and share. A trailing list-host field is read if present and ignored.
  3. **The key** is accepted if it matches P1 §5 with the constant 5, or with
     `(p & 0xFF) + ((p >> 8) & 0xFF)`, where p is the connecting hub's source port [NMDC] §4.4.39
     [OBS-GODCPP]. A wrong key ends the connection and is counted. A setting turns the check off.
  4. **Fields:** in the name and description, `&#124;`, `&#36;` and `&amp;` are unescaped. A
     description starting `[MINSHARE:<n>MB] ` gives the minimum share (n MiB), and the prefix is
     removed [OBS-GODCPP]. The address may be `host`, `host:port` or a URI (`dchub://`,
     `nmdcs://`). Without a scheme it is `dchub://`, and without a port 411, as [DCNF] normalises.
     Users and share must be decimal.
- **Then:** the entry is created or updated, keyed by its normalised address, with the source
  address that registered it. A new entry is `pending` until the pinger's first successful visit.
  Registrations are limited to 4 an hour per source address and per hub address.

## 2. The pinger

- **What it does.** It logs in to each listed hub as an NMDC client (P2), with:
  - `$Supports NoGetINFO NoHello BotINFO`;
  - the nick `sublevels-pinger` (a setting), and a `$MyINFO` with no share;
  - `$BotINFO <list name> <list URL>|`, sent once it is in.
- **What it reads:**
  - `$Lock`'s `Pk` (the software, when `$HubINFO` gives none);
  - `$HubName`, `$HubTopic`, and `$HubINFO` (max users, min share, min slots, max hubs, hub type,
    owner) [NMDC] §4.5.7;
  - the user list: the count and the total share from the `$MyINFO`s, leaving out nicks in
    `$BotList`. The operators come from `$OpList`.
- **TLS.** For a hub with an NMDCS address it also logs in over TLS, and records the certificate's
  keyprint (P7).
- **Then it disconnects.** It never chats or searches.
- **Schedule.** Every hub is pinged every 30 minutes, with jitter. At most 8 pings run at once
  overall, and one per hub.
  - `$HubIsFull` counts as reachable but full. go-dcpp's list types record that as an error code
    (`ErrCode`), and DCNF drops hubs whose code is 226 [DCNF].
  - Errors back off: 1, 2, then 4 hours.
- **Reliability** is the share of successful pings over the last 7 days, shown as a percentage.

## 3. Entries and moderation

- **States:** `pending`, `listed`, `hidden` (by an operator), `banned` (registrations refused) and
  `dropped` (unreachable for 48 hours; re-listed by its next successful ping).
- **Included automatically:** the cluster's own hubs (roadmap D10), listed without a push.
- **Mirrors.** A setting lists other public lists to merge. Their XML is fetched hourly, entries are
  merged by normalised address with the source recorded, and mirrored entries are pinged like any
  other. Candidates, from the lists' own pages [PWIAM] [DCNF]:
  - te-home.net (`?do=hublist&get=hublist.xml.bz2`);
  - hublist.pwiam.com (`/hublist.xml.bz2`);
  - dchublist.biz;
  - dchublists.com;
  - hublist.eu;
  - adchub.com (`/hublist.xml.bz2?proto=NMDC`);
  - dchubs.ro.
- **Operators** manage entries in the admin document and API.

## 4. The list clients load

- **URLs.** `GET /hublist.xml` and `GET /hublist.xml.bz2`, plus `/hublist-<cc>.xml.bz2` for each
  country with listed hubs (lower-case ISO codes, as [PWIAM] names them). Clients choose
  decompression by the URL's `.bz2` suffix [OBS-EI], so the compressed list must be served under a
  `.bz2` name. The list is regenerated every 5 minutes and served with `ETag` and `Last-Modified`.
- **Format.** XML 1.0, UTF-8, laid out as [DCNF]'s generator writes it:

  ```xml
  <?xml version="1.0" encoding="UTF-8"?>
  <Hublist Name="sublevels" Address="https://hublist.sublevels.net/" Generated="2026-11-26 12:00:00">
    <Hubs>
      <Columns>
        <Column Name="Address" Type="string"/>
        <Column Name="Name" Type="string"/>
        <Column Name="Users" Type="int"/>
        <Column Name="Shared" Type="bytes"/>
        <!-- one Column per attribute below, in the same order -->
      </Columns>
      <Hub Address="dchub://hub.example.org:411" Name="Example" Description="…" Users="120"
           Country="US" Shared="5497558138880" Minshare="0" Minslots="0" Maxhubs="0"
           Maxusers="6000" Reliability="99.5%" Rating="" Encoding="CP1252"
           Software="sublevels 0.2.0" Website="" Email="" Operators="3" Bots="1"
           Status="Online" Failover="" KP="SHA256/…"/>
    </Hubs>
  </Hublist>
  ```

- **Attributes.** EiskaltDC++ reads Name, Address, Description, Users, Country, Shared, Minshare,
  Minslots, Maxhubs, Maxusers, Reliability and Rating [OBS-EI]. go-dcpp's list type also has
  Encoding, Software, Website, Email, Operators, Bots, Status, Failover, Network, Icon, Logo,
  ErrCode and `KP` (keyprints) [OBS-GODCPP]. The hub writes all of them that it knows, in [DCNF]'s
  column order, with `KP` last.
- **Details:**
  - `Hub` elements sit under `Hubs`, but clients find them at any depth [OBS-EI].
  - Sizes are decimal bytes. go-dcpp also accepts a float [OBS-GODCPP], but the hub writes
    integers.
  - Attribute text is escaped as XML requires.
  - `Address` is `dchub://` or `nmdcs://`, with the port. `Encoding` is the hub's code page, which
    [DCNF] uses to keep or drop NMDC hubs.
- **The legacy `.config` text list** is not served. Current clients load only the XML list
  [OBS-EI], and no source gives the `.config` line format. [PWIAM] offers `.config` "for hub
  software and registration workflows". It is To capture, and is added only if a target needs it.
- **Also:** `+hublist` in hubs, and a document for native clients (roadmap D16), both reading the
  same entries.

## To capture

- The `.config` format, and whether any target client or hub software still loads it.
- How DC++, AirDC++ and ncdc load a list: the attributes each shows, and whether each reads `KP`
  and `Encoding`.
- Whether any list's pinger expects a reply to `$BotINFO` other than `$HubINFO`.
- The `ErrCode` values in use besides 226.
