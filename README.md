# sublevels

Direct Connect for the Ariadne stack: the NMDC (and later ADC) protocols, implemented from
public specifications as Ariadne documents and an `arisrvx` service.

- **Server.** A `sublevels` service that runs in an `arisrvx` host next to TX.WTF and the
  Ariadne conference rooms. It speaks the hub side of NMDC, so DC++ and the clients derived
  from it can connect. Native Ariadne clients reach the same hub over the arisrvx state plane.
- **Client.** The sublevels client: an Ariadne app, written as document components, that reaches
  sublevels hubs natively over arisrvx and ordinary NMDC hubs. It builds on
  [nicotina](https://github.com/cy-pca/nicotina)'s TX.WTF components (sign-in, profiles, DMs) and
  [cyprty](https://github.com/cy-pca/cyprty)'s conference components. nicotina itself stays a TX.WTF
  client only.
- **Extensions.** The conference suite's features (channels, calls, media, end-to-end
  encrypted messages) reach hub users. How much of this also goes over the NMDC wire is
  decided in the roadmap.

This is a clean-room implementation. It contains no code from eiskaltdcpp, DC++, verlihub or
any other Direct Connect implementation. See [CLEANROOM.md](CLEANROOM.md).

## Install

```bash
cvcpkg install sublevels
sublevels help
```

`sublevels` runs the conference relay, the NMDC/NMDCS hub, its HTTP API and (by default) TX.WTF
in one arisrvx process. See [server/README.md](server/README.md).

## Layout

| Path | What |
|---|---|
| [`spec/`](spec/README.md) | The protocol spec that implementers work from, with sources |
| [`server/`](server/README.md) | The `sublevels` Python package, its arisrvx launcher and server document, and their tests |
| `client/` | The client component set (planned) |
| `recipes/` | cvcpkg recipes, published under the cy-pca organisation |
| `docs/` | [Roadmap](docs/ROADMAP.md) and design notes |

## License

MIT. See [LICENSE](LICENSE).
