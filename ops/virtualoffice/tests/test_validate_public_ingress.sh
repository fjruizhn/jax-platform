#!/usr/bin/env bash
# Root-backed fixtures for the release verifier. Run with sudo in CI/ops.
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
validator="$root/validate-public-ingress.sh"
config="$root/virtualoffice.axioma-ia.io.conf"
[[ ${EUID:-$(id -u)} == 0 ]] || { echo 'SKIP: requires root for ownership fixtures'; exit 0; }

fixture=$(mktemp -d); chmod 755 "$fixture"
sha=1111111111111111111111111111111111111111
mkdir -p "$fixture/releases/$sha/dist/assets"
printf '<html>office</html>\n' > "$fixture/releases/$sha/dist/index.html"
printf 'asset\n' > "$fixture/releases/$sha/dist/assets/index-deadbeef.js"
index=$(sha256sum "$fixture/releases/$sha/dist/index.html" | awk '{print $1}')
asset=$(sha256sum "$fixture/releases/$sha/dist/assets/index-deadbeef.js" | awk '{print $1}')
printf '{"source_sha":"%s","files":{"assets/index-deadbeef.js":"%s","index.html":"%s"}}\n' "$sha" "$asset" "$index" > "$fixture/releases/$sha/manifest.json"
ln -s "$fixture/releases/$sha" "$fixture/current"
chown -R root:root "$fixture"; chown -h root:root "$fixture/current"; chmod -R go-w "$fixture"; chmod 755 "$fixture"

ok() { "$validator" offline --config "$config" --site-root "$fixture" --releases-root "$fixture/releases" --release "$fixture/current" --expected-source-sha "$sha" --nginx-user nobody >/dev/null; }
bad() { if "$@" >/dev/null 2>&1; then echo "unexpected pass: $*" >&2; exit 1; fi; }
ok
bad "$validator" offline --config "$config" --site-root "$fixture" --releases-root "$fixture/releases" --release "$fixture/current" --expected-source-sha 2222222222222222222222222222222222222222 --nginx-user nobody
sed '/location = \/api { return 404; }/i\    location /leak { proxy_pass http://172.16.20.5:8080; }' "$config" > "$fixture/leak.conf"
bad "$validator" offline --config "$fixture/leak.conf" --site-root "$fixture" --releases-root "$fixture/releases" --release "$fixture/current" --expected-source-sha "$sha" --nginx-user nobody
chmod g+w "$fixture/releases/$sha/dist"; bad ok; chmod g-w "$fixture/releases/$sha/dist"
chmod o+w "$fixture"; bad ok; chmod o-w "$fixture"
mkdir -p "$fixture/outside/2222222222222222222222222222222222222222"; ln -s "$fixture/outside/2222222222222222222222222222222222222222" "$fixture/escape"
bad "$validator" offline --config "$config" --site-root "$fixture" --releases-root "$fixture/releases" --release "$fixture/escape" --expected-source-sha 2222222222222222222222222222222222222222 --nginx-user nobody
ln -s /etc/passwd "$fixture/releases/$sha/manifest-link"; bad ok
printf tamper >> "$fixture/releases/$sha/dist/index.html"; bad ok
echo 'release verifier fixtures passed'
