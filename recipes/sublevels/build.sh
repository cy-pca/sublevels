#!/usr/bin/env bash
# sublevels: install the Python package, the arisrvx program and server document, and the launcher.
# Nothing to compile.
set -euo pipefail

: "${CVC_SOURCE_DIR:?CVC_SOURCE_DIR must be set}"   # the sublevels repo root (vendored)
: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"

SRC="${CVC_SOURCE_DIR}/server"
SITE="${CVC_INSTALL_DIR}/lib/python3.12/site-packages"
SHARE="${CVC_INSTALL_DIR}/share/sublevels"
DOC="${CVC_INSTALL_DIR}/share/doc/sublevels"
install -d "${CVC_INSTALL_DIR}/bin" "${SITE}" "${SHARE}" "${DOC}"

rm -rf "${SITE}/sublevels"
( cd "${SRC}/src" && find sublevels -type f \( -name '*.py' -o -name '*.md' \) \
    ! -path '*/__pycache__/*' -print0 | while IFS= read -r -d '' f; do
      install -D -m 0644 "$f" "${SITE}/$f"
    done )
install -m 0644 "${SRC}/main.py" "${SHARE}/main.py"
install -m 0644 "${SRC}/share/sublevels/server.ari" "${SHARE}/server.ari"
install -m 0755 "${SRC}/bin/sublevels" "${CVC_INSTALL_DIR}/bin/sublevels"
install -m 0644 "${CVC_SOURCE_DIR}/LICENSE" "${CVC_SOURCE_DIR}/README.md" "${DOC}/"
install -m 0644 "${SRC}/README.md" "${DOC}/server-README.md"
echo "cvcpkg: installed sublevels -> ${SITE}/sublevels, ${SHARE}, ${CVC_INSTALL_DIR}/bin/sublevels"
