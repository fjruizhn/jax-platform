#!/usr/bin/env bash
# Root-backed fixtures for the release verifier. Run with sudo in CI/ops.
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
validator="$root/validate-public-ingress.sh"
config="$root/virtualoffice.axioma-ia.io.conf"
[[ ${EUID:-$(id -u)} == 0 ]] || { echo 'SKIP: requires root for ownership fixtures'; exit 77; }

fixture=$(mktemp -d); chmod 755 "$fixture"
trap 'rm -rf -- "$fixture"' EXIT
sha=1111111111111111111111111111111111111111
mkdir -p "$fixture/releases/$sha/dist/assets"
printf '<html>office</html>\n' > "$fixture/releases/$sha/dist/index.html"
printf 'asset\n' > "$fixture/releases/$sha/dist/assets/index-deadbeef.js"
index=$(sha256sum "$fixture/releases/$sha/dist/index.html" | awk '{print $1}')
asset=$(sha256sum "$fixture/releases/$sha/dist/assets/index-deadbeef.js" | awk '{print $1}')
printf '{"source_sha":"%s","files":{"assets/index-deadbeef.js":"%s","index.html":"%s"}}\n' "$sha" "$asset" "$index" > "$fixture/releases/$sha/manifest.json"
ln -s "$fixture/releases/$sha" "$fixture/current"
chown -R root:root "$fixture"; chown -h root:root "$fixture/current"; chmod -R go-w "$fixture"; chmod 755 "$fixture"
config="$fixture/virtualoffice.axioma-ia.io.conf"
sed "s|    root /opt/jax-office/virtualoffice.axioma-ia.io/current/dist;|    root $fixture/current/dist;|" "$root/virtualoffice.axioma-ia.io.conf" > "$config"
chown root:root "$config"; chmod 644 "$config"

ok() { "$validator" offline --config "$config" --site-root "$fixture" --releases-root "$fixture/releases" --release "$fixture/current" --expected-source-sha "$sha" --nginx-root "$fixture/current/dist" --nginx-user nobody >/dev/null; }
bad() { if "$@" >/dev/null 2>&1; then echo "unexpected pass: $*" >&2; exit 1; fi; }
ok
bad "$validator" offline --config "$config" --site-root "$fixture" --releases-root "$fixture/releases" --release "$fixture/current" --expected-source-sha 2222222222222222222222222222222222222222 --nginx-root "$fixture/current/dist" --nginx-user nobody
bad "$validator" offline --config "$root/virtualoffice.axioma-ia.io.conf" --site-root "$fixture" --releases-root "$fixture/releases" --release "$fixture/current" --expected-source-sha "$sha" --nginx-root "$fixture/current/dist" --nginx-user nobody
sed '/location = \/api { return 404; }/i\    location /leak { proxy_pass http://172.16.20.5:8080; }' "$config" > "$fixture/leak.conf"
bad "$validator" offline --config "$fixture/leak.conf" --site-root "$fixture" --releases-root "$fixture/releases" --release "$fixture/current" --expected-source-sha "$sha" --nginx-root "$fixture/current/dist" --nginx-user nobody
chmod g+w "$fixture/releases/$sha/dist"; bad ok; chmod g-w "$fixture/releases/$sha/dist"
chmod o+w "$fixture"; bad ok; chmod o-w "$fixture"
outside=2222222222222222222222222222222222222222
mkdir -p "$fixture/outside/$outside/dist"
printf '<html>external</html>\n' > "$fixture/outside/$outside/dist/index.html"
outside_hash=$(sha256sum "$fixture/outside/$outside/dist/index.html" | awk '{print $1}')
printf '{"source_sha":"%s","files":{"index.html":"%s"}}\n' "$outside" "$outside_hash" > "$fixture/outside/$outside/manifest.json"
chown -R root:root "$fixture/outside"; chmod -R go-w "$fixture/outside"
ln -s "$fixture/outside/$outside" "$fixture/escape"
bad "$validator" offline --config "$config" --site-root "$fixture" --releases-root "$fixture/releases" --release "$fixture/escape" --expected-source-sha "$outside" --nginx-root "$fixture/current/dist" --nginx-user nobody
ln -s /etc/passwd "$fixture/releases/$sha/manifest-link"; bad ok
unlink "$fixture/releases/$sha/manifest-link"
printf tamper >> "$fixture/releases/$sha/dist/index.html"; bad ok
echo 'release verifier fixtures passed'
