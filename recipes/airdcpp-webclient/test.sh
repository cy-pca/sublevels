#!/usr/bin/env bash
# airdcpp-webclient smoke test, run by cvcpkg on the staged tree before packing: the documented
# setup commands (--add-user and --configure, answered on stdin), then the daemon itself through
# bin/airdcppd: its HTTP API answers with the version, the Web UI's index is served, and SIGTERM
# stops it cleanly. HOME points into a temp dir, so nothing touches the builder's own settings.
set -euo pipefail
: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
: "${CVC_DEPS_PREFIX:?CVC_DEPS_PREFIX must be set}"

# The staged tree reaches its dependencies' libraries through the deps prefix, not $ORIGIN, so the
# daemon (only: not curl) runs with it on the loader path.
libpath="${CVC_DEPS_PREFIX}/lib${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"
air() { env LD_LIBRARY_PATH="$libpath" "${CVC_INSTALL_DIR}/bin/airdcppd" "$@"; }
T="$(mktemp -d)"
pid=""
cleanup() { [[ -z "$pid" ]] || kill "$pid" 2>/dev/null || true; rm -rf "$T"; }
trap cleanup EXIT
export HOME="$T"
cfg="$T/cfg"

air -v | tee "$T/version"
grep -q '2\.14\.0' "$T/version"

port="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])')"
printf 'smoke\nsmoke-pass\nsmoke-pass\n' | air -c="$cfg" --add-user >/dev/null
printf '%s\n0\n' "$port" | air -c="$cfg" --configure >/dev/null
test -f "$cfg/web-server.json"

# Not through air(): a backgrounded function is a subshell, and $! must be the daemon itself (env
# and the launcher both exec).
env LD_LIBRARY_PATH="$libpath" "${CVC_INSTALL_DIR}/bin/airdcppd" -c="$cfg" --no-auto-connect </dev/null >"$T/daemon.log" 2>&1 &
pid=$!
info=""
for _ in $(seq 1 60); do
  info="$(curl -fsS -u smoke:smoke-pass "http://127.0.0.1:${port}/api/v1/system/system_info" 2>/dev/null || true)"
  [[ -n "$info" ]] && break
  sleep 0.5
done
[[ -n "$info" ]] || { echo "airdcppd's API did not answer"; cat "$T/daemon.log"; exit 1; }
echo "$info" | grep -q '"client_version":"AirDC++w 2\.14\.0' \
  || { echo "unexpected system_info: $info"; exit 1; }
curl -fsS -H 'Accept: text/html' "http://127.0.0.1:${port}/" | grep -qi '<title' \
  || { echo "the Web UI index was not served"; cat "$T/daemon.log"; exit 1; }

kill -TERM "$pid"
rc=0; wait "$pid" || rc=$?
pid=""
[[ "$rc" == 0 ]] || { echo "airdcppd exited $rc after SIGTERM"; cat "$T/daemon.log"; exit 1; }
# Its stdout is block-buffered into the log, so this is only complete once it has exited.
grep -q "Web resources path: ${CVC_INSTALL_DIR}/share/airdcpp/web-resources/" "$T/daemon.log" \
  || { echo "airdcppd did not use the prefix's web resources"; cat "$T/daemon.log"; exit 1; }
echo "airdcpp-webclient smoke test OK (port ${port})"
