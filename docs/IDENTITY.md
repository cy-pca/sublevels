# One identity and one session across the sublevels app: design

Status: proposed, 2026-11-26. Docs only; nothing here is built. Section 8 lists the decisions for
Joe. It works out the roadmap's "Identity is TX.WTF's" (Architecture, D9, M2) for all three parts
of the app, and replaces nothing there.

## 1. What is asked

Joe, 2026-11-26: one component in the sublevels client that binds three views under one session
and one identity:

- **TX.WTF**, through nicotina's components (cy-pca/nicotina#46, `docs/COMPONENTS.md`);
- **the conference rooms**, on the arisrvx relay, through cyprty's components;
- **the NMDC hub**, the sublevels service on the same arisrvx host (CLIENT_UI.md).

TX.WTF runs on arisrvx, so a TX.WTF identity should be the arisrv identity, or TX.WTF should
implement arisrv identities. **Guests** too, in all three: cyprty takes guests, an NMDC hub lets
anyone in without a login, and a TX.WTF guest can browse public posts and media but not post.

nicotina never depends on sublevels (CLIENT_UI.md CU-D3). Its side is `session.cmd` `adopt` and
`guest` (COMPONENTS.md 3.7, 3.8), and nothing else.

## 2. What exists

Checked 2026-11-26 against txwtf master (f95b665, 0.6.0), libcvc master and transfix/libcvc#1565,
sublevels main (b5dd50d) and nicotina#46.

