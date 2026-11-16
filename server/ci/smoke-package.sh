#!/usr/bin/env bash
# server/ci/smoke-package.sh PREFIX: the installed `sublevels` command, as a user runs it from a
# cvcpkg prefix that holds the sublevels package and its arisrvx:
#
#   1. `sublevels version` and `sublevels check` (arisrvx check: the program imports, no ports);
#   2. `sublevels serve` with the relay, NMDC, NMDCS (a self-signed certificate), the HTTP API and
#      TX.WTF as asked ($TXWTF_MODE: unset = the default, embedded if installed, else off; a URL;
#      off; or stub: a stand-in txwtf.api on arisrvx's --python-path, served in the process);
#   3. an NMDC and an NMDCS login and chat (tools/nmdc_client.py on the prefix's python);
#      GET /healthz reports the TX.WTF mode it should, and /stats the NMDCS keyprint;
#   4. SIGTERM: exit 0, no traceback.
set -euo pipefail

[[ $# -eq 1 ]] || { echo "usage: $0 PREFIX" >&2; exit 2; }
prefix=$(cd "$1" && pwd)
here=$(cd "$(dirname "$0")/.." && pwd)
bin=$prefix/bin
work=$(mktemp -d)
log=$work/sublevels.log
pid=
cleanup() {
  [[ -n "$pid" ]] && kill -KILL "$pid" 2>/dev/null || true
  echo "---- sublevels log ----"
  cat "$log" 2>/dev/null || true
  rm -rf "$work"
}
trap cleanup EXIT
fail() { echo "smoke-package: FAIL: $*" >&2; exit 1; }

"$bin/sublevels" version | grep -q '^sublevels ' || fail "sublevels version"
read -r state media ws http nmdc nmdcs < <("$bin/python3.12" -c '
import socket
socks = [socket.socket() for _ in range(6)]
for s in socks:
    s.bind(("127.0.0.1", 0))
print(*(s.getsockname()[1] for s in socks))
for s in socks:
    s.close()
')
openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj /CN=smoke -keyout "$work/hub.key" \
  -out "$work/hub.crt" >/dev/null 2>&1
args=(--state-listen "127.0.0.1:$state" --media-listen "127.0.0.1:$media" --ws-listen "127.0.0.1:$ws"
      --nmdc "127.0.0.1:$nmdc" --nmdcs "127.0.0.1:$nmdcs" --tls-cert "$work/hub.crt"
      --tls-key "$work/hub.key" --nmdcs-address "nmdcs://127.0.0.1:$nmdcs" --http "127.0.0.1:$http"
      --hub-name "smoke hub")
expect_txwtf=off  # without txwtf installed the default falls back to off
passthrough=()
read -r txport < <("$bin/python3.12" -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])')
case "${TXWTF_MODE:-}" in
  "") ;;
  off) args+=(--no-txwtf) ;;
  stub)
    # A stand-in for TX.WTF's ASGI factory (txwtf.api.create_app), so the embedded path is tested
    # before txwtf is public.
    mkdir -p "$work/stub/txwtf"
    : >"$work/stub/txwtf/__init__.py"
    cat >"$work/stub/txwtf/api.py" <<'PY'
from fastapi import FastAPI


def create_app():
    app = FastAPI()

    @app.get("/stub")
    def stub():
        return {"txwtf": "stub"}

    return app
PY
    args+=(--txwtf embedded --txwtf-listen "127.0.0.1:$txport")
    passthrough=(-- --python-path "$work/stub")
    expect_txwtf=embedded
    ;;
  *) args+=(--txwtf "$TXWTF_MODE"); expect_txwtf=external ;;
esac

"$bin/sublevels" check "${args[@]}" "${passthrough[@]}" >"$work/check.log" 2>&1 || { cat "$work/check.log"; fail "sublevels check"; }
grep -q "ok" "$work/check.log" || { cat "$work/check.log"; fail "sublevels check said nothing ok"; }
echo "smoke-package: sublevels check ok"

"$bin/sublevels" serve "${args[@]}" "${passthrough[@]}" >"$log" 2>&1 &
pid=$!
body=
for _ in $(seq 1 600); do
  kill -0 "$pid" 2>/dev/null || { wait "$pid" && rc=0 || rc=$?; pid=; fail "sublevels exited ($rc) before it served"; }
  body=$(curl -fsS --max-time 2 "http://127.0.0.1:$http/healthz" 2>/dev/null) && break
  body=
  sleep 0.1
done
[[ -n "$body" ]] || fail "GET /healthz did not answer within 60 s"
echo "smoke-package: GET /healthz -> $body"
grep -q "\"txwtf\":\"$expect_txwtf\"" <<<"$body" || fail "/healthz: TX.WTF mode is not $expect_txwtf"
stats=$(curl -fsS --max-time 5 "http://127.0.0.1:$http/stats") || fail "GET /stats"
echo "smoke-package: GET /stats -> $stats"
grep -q '"keyprint":"SHA256/' <<<"$stats" || fail "/stats has no NMDCS keyprint"
if [[ "$expect_txwtf" == embedded ]]; then
  tx=$(curl -fsS --max-time 5 "http://127.0.0.1:$txport/stub") || fail "the embedded TX.WTF did not answer"
  echo "smoke-package: embedded TX.WTF GET /stub -> $tx"
fi

"$bin/python3.12" "$here/tools/nmdc_client.py" 127.0.0.1 "$nmdc" --nick pkg-plain || fail "NMDC login"
"$bin/python3.12" "$here/tools/nmdc_client.py" 127.0.0.1 "$nmdcs" --tls --nick pkg-tls || fail "NMDCS login"

kill -TERM "$pid"
rc=
for _ in $(seq 1 300); do
  if ! kill -0 "$pid" 2>/dev/null; then wait "$pid" && rc=0 || rc=$?; break; fi
  sleep 0.1
done
[[ -n "$rc" ]] || fail "sublevels still running 30 s after SIGTERM"
pid=
[[ "$rc" -eq 0 ]] || fail "sublevels exited $rc after SIGTERM"
if grep -q "Traceback" "$log"; then fail "a traceback in the log"; fi
echo "smoke-package: OK (TX.WTF $expect_txwtf; exit 0 after SIGTERM)"
