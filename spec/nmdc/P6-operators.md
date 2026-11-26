# P6. Operators: kicks, bans, redirects and menus

Sources: [NMDC] §4.1.1 (the kick status line), §4.4.33 (`$Kick`), §4.4.34 (`$Close`), §4.4.35
(`$OpForceMove`), §4.4.36 (`$ForceMove`), §4.5.25 (`$UserCommand`), §4.6.14 (OpPlus); [D-9].

Roadmap D9 asks for Verlihub-style hub policy: operators can kick a live connection as a warning,
and ban a user for a time or for good. This item says who may do that, how NMDC operators ask for
it, and how bans are checked.

## 1. Who is an operator

- **Roles.** A user has the role `user`, `op` or `admin`. Accounts get theirs from TX.WTF or from
  the hub's own account settings. A user without an account is always `user`. Bots are their own
  class and can never be a target.
- **What each role may do:**
  - `op`: kick, close, redirect, temporary bans up to `op_max_ban` (24 hours by default), and `!info`;
  - `admin`: everything an `op` can do, plus permanent bans, unbans of any ban, the topic, and
    editing the message of the day and the rules.
- **The NMDC view.** Online `op` and `admin` users are in `$OpList` (P2 §6), and receive
  `$LogedIn` at login.
- **Native clients.** An operator connecting natively is a connection with the `admin` role in
  `ctx.principal["roles"]`: from rcon (ARISRV_IDENTITY ID3), or from a TX.WTF account with the
  role. Native operators act through room-call functions (§6).
- **Rank.** An operator cannot act on someone of the same or a higher role. An `admin` can act on
  an `op`.

## 2. NMDC commands from operators

- **`$Kick <victim>|`** [NMDC] §4.4.33.
  - From an `op` or `admin`, the hub closes the victim's connection.
  - **The reason.** Clients often send the victim a private message or a main-chat line just
    before `$Kick` [NMDC] §4.4.33. The hub takes the text of the last private message from the
    kicker to the victim within the last 10 s as the reason. Without one, the reason is
    `kicked by <op>`.
  - **What the victim sees,** before the close:
    `<sublevels> You were kicked by <op> because: <reason>|`
  - **Who else sees it.** The operators get the status form [NMDC] §4.1.1:
    `<sublevels> <op> is kicking <victim> because: <reason>|`.
    A setting, `kick_notice_all`, off by default, sends it to everyone.
  - **No ban.** A kick doesn't ban (D9: a kick is a warning). A setting, `kick_reconnect_block`
    (0 s by default), turns it into a short ban (§4).
  - **From anyone else,** the command is dropped, and the sender gets one bot line.
- **`$Close <victim>|`** [NMDC] §4.4.34. The same as `$Kick`, but the victim gets no message.
- **`$OpForceMove $Who:<victim>$Where:<address>$Msg:<reason>|`** [NMDC] §4.4.35.
  - From an operator, the hub sends the victim
    `<sublevels> You are being redirected to <address> because: <reason>|`, then
    `$ForceMove <address>|`, and closes once both are written.
  - The address must be a hub address: `host[:port]`, `dchub://`, `nmdcs://` or `adc(s)://`.
- **`$ForceMove`** is only ever sent by the hub (§2, P7 §1).
- **OpPlus.** Some hubs list `OpPlus` and take `$Ban`, `$TempBan` and similar commands [NMDC]
  §4.6.14. The hub doesn't advertise or accept them. Its bans are hub commands (§3).

## 3. Hub commands

Typed in main chat or in a private message to the hub bot (P3 §1). The hub answers the sender only.

| Command | Role | What it does |
|---|---|---|
| `+help` | user | the commands this user may use |
| `+motd`, `+rules` | user | the message of the day, the rules (P2 §7) |
| `!kick <nick> [reason]` | op | as `$Kick`, with the reason given |
| `!redirect <nick> <address> [reason]` | op | as `$OpForceMove` |
| `!ban <target> <duration> [reason]` | op (temporary), admin (`perm`) | adds a ban (§4) and closes the target's matching connections |
| `!unban <target>` | op (own bans), admin (any) | removes a ban |
| `!bans [filter]` | op | lists the bans in force |
| `!info <nick>` | op | the user's client id, account, address, country, client, connection time and recent connections (roadmap D13) |
| `!topic <text>` | admin | sets the topic, and sends `$HubTopic` (or the hub name) to everyone |
| `!motd set …`, `!rules set …` | admin | edits the documents (also editable in the admin document) |

