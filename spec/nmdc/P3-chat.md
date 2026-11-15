# P3. Main chat and private messages

Sources: [NMDC] §4.1.1, §4.4.1, §4.4.2, §4.5.12, §4.5.11 (DND); [D-9].

## 1. Main chat

`<nick> text|`

- From an online user, `<nick>` MUST equal the connection's nick. Otherwise the hub drops the
  line, counts it, and after 3 such lines closes the connection.
- The hub sends the line to every online user, **the sender included**: clients show their own
  messages when the hub echoes them, not when they send them.
- The text is display text (P1 §3). Some implementations allow spaces in nicks, and clients find
  the nick by the first `> `; the hub's nicks have no spaces (P2 §3).
- **Limits** (hub settings): text up to 2048 bytes by default; at most 5 lines in 10 s per user,
  with a burst of 3. Over the limit, the line is dropped and the sender gets one bot line saying
  so.
- **Commands.** A line whose text starts with `+` (users) or `!` (operators) is a hub command.
  The hub answers the sender with bot lines (§4) and does not broadcast it. M1 has `+help`,
  `+motd` and `+rules` [D-9]. The operator commands are P6.

## 2. Private messages

`$To: <target> From: <sender> $<<sender>> text|`

- `<sender>` and the nick inside `<…>` MUST both equal the connection's nick; otherwise the hub
  drops the message, as in §1.
- `<target>` MUST be an online user or a bot. If not, the hub answers the sender with a private
  message from the hub bot saying the user is not online.
- The hub sends the message, unchanged, to `<target>` only.
- **Limits:** text up to 2048 bytes; at most 10 private messages in 10 s per user.
- **Do not disturb.** A target whose status says do-not-disturb (the `$IN` status bit 7 in [NMDC]
  §4.5.11; no `$MyINFO` equivalent) is not supported in M1. The hub delivers all private messages.
- **Private messages to the hub bot** are commands, as in §1, answered by private message.

## 3. `$MCTo`

`$MCTo: <target> $<sender> text|` [NMDC] §4.5.12

A private message the target's client shows in main chat. The hub accepts it from clients that
listed `MCTo` and forwards it only to targets that listed it, with the same checks as `$To`. To a
target without `MCTo`, the hub sends it as a `$To`. The hub advertises `MCTo` only once this is
built (P2 §2).

## 4. Hub text

- The hub speaks through a bot user, by default named `sublevels`. The bot is in the user list
  (P2 §6), in `$BotList`, and in `$OpList` so that clients treat its messages as trusted.
- **To one user** the hub uses a private message from the bot, or a main-chat line that only that
  user receives (`<sublevels> text|` written to their connection alone). Replies to commands and
  refusals during login use main-chat lines, because a client may not open private-message
  windows before login.
- **To everyone:** a main-chat line from the bot.
- **Kicks shown by clients.** Clients show a main-chat line containing both `is kicking` and
  `because:` as a status message, for example
  `<sublevels> sublevels is kicking <nick> because: <reason>|` [NMDC] §4.1.1. The hub uses this
  form for kicks (P6). A line in this form from a user is not a kick: it is ordinary chat.

## 5. Native and bridged chat

- **Native users** (over arisrvx) post and receive main chat through the service. Their lines go to
  NMDC users as `<nick> text|` and their private messages as `$To:`, encoded in the hub's code page
  (P1 §2), so characters it cannot hold arrive as `?`.
- **Text from NMDC users** reaches native clients decoded and unescaped (P1 §3).
- **The bridge to a conference room** (M2) carries main chat both ways. Room users who are not hub
  users appear in NMDC chat with a prefix that marks them as bridged, so their nicks cannot be
  confused with hub users'. The prefix is decided with the bridge.

## To capture

- How each target client sends `/me`: as `<nick> /me text`, `* nick text`, or not at all.
- Whether any target client relies on the hub not echoing main chat back to the sender.
- Whether each client opens a private-message window for a `$To` that arrives during login.
- How each client shows bot private messages from a nick in `$OpList` and `$BotList`.
