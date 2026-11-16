#!/bin/sh
# airdcppd launcher (cvcpkg airdcpp-webclient): runs the daemon from libexec/ with the prefix's
# Web UI, so the prefix can live anywhere. Every argument is passed through; a --web-resources
# on the command line wins.
set -e
self=$(readlink -f "$0" 2>/dev/null || echo "$0")
prefix=$(cd "$(dirname "$self")/.." && pwd)
daemon="$prefix/libexec/airdcpp-webclient/airdcppd"
for arg in "$@"; do
  case "$arg" in
    --web-resources=*) exec "$daemon" "$@" ;;
  esac
done
exec "$daemon" "--web-resources=$prefix/share/airdcpp/web-resources/" "$@"