- **Targets:** `nick:<nick>` (matches the nick's account when it has one, and the online user's
  client id), `id:<client id>`, `ip:<address>`, `net:<cidr>` and `account:<name>`. A bare nick
  means `nick:`.
- **Durations:** `<n>m`, `<n>h`, `<n>d` or `perm`.
- **Malformed commands** get a bot line with the usage.

## 4. Bans

- **A ban holds:** its target (§3), its start, an end or none (permanent), the reason, and who made
  it. Bans are stored, survive restarts, and are listed in the admin API and document.
- **When they are checked:**
  1. **at connect,** before `$Lock`: `ip:` and `net:` bans, and `id:` bans for NMDC client ids
     (derived from the address, roadmap D9);
  2. **at `$ValidateNick`:** `nick:` bans, and `account:` bans for registered nicks;
  3. **after `$MyPass`:** `account:` bans;
  4. **for native clients, in the authenticate hook (S5):** `id:` and `account:` bans.
- **A refused connection** gets one bot line before the close:
  `<sublevels> You are banned until <time> because: <reason>|`, or `permanently because:` for a
  permanent ban. During login that is the line and a close; `$ValidateDenide` is only for nicks
  that are taken (P2 §3).
- **Repeated attempts** from a banned address are not answered after the third in a minute: the
  hub closes those connections at once.
- **Expiry:** an expired ban stops applying and stays in the log.
- **Location policy** (roadmap D12) is separate from bans, and is checked at the same point as
  step 1.
- **The registry.** For native clients, and for NMDC connections once they are in libcvc's client
  registry, an `id:` ban is also made with ID3's `server.ban`, so the relay refuses the client
  everywhere. Until then the hub's own list is the one checked.

## 5. Menus: `$UserCommand`

To clients that list `UserCommand` [NMDC] §4.5.25, the hub sends menus after login (P2 §2).

- **First,** `$UserCommand 255 15|`, which clears the menus this hub sent before.
- **Then, by role:**
  - every user gets `+motd` and `+rules` in the hub context (context 1);
  - operators also get Kick, Redirect, Ban 1 hour, Ban 1 day, Ban permanently (admins only) and Info
    in the user and search contexts (2 and 4).
- **The form:** each item is type 2 (raw, once per nick), with the command as a chat line to the
  hub. For example:

  ```
  $UserCommand 2 6 Kick$<%[mynick]> !kick %[nick] %[line:Reason]&#124;|
  ```

  - `$` and `|` inside the raw command are written `&#36;` and `&#124;` [NMDC] §4.5.25.
  - `%[line:…]` prompts the user for text in DC++-derived clients (To capture).
- **When roles change** (an operator logs in or loses the role), the hub sends the user's menus
  again.

## 6. Native operators

- **Room-call functions,** each gated to the `admin` role (S2, S5):
  - `sublevels.kick(target, reason)`, `sublevels.redirect(target, address, reason)`;
  - `sublevels.ban(target, duration, reason)`, `sublevels.unban(target)`, `sublevels.bans()`;
  - `sublevels.info(target)`.
  They do what §2-§4 do, and a native user can be the target of an NMDC operator's command, and the
  other way round.
- **Registry clients** are also kicked and banned through ID3's `server.kick` and `server.ban` when
  the target is a native client.

## 7. The operator log

Every operator action is logged: the actor, the target, the action, the reason, the duration and
the time. Refusals by ban or location policy are logged too. The log is in the admin API, and
counted in the analytics (roadmap D14).

## To capture

- How DC++, AirDC++, EiskaltDC++ and ncdc show a kick: the victim's line, the status line, and
  whether they reconnect by themselves after a kick.
- Whether each client supports `%[line:…]` in `$UserCommand`, and how each shows the menus.
- How each client shows `$ForceMove`'s reason, and whether it follows to `nmdcs://` (also P7).
- What each client sends before `$Kick`: a private message, a main-chat line, or nothing.