| Piece | Where | What it gives today |
|---|---|---|
| TX.WTF session | txwtf `core/__init__.py` `sign_jwt`, `authorized_session_launch`, `authorized_session_verify` | An HS256 JWT `{user_id, expires, uuid}` with an `AuthorizedSession` row. Verifying means decoding it and checking the row: known, same secret, unexpired, user enabled, not deactivated. `expires` is not the JWT `exp` claim, so a bare `jwt.decode` does not check expiry; only the row does. The token is base64url and dots, so it fits the relay's bearer characters. |
| Many sessions at once | `authorized_sessions_live(session, uuids, secret)` | Which of a set of sessions are still good, 500 per query. Built for re-checking the sessions behind open WebSockets. |
| `GET /user/` | txwtf `api/__init__.py` | The caller's own profile (id, username, groups) under `JWTBearer`. 401 for a dead session. An external check of a session token needs nothing else. |
| OIDC provider | txwtf `api/oidc.py`, `core/oidc_flow.py` | Discovery, authorization code with PKCE, the device grant, refresh, revoke, userinfo. Access tokens are opaque and expire after an hour. `/oauth/introspect` answers only the client that owns the token. |
| `POST /user/login/oidc` | txwtf | Trades a first-party client's access token with the `txwtf.api` scope for an ordinary session (an hour by default, up to the refresh token's 30 days). The session is named after the app on the sessions screen. |
| Hub passwords | none | txwtf has no per-app or per-service passwords. |
| Authenticate hook | libcvc ARIADNE.md §9.8.4, `@host.authenticate` | The program checks each connection's bearer before admission and returns a principal `{id, name, roles}`, or None to refuse. The principal keys bound writes, `ctx.principal`, role gates and call limits. `host.disconnect(principal_id)` closes its connections. |
| Joining with a credential | ARIADNE.md §9.8.3; `--relay-credential` (host_cli.h; ctest `arisrvx_auth_credential`) | `relay: token: credential:NAME` or `oidc:NAME`. The host reads the bearer from its credential store at every dial, bound to the relay's origin by a policy line. |
| Device id and key | ARIADNE_CONFERENCE_ROADMAP §5f | A per-relay device id and an Ed25519 key derived per relay, pinned on first use. It proves the device, not the person, and two relays can't match one device. |
| Client registry | ARISRV_IDENTITY ID1-ID3, ID5 (#1565) | A client id per device (or per address with no device id), a server-owned username (`guest-<hex>` when none is asked for), kinds and capabilities, the guest password, rcon, admin calls. NMDC connections join it through `open_engine` and `name_engine`. The registry knows nothing about principals. |
| sublevels and TX.WTF | `server/src/sublevels/txwtf_service.py` | TX.WTF in the same process (the default), at an external URL, or off. `sublevels.server.txwtf` publishes `{mode, url}`, and an embedded instance's URL is its private listen address. No authenticate hook yet. |
| The hub's accounts | `nmdc/accounts.py`, hub `Accounts` protocol | A JSON file of nicks, scrypt hashes and roles, with `check(nick, password)` called at `$MyPass`. TLS required for passwords (P2 §4). |
| The bridge | `bridge.py` | NMDC users are bridged users of the room (`via="nmdc"`). Room users show on the hub as `[room]name`. |
| nicotina | #46 3.2, 3.7, 3.8 | One `session` subtree per site. The token stays in the host under `session.cred`. `adopt` takes a session the host already holds; `guest` browses public routes. |
| cyprty | cy-pca/cyprty README | Guests only: a name from `?name=` or `CVC_ARIADNE_NAME`, otherwise `guest-xxxxx`; a room token for a private room. |

So nothing links a TX.WTF account to anything on the relay or the hub today. The pieces to do it
are there: the hook, credentials on the join, a session check in process and over HTTP.

## 3. The design

### 3.1 The root is the TX.WTF account

There is one account, the TX.WTF account of the instance on the same arisrvx host. It signs in to
all three parts. Everything else is evidence about it or around it:

| What | Says | Kept by |
|---|---|---|
| The TX.WTF account (`user.id`, `username`) | who the person is | TX.WTF |
| The TX.WTF session (`uuid`) | that this sign-in is live | TX.WTF |
| The device key | that this connection comes from the same device as before | the relay (pins) |
| The client id | which device (or address) the connection is, for the log and for bans | the registry |
| A hub password | that this NMDC login belongs to the account | TX.WTF, hashed |

On the relay a signed-in connection's principal is:

```python
{"id": "txwtf:42:<session uuid>",      # one principal a session (3.6 says why)
 "account": 42, "name": "joe",          # the TX.WTF user id and username
 "roles": ["op"],                       # from TX.WTF groups (3.4)
 "session": "<session uuid>"}
```

A guest's principal is `guest:<client id>`, the same each time the same machine comes back (3.8).
sublevels keeps no identity store (D9 holds): the
account is TX.WTF's, the device proof is the relay's, and the hub's own file of accounts stays
only for a hub run with `--txwtf off`.

### 3.2 The relay: the authenticate hook

The sublevels program sets `@host.authenticate`. The client sends its TX.WTF session token as the
bearer (3.5). The hook sorts the bearer by its shape:

| Bearer | Is | The hook |
|---|---|---|
| a JWT (two dots) | a TX.WTF session | verifies it (below); a principal, or None |
| 64 hex digits | the guest password's hash (ARISRV_IDENTITY 2.4) | `host.guest_password_ok(cred)` (ID4c); a guest, or None |
| empty | no credential | a guest if the relay takes open guests, else None |

**Verifying a session.**

- **Embedded TX.WTF** (the default): in process, on the program's loop. It decodes with TX.WTF's
  secret, then checks the row with `authorized_session_verify` and reads the user and groups.
  There's no network call. It needs a public helper in txwtf (T1), because today the engine and
  secret are private attributes of the app (`app._engine`, `app._jwt_secret`).
- **External TX.WTF** (a URL): `GET /user/` with the token as the bearer. 200 gives the account,
  401 refuses it. Answers are cached for 60 s by token hash, so a reconnect storm doesn't become a
  storm at TX.WTF. A session token goes only to a relay run by the TX.WTF's own operator (section
  5).

**Guests.** A guest verdict admits the connection, and the relay makes its principal
`guest:<client id>` once the Hello gives the device id (3.8). Call limits, bans and throttles then
follow the guest across reconnects. A pid would be new on every connection. One principal shared by
all guests would put them all under one set of call limits. This needs a libcvc change (L1),
because the hook runs before the Hello and can't see the device id.

**Names.** A signed-in connection's registry username is the account's username. The relay
writes it, and the client can't rename it (`server.set` refuses). A guest is `guest-<hex>`, and
can't take a name either, so a guest is always visibly a guest. Both need the verdict to set the
username (L2).

**The device key** keeps its job: proving the device. The record says whether the device proved
its key. A ban can name the device as well as the account. A signed-in connection whose device
didn't prove its key is still admitted. The admin view shows it as unverified.

