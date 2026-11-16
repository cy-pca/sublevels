# sublevels server

The `sublevels` service: a Python package that runs in an `arisrvx` host (libcvc
ARIADNE_SCRIPTING_HOSTS.md §6.5) on the host's one asyncio loop. M0 is the groundwork: it serves a
health check, publishes a status subtree and answers one room-call. It speaks no NMDC yet; the
listener comes in M1, in `src/sublevels/nmdc/`, from `spec/nmdc/`.

| Path | What |
|---|---|
| `src/sublevels/service.py` | The service layer that every front end calls |
| `src/sublevels/api.py` | The HTTP API (FastAPI): `GET /healthz` |
| `src/sublevels/rpc.py` | Room-call exports: `sublevels.ping` |
| `src/sublevels/server.py` | The entry point: `python -m sublevels.server` |
| `src/sublevels/nmdc/` | The NMDC listener (M1); empty |
| `main.py` | arisrvx's launcher for the same entry point |
| `share/sublevels/server.ari` | The server document: publishes the `sublevels` namespace |
| `tests/` | pytest: the service, the API, the Host and the S7 `ServerHarness` |
| `ci/install.sh`, `ci/smoke.sh` | Install the stack from cvcpkg; run the arisrvx smoke test |

## What it publishes and exports

- `sublevels.server.version`, `.started` (ISO 8601, UTC) and `.status` (`up`, then `stopping`).
- `sublevels.ping` (room-call, no arguments): `{pong: true, version, who}`, where `who` is the
  caller's principal id.
- `GET /healthz`: `{status: "ok", version, uptime}`.

## Running it

Install the stack from cvcpkg (`ci/install.sh ROOT` pins what CI uses):

```bash
cvcpkg install arisrvx pycvc-cp312 fastapi-cp312 uvicorn-cp312 --prefix deps
```

In arisrvx, which runs a script by path and passes it no arguments:

```bash
SUBLEVELS_HTTP_LISTEN=127.0.0.1:8411 deps/bin/arisrvx --python server/main.py --doc server/share/sublevels/server.ari
```

Under plain python, with pycvc beside it:

```bash
cd server && PYTHONPATH=src ../deps/bin/python3.12 -m sublevels.server --http-listen 127.0.0.1:8411
```

The HTTP address is `--http-listen`, else `$SUBLEVELS_HTTP_LISTEN`, else `127.0.0.1:8411`; `off`
serves none. SIGTERM or SIGINT stops it.

## Tests

```bash
cd server && ../deps/bin/python3.12 -m pytest
```

The service and API tests need only fastapi, httpx and pytest. The Host tests need pycvc, and the
harness tests libcvc S7's `pycvc.ariadne.testing`; without them they skip, unless
`SUBLEVELS_REQUIRE_PYCVC=1` or `SUBLEVELS_REQUIRE_HARNESS=1` makes that a failure. Room-call needs
libcvc S2: on an older host (arisrvx 3.7.13+cvc.2) the service serves HTTP and publishes, and
registers no exports.
