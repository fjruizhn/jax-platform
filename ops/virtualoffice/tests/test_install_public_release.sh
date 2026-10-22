#!/usr/bin/env bash
# Disposable root fixture for the immutable public frontend installer.
set -euo pipefail

root=$(cd "$(dirname "$0")/.." && pwd)
installer="$root/install-public-release.sh"
config="$root/virtualoffice.axioma-ia.io.conf"
[[ ${EUID:-$(id -u)} == 0 ]] || { echo 'SKIP: requires root for ownership fixtures'; exit 77; }

fixture=$(mktemp -d)
trap 'rm -rf -- "$fixture"' EXIT
chmod 755 "$fixture"

make_built_release() {
  local sha=$1 release="$fixture/built/$1"
  mkdir -p "$release/dist/assets"
  printf '<html>office %s</html>\n' "$sha" > "$release/dist/index.html"
  printf 'asset %s\n' "$sha" > "$release/dist/assets/index-deadbeef.js"
  local index asset
  index=$(sha256sum "$release/dist/index.html" | awk '{print $1}')
  asset=$(sha256sum "$release/dist/assets/index-deadbeef.js" | awk '{print $1}')
  printf '{"source_sha":"%s","files":{"assets/index-deadbeef.js":"%s","index.html":"%s"}}\n' "$sha" "$asset" "$index" > "$release/manifest.json"
}

good=1111111111111111111111111111111111111111
bad=2222222222222222222222222222222222222222
mkdir -p "$fixture/built" "$fixture/site/releases"
make_built_release "$good"
chown -R nobody:nogroup "$fixture/built"
chown -R root:root "$fixture/site"
chmod -R go-w "$fixture/site"

"$installer" --built-release "$fixture/built/$good" --site-root "$fixture/site" \
  --releases-root "$fixture/site/releases" --config "$config" --expected-source-sha "$good" --nginx-user nobody >/dev/null
target="$fixture/site/releases/$good"
[[ $(stat -c '%U:%G:%a' "$target") == root:root:755 ]]
[[ $(stat -c '%U:%G:%a' "$target/manifest.json") == root:root:644 ]]
[[ $(stat -c '%U:%G:%a' "$target/dist/index.html") == root:root:644 ]]
if runuser -u nobody -- sh -c "printf changed >> '$target/dist/index.html'" 2>/dev/null; then
  echo 'unexpected write to immutable asset' >&2; exit 1
fi

make_built_release "$bad"
ln -s /etc/passwd "$fixture/built/$bad/dist/leak"
if "$installer" --built-release "$fixture/built/$bad" --site-root "$fixture/site" \
  --releases-root "$fixture/site/releases" --config "$config" --expected-source-sha "$bad" --nginx-user nobody >/dev/null 2>&1; then
  echo 'unexpected symlink release install' >&2; exit 1
fi
[[ ! -e "$fixture/site/releases/$bad" && ! -L "$fixture/site/releases/$bad" ]]
echo 'public release installer fixtures passed'
