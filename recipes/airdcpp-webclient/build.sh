#!/usr/bin/env bash
# airdcpp-webclient: the daemon from upstream's source (CMake), the Web UI from its npm package,
# and the bin/airdcppd launcher. See the recipe's notes for why each piece is where it is.
set -euo pipefail

: "${CVC_SOURCE_DIR:?CVC_SOURCE_DIR must be set}"
: "${CVC_BUILD_DIR:?CVC_BUILD_DIR must be set}"
: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
: "${CVC_DEPS_PREFIX:?CVC_DEPS_PREFIX must be set}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CVC_JOBS="${CVC_JOBS:-$(nproc 2>/dev/null || echo 4)}"
case "$(echo "${CVC_BUILD_TYPE:-release}" | tr '[:upper:]' '[:lower:]')" in
  debug) BUILD_TYPE=Debug ;;
  *) BUILD_TYPE=Release ;;
esac

# The Web UI: upstream's INSTALL_WEB_UI step runs `npm install airdcpp-webui@<version>` and copies
# its dist/. This is the same package, fetched directly and checked against a pin.
AIRDCPP_VERSION=2.14.0
WEBUI_VERSION=2.14.0
WEBUI_URL="https://registry.npmjs.org/airdcpp-webui/-/airdcpp-webui-${WEBUI_VERSION}.tgz"
WEBUI_SHA256=5240dc788f5b6988d0766e084b572652b4495402b7bc3516cc14651dc81a84a7

prefix_path="${CVC_DEPS_PREFIX}"
if [[ -n "${CVC_BUILD_PREFIX:-}" && "${CVC_BUILD_PREFIX}" != "${CVC_DEPS_PREFIX}" ]]; then
  prefix_path="${CVC_DEPS_PREFIX};${CVC_BUILD_PREFIX}"
fi

# RESOURCE_DIRECTORY and GLOBAL_CONFIG_DIRECTORY are compiled in. The first is only a fallback
# here (the launcher passes --web-resources); the second is where upstream reads an optional
# system-wide dcppboot.xml, /etc/airdcpp/ as in its distribution packages.
cmake -G Ninja -S "${CVC_SOURCE_DIR}" -B "${CVC_BUILD_DIR}" \
  -DCMAKE_BUILD_TYPE="${BUILD_TYPE}" \
  -DCMAKE_INSTALL_PREFIX="${CVC_INSTALL_DIR}" \
  -DCMAKE_INSTALL_LIBDIR=lib \
  -DCMAKE_PREFIX_PATH="${prefix_path}" \
  -DBUILD_SHARED_LIBS=ON \
  -DINSTALL_WEB_UI=OFF \
  -DENABLE_NATPMP=OFF \
  -DENABLE_TBB=OFF \
  -DSTRIP=OFF \
  -DRESOURCE_DIRECTORY=/usr/share/airdcpp/ \
  -DGLOBAL_CONFIG_DIRECTORY=/etc/airdcpp/ \
  -DCMAKE_INSTALL_RPATH='$ORIGIN/../lib;$ORIGIN/../../lib'
cmake --build "${CVC_BUILD_DIR}" -j "${CVC_JOBS}"
cmake --install "${CVC_BUILD_DIR}"

# The daemon moves to libexec/; bin/airdcppd becomes the launcher.
install -d "${CVC_INSTALL_DIR}/libexec/airdcpp-webclient"
mv "${CVC_INSTALL_DIR}/bin/airdcppd" "${CVC_INSTALL_DIR}/libexec/airdcpp-webclient/airdcppd"
install -m 0755 "${SCRIPT_DIR}/airdcppd.sh" "${CVC_INSTALL_DIR}/bin/airdcppd"

webui_tgz="${CVC_BUILD_DIR}/airdcpp-webui-${WEBUI_VERSION}.tgz"
curl -fsSL --retry 3 -o "${webui_tgz}" "${WEBUI_URL}"
echo "${WEBUI_SHA256}  ${webui_tgz}" | sha256sum -c -
webui_dir="${CVC_BUILD_DIR}/airdcpp-webui"
rm -rf "${webui_dir}" && mkdir -p "${webui_dir}"
tar -xzf "${webui_tgz}" -C "${webui_dir}" --strip-components=1
resources="${CVC_INSTALL_DIR}/share/airdcpp/web-resources"
rm -rf "${resources}" && install -d "${resources}"
cp -R "${webui_dir}/dist/." "${resources}/"
test -f "${resources}/index.html" || { echo "airdcpp-webclient: the Web UI has no index.html" >&2; exit 1; }

# Licence texts: upstream's tree has none, so cvcpkg would find nothing to copy (see recipe.yaml).
licenses="${CVC_INSTALL_DIR}/share/licenses/airdcpp-webclient"
install -d "${licenses}"
install -m 0644 "${SCRIPT_DIR}/GPL-2.0.txt" "${licenses}/GPL-2.0.txt"
install -m 0644 "${webui_dir}/LICENSE" "${licenses}/airdcpp-webui-LICENSE"
cat > "${licenses}/README" <<TXT
airdcpp-webclient ${AIRDCPP_VERSION}, built from https://github.com/airdcpp-web/airdcpp-webclient
(tag ${AIRDCPP_VERSION}). The daemon is free software under the GNU General Public License, version 2 or (at
your option) any later version: GPL-2.0.txt. Its Web UI, airdcpp-webui ${WEBUI_VERSION}
(https://github.com/airdcpp-web/airdcpp-webui), is MIT: airdcpp-webui-LICENSE.
TXT

echo "cvcpkg: airdcpp-webclient -> ${CVC_INSTALL_DIR} (bin/airdcppd launcher, libexec/airdcpp-webclient/airdcppd)"
