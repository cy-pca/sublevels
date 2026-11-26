# sublevels server

The `sublevels` service: a Python package that runs in an `arisrvx` host (libcvc
ARIADNE_SCRIPTING_HOSTS.md §6.5) on the host's one asyncio loop. It is an NMDC hub (M1, from
`spec/nmdc/`), with an HTTP API, a room-call export and a published subtree beside it.

| Path | What |
|---|---|
| `src/sublevels/service.py` | The service layer that every front end calls |
| `src/sublevels/api.py` | The HTTP API (FastAPI): `GET /healthz` |
| `src/sublevels/rpc.py` | Room-call exports: `sublevels.ping` |
| `src/sublevels/server.py` | The entry point: `python -m sublevels.server` |
| `src/sublevels/nmdc/` | The NMDC hub: see [its README](src/sublevels/nmdc/README.md) |
| `main.py` | arisrvx's launcher for the same entry point |
| `share/sublevels/server.ari` | The server document: publishes the `sublevels` namespace |
| `tests/` | pytest: the hub per spec item, the service, the API, the Host and the S7 `ServerHarness` |
| `tools/nmdc_client.py` | A small NMDC client for the tests, the smoke run and the soak |
| `tools/run_hub.py` | The hub alone on plain asyncio, stdlib only, for interop runs and the soak |
| `interop/ncdc_interop.py` | ncdc as a black box against the hub, with the hub's captures and findings |
| `interop/airdcpp_interop.py` | The AirDC++ web client as a black box, through its documented HTTP API |
| `interop/transfers.py` | ncdc and AirDC++ download each other's files through the hub, over NMDC or NMDCS |
| `interop/eiskaltdcpp_interop.py` | EiskaltDC++ through its JSON-lines driver (`eiskaltdcpp-interop`, cvcpkg `eiskaltdcpp-py-cp312`): login, users, chat, PMs, search and downloads both ways, a wrong keyprint, and an operator's redirect and kick (P6) |
| `interop/soak.py` | Synthetic clients toward 6000+ (D7): memory, CPU, fan-out latency and the broadcasts-per-second ceiling, on either transport |
| `interop/bridge_e2e.py`, `interop/bridge_client.ari` | The room bridge end to end: an Ariadne client (arigl) and an NMDC client in the conference room, on real arisrvx |
| `bin/sublevels` | The `sublevels` command: the relay, the hub, the HTTP API and TX.WTF in one arisrvx |
| `src/sublevels/txwtf_service.py` | TX.WTF in this process, a separate instance, or none |
| `src/sublevels/bridge.py` | NMDC users in the conference room; the hub's chat and the room's joined |
| `ci/install.sh`, `ci/smoke.sh`, `ci/smoke-package.sh` | Install the stack from cvcpkg; the arisrvx smoke run; the installed command's |

## What it publishes and exports

- `sublevels.server.version`, `.started` (ISO 8601, UTC) and `.status` (`up`, then `stopping`).
- `sublevels.ping` (room-call, no arguments): `{pong: true, version, who}`, where `who` is the
  caller's principal id.
- `GET /healthz`: `{status: "ok", version, uptime}`.
- `GET /stats`: `{users}`. `GET /admin/stats` (`Authorization: Bearer <token>`, the token from
  `$SUBLEVELS_ADMIN_TOKEN_FILE` or `$SUBLEVELS_ADMIN_TOKEN`; off without one): the D14 counters.
- `sublevels.hubs.main.{name, topic, bot, users.count, users.list}`: the hub, for native clients.
- **The room bridge** (`src/sublevels/bridge.py`; arisrvx and pycvc 4.0.2+): every online NMDC
  user is a bridged user of the conference room (`--room`), listed in its users for Ariadne
  clients. NMDC main chat and the room chat are joined, and direct messages go both ways. Room
  users appear to NMDC clients as `[room]name`, a prefix no hub nick may take.
- NMDC on `$SUBLEVELS_NMDC_LISTEN` (`0.0.0.0:411`), NMDCS on `$SUBLEVELS_NMDCS_LISTEN`
  (`0.0.0.0:4111`, with `$SUBLEVELS_TLS_CERT` and `$SUBLEVELS_TLS_KEY`). Port 411 needs the
  privilege to bind it; the other settings are in `src/sublevels/nmdc/config.py`.

## Running it

From cvcpkg (the `sublevels` package, in the cy-pca organisation, brings arisrvx and pycvc with it):

```bash
cvcpkg install sublevels --prefix ~/sublevels
~/sublevels/bin/sublevels --tls-cert hub.crt --tls-key hub.key --nmdcs-address nmdcs://hub.example:4111
```

`sublevels` runs one arisrvx process:

- **The conference relay**, with cyprty's settings unless given: room `standup`, state plane
  `0.0.0.0:7700`, media `0.0.0.0:7701`, WebSocket `0.0.0.0:7702`.
- **The NMDC hub** on `0.0.0.0:411`. Binding port 411 needs the privilege, so `--nmdc` can move it.
- **NMDCS** on `0.0.0.0:4111` when a certificate is given. The keyprint is published at `/stats`.
- **The HTTP API** on `127.0.0.1:8411`.
- **TX.WTF**, in the same process when `txwtf` is installed. `--txwtf URL` names a separate
  instance, and `--no-txwtf` turns it off.

`sublevels help` lists every option. `sublevels check` imports the program and binds nothing.

From a checkout, the same launcher works as `server/bin/sublevels` with an arisrvx on PATH (for
example `cvcpkg install arisrvx pycvc-cp312 fastapi-cp312 uvicorn-cp312 --prefix deps`;
`ci/install.sh ROOT` pins what CI uses). Under plain python, with pycvc beside it, the server runs
without the relay:

```bash
cd server && PYTHONPATH=src ../deps/bin/python3.12 -m sublevels.server --http-listen 127.0.0.1:8411 \
  --nmdc-listen 127.0.0.1:4110 --nmdcs-listen off --txwtf off
```

## Tests

```bash
cd server && ../deps/bin/python3.12 -m pytest
```

The service and API tests need only fastapi, httpx and pytest. The Host tests need pycvc, and the
harness tests libcvc S7's `pycvc.ariadne.testing`; without them they skip, unless
`SUBLEVELS_REQUIRE_PYCVC=1` or `SUBLEVELS_REQUIRE_HARNESS=1` makes that a failure. Room-call needs
libcvc S2: on an older host (arisrvx 3.7.13+cvc.2) the service serves HTTP and publishes, and
registers no exports.
