#!/usr/bin/env bash
# server/ci/smoke.sh ARISRVX: run the service in arisrvx as it is deployed, check it serves, and
# check it stops cleanly.
#
#   1. arisrvx --python server/main.py --doc server/share/sublevels/server.ari, its relay on
#      loopback ports, the HTTP API on another (SUBLEVELS_HTTP_LISTEN);
#   2. GET /healthz answers with the package's version;
#   3. SIGTERM: arisrvx exits 0 within the shutdown timeout, with no traceback in its log.
#
# The log is printed at the end either way.
set -euo pipefail

[[ $# -eq 1 ]] || { echo "usage: $0 ARISRVX" >&2; exit 2; }
arisrvx=$1
here=$(cd "$(dirname "$0")/.." && pwd)
work=$(mktemp -d)
log=$work/arisrvx.log
pid=
cleanup() {
  [[ -n "$pid" ]] && kill -KILL "$pid" 2>/dev/null || true
  echo "---- arisrvx log ----"
  cat "$log" 2>/dev/null || true
  rm -rf "$work"
}
trap cleanup EXIT
fail() { echo "smoke: FAIL: $*" >&2; exit 1; }

# Four free loopback ports, from arisrvx's own interpreter (nothing else is needed on the box).
read -r state media ws http < <("$arisrvx" python -c '
import socket
socks = [socket.socket() for _ in range(4)]
for s in socks:
    s.bind(("127.0.0.1", 0))
print(*(s.getsockname()[1] for s in socks))
for s in socks:
    s.close()
')
version=$("$arisrvx" python -c "import sys; sys.path.insert(0, '$here/src'); import sublevels; print(sublevels.__version__)")
echo "smoke: sublevels $version; relay 127.0.0.1:$state (media $media, ws $ws), http 127.0.0.1:$http"

SUBLEVELS_HTTP_LISTEN=127.0.0.1:$http "$arisrvx" \
  --python "$here/main.py" --doc "$here/share/sublevels/server.ari" \
  --state-listen "127.0.0.1:$state" --media-listen "127.0.0.1:$media" --ws-listen "127.0.0.1:$ws" \
  --shutdown-timeout 5 >"$log" 2>&1 &
pid=$!

body=
for _ in $(seq 1 600); do
  kill -0 "$pid" 2>/dev/null || { wait "$pid" && rc=0 || rc=$?; pid=; fail "arisrvx exited ($rc) before it served"; }
  if body=$(curl -fsS --max-time 2 "http://127.0.0.1:$http/healthz" 2>/dev/null); then
    break
  fi
  body=
  sleep 0.1
done
[[ -n "$body" ]] || fail "GET /healthz did not answer within 60 s"
echo "smoke: GET /healthz -> $body"
grep -q "\"version\":\"$version\"" <<<"$body" || fail "/healthz did not report version $version"
grep -q "\"status\":\"ok\"" <<<"$body" || fail "/healthz did not report status ok"

kill -TERM "$pid"
rc=
for _ in $(seq 1 300); do
  if ! kill -0 "$pid" 2>/dev/null; then
    wait "$pid" && rc=0 || rc=$?
    break
  fi
  sleep 0.1
done
[[ -n "$rc" ]] || fail "arisrvx still running 30 s after SIGTERM"
pid=
[[ "$rc" -eq 0 ]] || fail "arisrvx exited $rc after SIGTERM"
if grep -q "Traceback" "$log"; then fail "a traceback in arisrvx's log"; fi
grep -q "publishing sublevels.server" "$log" || fail "the service did not report publishing"
echo "smoke: OK (exit 0 after SIGTERM)"
