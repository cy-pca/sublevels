#!/usr/bin/env bash
# server/ci/install.sh ROOT: install arisrvx, pycvc and the service's Python stack from cvcpkg.org
# into ROOT/deps, as an operator installs them. ROOT/deps/bin/python3.12 is cvcpkg's CPython, the
# one arisrvx embeds, and runs the tests with pycvc beside it.
#
# The pins: the newest published libcvc release is 3.7.13, which has libcvc S0 and S1 (Host,
# serve_asgi) but not S2-S7 (export/room-call, authenticate, read scope, ServerHarness). Those come
# with 3.7.14; then bump LIBCVC and set SUBLEVELS_REQUIRE_HARNESS=1 in ci.yml.
# libcvc itself is pinned to +cvc.3: arisrvx and pycvc-cp312 3.7.13+cvc.2 were built against it,
# and the libcvc 3.7.13+cvc.4 republish (2026-11-15) no longer exports symbols they link
# (cvc::image::save(std::string), cvc::ariadne::register_cvc_uri_handler), so they fail to load.
set -euo pipefail

[[ $# -eq 1 ]] || { echo "usage: $0 ROOT" >&2; exit 2; }
mkdir -p "$1"
root=$(cd "$1" && pwd)
deps=$root/deps

LIBCVC=${LIBCVC:-3.7.13}
LIBCVC_BUILD=${LIBCVC_BUILD:-cvc.3}
ARISRVX_BUILD=${ARISRVX_BUILD:-cvc.2}

cvcpkg install \
  "libcvc==$LIBCVC+$LIBCVC_BUILD" \
  "arisrvx==$LIBCVC+$ARISRVX_BUILD" \
  "pycvc-cp312==$LIBCVC+$ARISRVX_BUILD" \
  fastapi-cp312 uvicorn-cp312 httpx-cp312 pytest-cp312 \
  --prefix "$deps" --config release --link shared --no-fallback-to-source
sed -n '/^- name: \(arisrvx\|pycvc-cp312\|libcvc\|libcvc-cuda\|python312\|fastapi-cp312\|uvicorn-cp312\)$/{p;n;p;}' \
  "$deps/share/libcvc-deps/lockfile.yaml"

# A host that cannot load would fail the smoke run for a reason that has nothing to do with
# sublevels: say so here.
if ldd "$deps/bin/arisrvx" "$deps/lib/python3.12/site-packages/_pycvc.so" | grep 'not found'; then
  echo "::error::arisrvx or pycvc cannot find a shared library" >&2
  exit 1
fi
"$deps/bin/python3.12" -c 'import pycvc, pycvc.ariadne, fastapi, uvicorn, httpx, pytest' || {
  echo "::error::cvcpkg's python3.12 cannot import pycvc or the ASGI stack" >&2
  exit 1
}
"$deps/bin/python3.12" -c '
import pycvc.ariadne as a
s2 = hasattr(a, "export")
try:
    import pycvc.ariadne.testing
    s7 = True
except ImportError:
    s7 = False
yn = {True: "yes", False: "no"}
print("pycvc.ariadne: room-call (S2)", yn[s2] + ", ServerHarness (S7)", yn[s7])
'