**Re-checking.** A session can end while its connection is open: signed out elsewhere, revoked
from TX.WTF's sessions screen, expired, or the account disabled. Every 60 s the program checks the
live principals' sessions in one batch (`authorized_sessions_live` in process; for an external
TX.WTF, `GET /user/` per session until T1 offers a batch). It calls `host.disconnect` for each dead
one. The client dials again with whatever credential it now holds, or as a guest.

**What this unblocks.** The hook is what `host.read_scope` needs (ARIADNE.md §9.8.5). So operator-only
analytics series (D14) and members-only rooms follow from it.

### 3.3 The NMDC hub

**Native users** (the sublevels client) reach the hub over the relay through room-calls. They are
the relay's principals from 3.2: same account, same name, no second login.

**Vanilla NMDC clients** (DC++, AirDC++, ncdc) can only send a nick and a password, so:

- **A hub password, issued by the TX.WTF account.** In TX.WTF's settings (and in the sublevels
  client's Settings, through TX.WTF's API) the user makes a password for "NMDC hubs". TX.WTF
  generates it, shows it once, and stores only a hash. The user can name it ("laptop DC++"),
  see when it was last used, and revoke it. This is the general "app password" feature (T2).
  It is never the account's own password. A client that keeps the password in its favorites file
  then holds something that opens only the hub.
- **The nick is the TX.WTF username.** At `$ValidateNick` the hub asks whether the nick is an
  account's name (3.7). If it is, the login is registered: `$GetPass`, then `$MyPass` over TLS
  (P2 §4, `password_requires_tls`). The hub checks the password with TX.WTF:
  - embedded: in process (T2's `check_app_password(username, password, scope="nmdc")`);
  - external: `POST` to a TX.WTF route authenticated as the hub's own confidential client, which
    txwtf's client authentication already supports (as `/oauth/introspect` uses it).

  The hub never stores the password. Wrong passwords keep P2's rate limit (5 per 10 minutes by
  address and identity). TX.WTF limits its check route on its side as well.
- **Roles** come from TX.WTF groups (3.4) through the hub's `Accounts` protocol: a new
  `TxwtfAccounts` beside `FileAccounts`. The protocol's `check` becomes asynchronous, because the
  hub must wait for TX.WTF's answer before it accepts the login.
- **In the registry,** the connection is `open_engine`'d at accept as today (ID5b). It is named at
  login with the nick, and with the account when it logged in by password (L3).
- **Revoking a hub password** closes that account's NMDC logins that used it. Disabling the account
  closes them all. TX.WTF tells the hub in process; with an external TX.WTF, the hub checks again
  every few minutes.

**Guests on the hub** are NMDC's unregistered users: any free nick that isn't reserved, no
password. They are identified by the registry's client id (the address fallback for NMDC, D9).

### 3.4 Roles

TX.WTF groups map to hub and relay roles in the sublevels config. For example `sublevels-ops` →
`op`, and TX.WTF site admins → `admin`. The role is read at sign-in and at every re-check, so
taking someone out of the group takes effect within a minute. rcon (ID3) stays as the operator's
way in when TX.WTF is down. A guest never has a role.

### 3.5 Signing in once, in the client

The sublevels client's shell holds one identity state, and mounts one nicotina `session` for the
host's TX.WTF:

```
identity.state     guest | signing-in | in | out
identity.site      the TX.WTF that goes with the relay ("https://social.sublevels.net")
identity.cred      the credential holding the TX.WTF session ("txwtf.social.sublevels.net")
identity.account   id, username (from nicotina's session.me once it is "in")
identity.relay     joined | dialing | refused, and as whom (account or guest-xxxx)
identity.hub       the hub's view of us: nick, role
```

1. **Which TX.WTF.** The relay publishes where its TX.WTF answers. Today
   `sublevels.server.txwtf.url` is an embedded instance's private listen address
   (`http://127.0.0.1:8086`), so the service gets a public URL setting (S1). The client reads it
   at its first, guest, join, or takes it from its launcher config.
