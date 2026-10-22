#!/usr/bin/env bash
# Disposable root fixture for the immutable public frontend installer.
set -euo pipefail

root=$(cd "$(dirname "$0")/.." && pwd)
[[ ${EUID:-$(id -u)} == 0 ]] || { echo 'SKIP: requires root for ownership fixtures'; exit 77; }

fixture=$(mktemp -d)
trap 'rm -rf -- "$fixture"' EXIT
chmod 755 "$fixture"
trusted="$fixture/root-libexec"
install -d -o root -g root -m 0755 "$trusted"
install -o root -g root -m 0755 "$root/install-public-release.sh" "$trusted/install-public-release.sh"
install -o root -g root -m 0755 "$root/validate-public-ingress.sh" "$trusted/validate-public-ingress.sh"
installer="$trusted/install-public-release.sh"
validator="$trusted/validate-public-ingress.sh"
config="$root/virtualoffice.axioma-ia.io.conf"

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
manifest=$(sha256sum "$fixture/built/$good/manifest.json" | awk '{print $1}')

"$installer" --built-release "$fixture/built/$good" --site-root "$fixture/site" \
  --releases-root "$fixture/site/releases" --operational-root "$fixture" --config "$config" \
  --validator "$validator" --expected-source-sha "$good" --expected-manifest-sha256 "$manifest" --nginx-user nobody >/dev/null
target="$fixture/site/releases/$good"
[[ $(stat -c '%U:%G:%a' "$target") == root:root:755 ]]
[[ $(stat -c '%U:%G:%a' "$target/manifest.json") == root:root:644 ]]
[[ $(stat -c '%U:%G:%a' "$target/dist/index.html") == root:root:644 ]]
if runuser -u nobody -- sh -c "printf changed >> '$target/dist/index.html'" 2>/dev/null; then
  echo 'unexpected write to immutable asset' >&2; exit 1
fi
if "$installer" --built-release "$fixture/built/$good" --site-root "$fixture/site" \
  --releases-root "$fixture/site/releases" --operational-root "$fixture" --config "$config" \
  --validator "$validator" --expected-source-sha "$good" \
  --expected-manifest-sha256 "0000000000000000000000000000000000000000000000000000000000000000" --nginx-user nobody >/dev/null 2>&1; then
  echo 'unexpected self-attested manifest install' >&2; exit 1
fi

make_built_release "$bad"
ln -s /etc/passwd "$fixture/built/$bad/dist/leak"
if "$installer" --built-release "$fixture/built/$bad" --site-root "$fixture/site" \
  --releases-root "$fixture/site/releases" --operational-root "$fixture" --config "$config" \
  --validator "$validator" --expected-source-sha "$bad" \
  --expected-manifest-sha256 "$(sha256sum "$fixture/built/$bad/manifest.json" | awk '{print $1}')" --nginx-user nobody >/dev/null 2>&1; then
  echo 'unexpected symlink release install' >&2; exit 1
fi
[[ ! -e "$fixture/site/releases/$bad" && ! -L "$fixture/site/releases/$bad" ]]
rm "$fixture/built/$bad/dist/leak"
mkfifo "$fixture/built/$bad/dist/pipe"
if "$installer" --built-release "$fixture/built/$bad" --site-root "$fixture/site" \
  --releases-root "$fixture/site/releases" --operational-root "$fixture" --config "$config" \
  --validator "$validator" --expected-source-sha "$bad" \
  --expected-manifest-sha256 "$(sha256sum "$fixture/built/$bad/manifest.json" | awk '{print $1}')" --nginx-user nobody >/dev/null 2>&1; then
  echo 'unexpected special-file release install' >&2; exit 1
fi
[[ ! -e "$fixture/site/releases/$bad" && ! -L "$fixture/site/releases/$bad" ]]
rm "$fixture/built/$bad/dist/pipe"
chmod g+w "$fixture"
if "$installer" --built-release "$fixture/built/$bad" --site-root "$fixture/site" \
  --releases-root "$fixture/site/releases" --operational-root "$fixture" --config "$config" \
  --validator "$validator" --expected-source-sha "$bad" \
  --expected-manifest-sha256 "$(sha256sum "$fixture/built/$bad/manifest.json" | awk '{print $1}')" --nginx-user nobody >/dev/null 2>&1; then
  echo 'unexpected writable operational ancestor' >&2; exit 1
fi
chmod g-w "$fixture"
echo 'public release installer fixtures passed'
