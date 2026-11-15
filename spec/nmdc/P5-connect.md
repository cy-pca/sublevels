# P5. Connection signalling

Sources: [NMDC] §4.2.1, §4.4.3, §4.4.4, §4.6.10, §4.6.18, §4.6.19; [ADC-EXT] §3.9 (NATT); [D-6].

The hub only passes connection requests between users. The connection itself, and every transfer,
is client to client (P8), or for native users relay mode [D-6].

## 1. `$ConnectToMe`

`$ConnectToMe <remote> <ip>:<port>[S]|`

The sender is listening on `ip:port` and asks `<remote>` to connect to it. An `S` after the port
means the listener speaks TLS (the `TLS` feature [NMDC] §4.6.19).

- **`<remote>`** MUST be an online user other than the sender, and not a bot. Otherwise the request
  is dropped.
- **The address** MUST be the sender's own [NMDC] §4.2.1. The hub replaces it with the address it
  sees for the connection, and keeps the port and suffix. A port that is not 1 to 65535 drops the
  request.
- **The suffix.** `S` is kept. Other single-letter suffixes are kept as they are, because clients
  use them for NAT traversal (To capture).
- The hub forwards the request, with the rewritten address, to `<remote>` only.
- **Older and newer forms.** [NMDC] records a form with the sender's nick before `<remote>`
  (`$ConnectToMe <sender> <remote> <ip>:<port>|`). The hub accepts it, checks that `<sender>` equals
  the connection's nick, and forwards it in the same form.

## 2. `$RevConnectToMe`

`$RevConnectToMe <sender> <remote>|`

A passive sender asks `<remote>` to send it a `$ConnectToMe`.

- `<sender>` MUST equal the connection's nick, and `<remote>` MUST be an online user other than the
  sender, and not a bot. Otherwise the request is dropped.
- **Both passive.** If both users' modes are passive (P2 §5), neither can accept a connection. The
  hub does not forward the request, and sends the sender one bot line saying the other user is also
  passive. The exception: if both users signal NAT traversal support (To capture), the hub forwards
  the request.
- Otherwise the hub forwards the request, unchanged, to `<remote>` only.

## 3. Limits

- A user may send at most 20 `$ConnectToMe` and `$RevConnectToMe` in 10 s, with at most 5 to the
  same remote user. Requests over the limit are dropped.
- These limits stop a user from flooding another; the address rewrite (§1) stops a user from aiming
  other clients at a third party [NMDC] §4.2.1.

## 4. IPv6 and the mode field

- With the `IP64` feature [NMDC] §4.6.18, a tag's mode is two characters, for IPv4 then IPv6, each
  `A`, `P`, `5`, or `N` (not supported).
- The hub's listeners accept IPv4 and IPv6. The address the hub writes into a request is the
  family of the sender's hub connection. Translating between families for dual-stack users, as
  [NMDC] describes, is not done in M1.

## 5. `$CTM` and `$RCTM`

The ACTM extension [NMDC] §4.6.10 replaces these commands with `$CTM` and `$RCTM`, which carry a
request id. Clients that use it must still answer the classic commands. The hub does not
advertise or forward ACTM in M1.

## 6. Native users

- Native users have no NMDC listener until the vanilla client path exists (M4). In the NMDC view
  they are passive (P2 §8).
- Between two native users, a transfer may be direct, passive, or relayed through arisrvx inside an
  end-to-end encrypted channel, where neither learns the other's address [D-6]. That signalling
  is not NMDC and is specified with relay mode (M3).

## To capture

- What NAT traversal each target client sends in NMDC (suffixes on the port, extra arguments, a
  `$Supports` feature) and what it expects back. [ADC-EXT] §3.9 (NATT) describes the ADC form, and
  clients that have it in NMDC may follow it.
- Which form of `$ConnectToMe` each client sends.
- Whether any client sends `$ConnectToMe` to a remote user it learned of from another hub.