2. **Sign in to TX.WTF**, by the best route the site and host offer:
   - **OIDC** (preferred): `(oidc-sign-in ...)` with the site as issuer and the sublevels app's
     first-party public client (ID-D8). The flow is loopback in a window, the device code over
     SSH, or redirect in a browser page. Then `POST /user/login/oidc` with `capture_auth` under
     `identity.cred` gives a TX.WTF session. The OIDC refresh token lets the shell trade again
     before the session expires, so the user doesn't sign in again.
   - **Password**, where the site has no OIDC client or device grant yet (COMPONENTS.md 3.5:
     tx.wtf and its peers run 0.4.0): the shell mounts nicotina's `signin` component, which
     captures the session under the same name. When that session expires, the user signs in again,
     as in nicotina.
3. **Hand it to nicotina:** `session.cmd adopt` on the mounted `session` (nicotina#46 3.8). nicotina
   reads `/user/` and goes to `in`. When the password route was used, `signin` has already done
   this, and nicotina is `in`.
4. **Join the relay as the account:** the relay link names the same credential (`relay: token:
   credential:txwtf.<host>`, or `--relay-credential`). The launcher's policy binds that credential
   to the relay's origin as well as TX.WTF's (`credential txwtf.social.sublevels.net
   https://social.sublevels.net https://paladin.sublevels.net:7700`, the origin form ARIADNE.md
   §9.8.3 gives for `grpcs://`). The host reads it at every dial,
   so a renewed session is picked up at the next reconnect. To move from guest to account, the
   client leaves the relay and dials again.
5. **The hub and the rooms** need nothing more. cyprty's conference views and the hub's native
   views use the same relay connection, so they show the same principal and name.

One relay connection serves both the rooms and the hub, because they are one arisrvx host. cyprty's
components must therefore use the shell's relay link instead of opening their own. arigl's
conference mode (`CVC_ARIADNE_RELAY`) must also take its bearer from a credential, not only from
`CVC_ARIADNE_ROOM_TOKEN`. This hasn't been checked; if it doesn't, it is L4.

### 3.6 Signing out

**Sign out** in the shell signs out of all three, in this order:

1. Leave the relay. This closes the room and the native hub session at once.
2. `session.cmd signout` to nicotina: `POST /user/logout`, and the credential is dropped.
3. `(oidc-sign-out ...)` when signed in by OIDC: the refresh token is revoked.
4. If the user chose **Continue as a guest**: nicotina `guest`, and the relay is dialed again with
   no bearer, as `guest-<hex>`. Otherwise the app stays out, with the sign-in screen.

Sessions ended elsewhere arrive through 3.2's re-check: "sign out everywhere" on TX.WTF, a revoked
session, a disabled account. The relay closes those connections within a minute. The client sees
its join refused, and falls back to guest or to the sign-in screen as the user set.

The principal is one per session, not one per account, so `host.disconnect` closes only the device
that signed out. The account's other devices stay. The program keeps an account → principals map
for account-wide actions (a ban, a disabled account).

Vanilla NMDC logins aren't sessions. They end when their hub password is revoked or the account is
disabled (3.3).

### 3.7 Names

- **An account's name is the account's everywhere.** TX.WTF usernames are reserved on the relay
  and on the hub for everyone else, whether or not that account is online. A guest can never pass
  as `joe`. On the hub that is one lookup at `$ValidateNick`, cached for a minute both ways: in
  process, or for an external TX.WTF through T1's service route (`GET /user/by-username/{name}`
  needs a user's session, so the hub can't use it).
- **Rules differ.** TX.WTF takes any username up to 256 characters, case-sensitive (txwtf has a
  TODO for a pattern). A registry name is at most 32 bytes, an NMDC nick at most 64 without
  spaces, `$`, `|`, `<` or `>`, and both compare without case. txwtf adds a username rule for new
  accounts (T3): 1-32 bytes, nick-safe. An existing account outside the rule gets a derived name
  on the relay and the hub (unsafe characters to `_`, cut to fit), and the derived name is what's
  reserved. Two accounts that differ only in case are an open question for txwtf.
- **Room users on the hub** stay `[room]name` (P3 §5), with the account's name. **NMDC users in
  the room** stay bridged users. Their detail says whether the user logged in to an account
  (then the TX.WTF profile is one click away, CLIENT_UI §4.2) or is a guest.

### 3.8 Guests are the same guest each time

Joe, 2026-11-26: guests get semi-stable ids, so a guest who comes back from the same machine (its
hardware, OS user and addresses, whatever makes the hash) gets the same id. Repeat guests can then
be tracked, banned and throttled across all three parts.

