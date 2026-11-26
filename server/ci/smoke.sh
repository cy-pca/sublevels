#!/usr/bin/env bash
# server/ci/smoke.sh ARISRVX: run the service in arisrvx as it is deployed, check it serves, and
# check it stops cleanly.
#
#   1. arisrvx --python server/main.py --doc server/share/sublevels/server.ari, its relay on
#      loopback ports, the HTTP API, the NMDC listener and (with openssl to make a self-signed
#      certificate) the NMDCS listener on others;
#   2. GET /healthz answers with the package's version, and, when this arisrvx has room-call
#      (libcvc S2), the log says sublevels.ping is exported;
#   3. two clients (tools/nmdc_client.py, run by arisrvx's own python) log in over NMDC and NMDCS
#      and chat, and GET /admin/stats counts their logins;
#   4. SIGTERM: arisrvx exits 0 within the shutdown timeout, with no traceback in its log.
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

# Free loopback ports, from arisrvx's own interpreter (nothing else is needed on the box).
read -r state media ws http nmdc nmdcs < <("$arisrvx" python -c '
import socket
socks = [socket.socket() for _ in range(6)]
for s in socks:
    s.bind(("127.0.0.1", 0))
print(*(s.getsockname()[1] for s in socks))
for s in socks:
    s.close()
')
version=$("$arisrvx" python -c "import sys; sys.path.insert(0, '$here/src'); import sublevels; print(sublevels.__version__)")
echo "smoke: sublevels $version; relay 127.0.0.1:$state (media $media, ws $ws), http 127.0.0.1:$http," \
  "nmdc 127.0.0.1:$nmdc"

tls_env=(SUBLEVELS_NMDCS_LISTEN=off)
if command -v openssl >/dev/null; then
  openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj /CN=smoke -keyout "$work/hub.key" \
    -out "$work/hub.crt" >/dev/null 2>&1
  tls_env=(SUBLEVELS_NMDCS_LISTEN=127.0.0.1:$nmdcs SUBLEVELS_TLS_CERT="$work/hub.crt"
           SUBLEVELS_TLS_KEY="$work/hub.key")
  echo "smoke: nmdcs 127.0.0.1:$nmdcs (a self-signed certificate)"
fi
token=smoke-admin-token-$RANDOM$RANDOM  # a test value for this run only

env SUBLEVELS_HTTP_LISTEN=127.0.0.1:$http SUBLEVELS_NMDC_LISTEN=127.0.0.1:$nmdc "${tls_env[@]}" \
  SUBLEVELS_ADMIN_TOKEN="$token" "$arisrvx" \
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

"$arisrvx" python "$here/tools/nmdc_client.py" 127.0.0.1 "$nmdc" --nick smoke-plain \
  || fail "an NMDC login and chat failed"
logins=1
if [[ "${tls_env[0]}" != SUBLEVELS_NMDCS_LISTEN=off ]]; then
  "$arisrvx" python "$here/tools/nmdc_client.py" 127.0.0.1 "$nmdcs" --tls --nick smoke-tls \
    || fail "an NMDCS login and chat failed"
  logins=2
fi
stats=$(curl -fsS --max-time 5 -H "Authorization: Bearer $token" "http://127.0.0.1:$http/admin/stats") \
  || fail "GET /admin/stats failed"
echo "smoke: GET /admin/stats -> $stats"
grep -q "\"logins\":$logins" <<<"$stats" || fail "/admin/stats did not count $logins login(s)"

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
# Asked for the native engine (SUBLEVELS_NMDC_TRANSPORT=engine), plain NMDC must be on it.
if [[ "${SUBLEVELS_NMDC_TRANSPORT:-asyncio}" == engine ]]; then
  grep -q "listening for nmdc on .* (native engine" "$log" || fail "plain NMDC is not on the native engine"
fi
# A host with the bridged-users API (libcvc 4.0.2) must have started the room bridge.
if "$arisrvx" python -c 'from pycvc.ariadne import Host; assert hasattr(Host, "room_presence")' 2>/dev/null; then
  grep -q "room bridge: NMDC users join room" "$log" || fail "this arisrvx has the bridged-users API, but the room bridge did not start"
  grep -q '"bridge_presence":2' <<<"$stats" || fail "/admin/stats: the NMDC users did not join the room"
fi
# A host with room-call (libcvc S2) must have taken the export.
if "$arisrvx" python -c 'from pycvc.ariadne import export' 2>/dev/null; then
  grep -q "room-call: sublevels.ping" "$log" || fail "this arisrvx has room-call, but sublevels.ping was not exported"
fi
echo "smoke: OK (exit 0 after SIGTERM)"
