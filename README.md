# sublevels

Direct Connect for the Ariadne stack: the NMDC (and later ADC) protocols, implemented from
public specifications as Ariadne documents and an `arisrvx` service.

- **Server.** A `sublevels` service that runs in an `arisrvx` host next to TX.WTF and the
  Ariadne conference rooms. It speaks the hub side of NMDC, so DC++ and the clients derived
  from it can connect. Native Ariadne clients reach the same hub over the arisrvx state plane.
- **Client.** An Ariadne document component set for the client side of the protocol.
  [nicotina](https://github.com/cy-pca/nicotina) uses it to reach sublevels hubs natively
  over arisrvx, and to reach ordinary NMDC hubs.
- **Extensions.** The conference suite's features (channels, calls, media, end-to-end
  encrypted messages) reach hub users. How much of this also goes over the NMDC wire is
  decided in the roadmap.

This is a clean-room implementation. It contains no code from eiskaltdcpp, DC++, verlihub or
any other Direct Connect implementation. See [CLEANROOM.md](CLEANROOM.md).

## Layout

| Path | What |
|---|---|
| [`spec/`](spec/README.md) | The protocol spec that implementers work from, with sources |
| [`server/`](server/README.md) | The `sublevels` Python package, its arisrvx launcher and server document, and their tests |
| `client/` | The client component set (planned) |
| `docs/` | [Roadmap](docs/ROADMAP.md) and design notes |

## License

MIT. See [LICENSE](LICENSE).