**The id** is the registry's client id (ARISRV_IDENTITY 2.1), computed once for the host and used
by all three parts:

| Who | Their guest id is made from | Stable across |
|---|---|---|
| a native client (the sublevels app, cyprty in `arigl`, `ari`) | the device id: OS machine id, host name and OS user, scoped to the relay | reconnects, restarts of the client, new networks (addresses aren't hashed in, ARISRV D1) |
| a browser page | the random device id it keeps in `localStorage` | reconnects, until site data is cleared |
| a vanilla NMDC client, or a page with no storage | the fallback: its public and local addresses | reconnects from the same addresses |
| a TX.WTF guest over HTTP (T5) | the same fallback, from the request's address, with the local address empty (as a browser sends it) | requests from the same address |

**What has to change for it to hold:**

- **One salt that survives restarts.** Today the relay's salt is random in each process, so every
  id changes when arisrvx restarts. ARISRV_IDENTITY plans `--identity-salt-file`, which isn't
  built yet (L5). The sublevels host keeps the file with its data, and the hub and TX.WTF hash
  with the same salt. The device-key pins already persist (`--device-pins`), and the host must
  use them too.
- **A guest's principal is its id** (3.2, L1), and its name follows from it: `guest-<first 4 hex>`,
  which the registry already gives back to a returning id.
- **No borrowing another guest's id.** A device id is an identifier, not a credential. A client
  that sends a device id pinned to a different key is refused the proof today, but keeps the id.
  For a guest it must not: it gets the address fallback instead. Otherwise anyone could take, or
  get banned in place of, another guest's record (L5).
- **One guest list for the host.** Today the registry keeps records for one run and its bans in
  memory, and the hub keeps its own bans (P6). The sublevels service keeps the lasting record: the
  connection log it already writes (D13), with the guest id on every row from every part, and one
  ban and throttle list keyed by guest id, address and range. It applies the list:
  - on the relay, through the guest verdict (a banned id is refused, a throttled one gets lower
    call limits), and by restoring its bans into the registry at start (`host.clients.ban` from
    Python, L6);
  - on the hub, at accept (`open_engine` already returns a refusal for a banned id) and in P6's
    commands, which ban by id;
  - in TX.WTF, for anonymous requests: an embedded TX.WTF asks the service in process, and an
    external one through the admin API (T5).
- **Where one machine has two ids.** A native client is known by its device id. DC++ on the same
  machine is known only by its addresses, and the two hashes differ. They're linked through the
  connection log, which keeps the addresses every id came from. So a guest ban covers the id and
  the addresses it was last seen at, as D9 already does for NMDC users. The admin view lists the
  other ids seen at those addresses.

**Semi-stable, not a credential.** A guest who changes the inputs (another OS user, a new
`CVC_DEVICE_ID`, cleared site data, a VPN) gets a new id. That is why bans also cover addresses
and ranges. A signed-in user is tracked by their account (3.1), and their client id only says which
device.

### 3.9 What a guest sees

| Part | A guest | Signed in |
|---|---|---|
| TX.WTF (nicotina `guest`) | the public feed, posts and replies, tags, search, pictures and video, the site page (COMPONENTS.md 3.7). Buttons that need an account say **Sign in to like** and so on | everything the account can do |
| Conference rooms | joins as `guest-<hex>`: sees and hears the room, chats, and sends audio and video, as cyprty's guests do now. No client programs or guests (G4) unless the operator allows them. Members-only rooms are hidden (`host.read_scope`) | the account's name, its roles, and the rooms its groups open |
| Hub, native | in the user list as `guest-<hex>`: chat, private messages, search and downloads, as NMDC's unregistered users. Relay-mode transfers (D6) are for accounts only at first: they cost the host's bandwidth (roadmap open question) | the account's name and role; relay mode; operator tools for `op` and `admin` |
| Hub, vanilla NMDC | any free nick that isn't an account's, no password | the TX.WTF username and a hub password |

The operator can turn guests off in each part: `guests: open | password | off` for the relay (the
guest password is ID2's), and `allow_guests` on the hub. TX.WTF gets `allow_anonymous_browsing`
(T4, nicotina#46 CD-7).

## 4. Alternatives

- **The device key as the root.** Each device's Ed25519 key is the identity, and a TX.WTF account
  is linked to it. Not chosen:
  - the key is derived per relay on purpose, so that two relays can't match a device. A root
    identity would have to undo that;
  - one person with a laptop, a phone and a browser is three keys, which need linking anyway;
  - a browser's key lives in `localStorage` and is gone when site data is cleared;
  - NMDC clients have no key at all;
  - TX.WTF's web app and nicotina would need key handling they don't have.

  The key stays as proof of the device, under the account.
- **A binding record.** Each part keeps its own identity (the TX.WTF account, the relay's client
  id, a hub account), and sublevels keeps a table linking them, filled when a user proves two at
  once. Not chosen:
  - three sign-ins, three sign-outs, and revocations that drift apart;
  - the table would be a fourth identity store to secure, against D9's "sublevels keeps no
    identity scheme of its own";
  - TX.WTF runs on the same host, so there's nothing to bridge.
- **TX.WTF implements arisrv identities** (Joe's other suggestion): TX.WTF keeps the account's
  device keys, and the relay asks it. The recommended design is that, minus the key storage: TX.WTF
  is the account, and the relay keeps the device proof. TX.WTF *showing* the devices an account
  has used on the relay is a later, optional step (section 6, later).
- **The OIDC access token as the relay's bearer**, instead of the session token. The host would
  refresh it by itself (`token: oidc:NAME`). Not chosen as the default, because:
  - only OIDC sign-ins have one. A password sign-in, the only route on 0.4.0 sites, gives a
    session token alone;
  - an external relay could check it only through `/oauth/userinfo`. `/oauth/introspect` answers
    only the token's own client.

  The hook could take both shapes later if needed.

## 5. Security notes

- **The session token is a full TX.WTF credential.** Sending it to the relay is fine when TX.WTF
  and the relay are one operator's: embedded, or an external TX.WTF the operator also runs.
  A relay run by someone else (federation, M7) must get an audience-bound token instead: a
  short-lived token for one relay origin, signed with TX.WTF's OIDC key (it already publishes a
  JWKS). That's later work, and until then a relay accepts only its own TX.WTF.
- **TLS.** The session token and hub passwords travel only over `grpcs://`, `wss://` and NMDCS.
  The hook should refuse a session token on a plain connection from an address that isn't
  loopback, as rcon does. Its `cred` doesn't say whether the connection is TLS today, so L1 adds
  that (a browser's `wss://` ends at a reverse proxy on loopback, which counts as secure).
- **Verify the row, not just the signature.** In process, a decoded JWT alone accepts expired and
  revoked sessions (2). The helper (T1) always checks the row.
- **Hub passwords** are scoped (`nmdc`) and revocable, and shown once. They're checked in constant
  time, and limited on both sides.
- **Privacy.** Guest ids are salted with the host's salt, so another operator's relay can't match
  them. Inside the host, the relay, the hub and TX.WTF share them by design (3.8). Devices,
  addresses and guest ids stay in the relay's private registry and the service's connection log
  (D13). Users never see them, and operators see them in the admin view. The roadmap's retention
  question covers all of these, and the salt file is as sensitive as the log.

## 6. The work, in phases

| Phase | Repo | What |
|---|---|---|
| I0 | sublevels | This note |
| I1 | txwtf | **T1** a public helper that verifies a session token in process and returns `{user_id, username, groups, session}`, and service-authenticated routes for an external relay and hub: a batch session check and a username lookup. **T3** a username rule for new accounts. **T5** a guest id for anonymous requests (the address fallback, with the host's salt) in its request log and rate limits, checked against the service's guest list |
| I1 | libcvc | **L1** a guest verdict from `@host.authenticate`, whose principal the relay sets to `guest:<client id>` after the Hello, and `cred.secure`. **L2** a verdict's `username`, set by the relay, which `server.set` can't change, and a principal's account on the registry record and in `server.whoami`. **L3** `name_engine` takes an account. **L4** arigl's conference client takes its bearer from a credential (check first). **L5** `--identity-salt-file`, and a guest whose device id is pinned to another key gets the address fallback. **L6** `host.clients.ban` / `unban` from Python. With the identity session (#1565, ID4c) |
| I2 | sublevels | The authenticate hook (embedded and external), the re-check task, groups to roles, the public TX.WTF URL (**S1**), `guests:` settings, the guest id in the connection log and one guest ban and throttle list for the relay, the hub and TX.WTF (3.8), and `host.read_scope` for operator analytics. Tests: `ServerHarness` with an in-process txwtf and a stub external one |
| I3 | txwtf | **T2** app passwords: the model, the API, the settings page, the in-process check, and the service-authenticated check route |
| I3 | sublevels | `TxwtfAccounts` on T2, the asynchronous `$MyPass` check, reserved account names at `$ValidateNick`, revocation closing logins, and the account or guest in the bridge's detail. Interop: ncdc logs in with a hub password over NMDCS |
| I4 | nicotina | `adopt` and `guest` (nicotina#46 3.7, 3.8) |
| I4 | cyprty | Its conference components mountable on the shell's relay link, taking the relay's name |
| I5 | sublevels | The client shell's identity (3.5, 3.6): OIDC or nicotina's `signin`, `adopt`, the join by credential, guest mode, one sign-out, and a Hub passwords page in Settings. `ari snapshot` tests with a stub TX.WTF and relay |
| I5 | txwtf (deploy) | The sublevels app's OIDC client and a txwtf with the device grant on the host's site (Joe's call, as nicotina's CD-5) |
| later | txwtf, libcvc | **T4** `allow_anonymous_browsing` and a public profile route (nicotina's CD-7). An audience-bound relay token (M7). TX.WTF listing the devices an account used on the relay |

I1's two halves run in parallel. I2 needs T1, L1, L2, L5 and L6; I3 needs T2. I5 needs I2 and I4. Until
I2, the relay stays as it is: every client is a guest, named by itself.

## 7. Not in scope

- Federation across operators (D10, M7), except to say it needs an audience-bound token (5).
- Multi-device E2EE for TX.WTF DMs (nicotina#46 CD-8).
- Logins at third-party hubs from the sublevels client (M4). Those are the user's own nicks and
  passwords for that hub, kept by the client, and have nothing to do with TX.WTF.
- ADC (M5), whose login is different and gets its own section then.

## 8. Decisions for Joe

| # | Question | Recommended |
|---|---|---|
| ID-D1 | The root identity | **The TX.WTF account** on the same host (3.1). The device key proves the device; the client id stays the device's (D9). Not the device key, not a binding record (4). |
| ID-D2 | What the client shows the relay | **Its TX.WTF session token**, the one nicotina adopts, verified in process when TX.WTF is embedded and by `GET /user/` when it is external. Only the relay's own TX.WTF; an audience-bound token for other operators' relays later (5). |
| ID-D3 | NMDC logins | **A hub password the TX.WTF account issues** (txwtf app passwords, scoped `nmdc`, shown once, revocable), with the TX.WTF username as the nick, over TLS. The hub never stores it. |
| ID-D4 | Account names | **Reserved everywhere** for their account, online or not. txwtf adds a nick-safe rule for new usernames, and older names get a derived form. |
| ID-D5 | Guest names | `guest-<hex>` on the relay, from the guest's id, so a returning guest gets it back and can't change it. Any free nick that isn't an account's on the hub (NMDC's way). |
| ID-D6 | What guests may do | As in 3.9. TX.WTF: public reads. Rooms: enter, chat, audio and video. Hub: chat, search and downloads; relay-mode transfers and operator tools need an account. Each part can turn guests off. |
| ID-D7 | Roles | From TX.WTF groups named in the sublevels config, re-read every minute. rcon stays as the fallback. |
| ID-D8 | The client's OIDC client | **Its own** first-party public client, `sublevels`, at the host's site, so TX.WTF's sessions screen names the app. Not nicotina's. The password form (nicotina's `signin`) until the site has one. |
| ID-D9 | Sign-out | **One Sign out for all three** (3.6), with **Continue as a guest**. A session ended elsewhere drops the relay within a minute. |
| ID-D10 | Guest ids (Joe, 2026-11-26: semi-stable, the same on reconnect from the same machine) | **The registry's client id, with a salt kept across restarts, shared by the relay, the hub and TX.WTF** (3.8). It's the device id where the client sends one, and the addresses otherwise. One guest ban and throttle list for all three parts, kept by the sublevels service. |
