#!/usr/bin/env bash
# server/ci/install.sh ROOT: install arisrvx, pycvc and the service's Python stack from cvcpkg.org
# into ROOT/deps, as an operator installs them. ROOT/deps/bin/python3.12 is cvcpkg's CPython, the
# one arisrvx embeds, and runs the tests with pycvc beside it.
#
# The pins: libcvc 4.0.0, the first release on cvcpkg with libcvc S2-S7 (export/room-call,
# authenticate, read scope, ServerHarness) in arisrvx and pycvc-cp312. libcvc is pinned to the build
# arisrvx and pycvc-cp312 4.0.0+cvc.1 were built against (4.0.0+cvc.2, published 2026-11-16 01:02Z,
# minutes before them; every libcvc symbol arisrvx imports is defined there). Every pin matters:
# arisrvx and pycvc-cp312 depend on libcvc with no version, and a libcvc republished under the same
# upstream version has dropped symbols before (3.7.13+cvc.4 broke arisrvx 3.7.13+cvc.2). Move all
# three together.
set -euo pipefail

[[ $# -eq 1 ]] || { echo "usage: $0 ROOT" >&2; exit 2; }
mkdir -p "$1"
root=$(cd "$1" && pwd)
deps=$root/deps

LIBCVC=${LIBCVC:-4.0.0}
LIBCVC_BUILD=${LIBCVC_BUILD:-cvc.2}
ARISRVX_BUILD=${ARISRVX_BUILD:-cvc.1}

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
