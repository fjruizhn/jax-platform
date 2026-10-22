#!/usr/bin/env bash
# Disposable proof for activation, automatic restoration, and first-deploy rollback.
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
[[ ${EUID:-$(id -u)} == 0 ]] || { echo 'SKIP: requires root'; exit 77; }
fixture=$(mktemp -d); trap 'rm -rf -- "$fixture"' EXIT
install -d -o root -g root -m 0755 "$fixture/site/releases" "$fixture/vhosts" "$fixture/bin" "$fixture/backups"
install -o root -g root -m 0755 "$root/activate-public-release.sh" "$fixture/bin/activate"
install -o root -g root -m 0755 "$root/rollback-public-release.sh" "$fixture/bin/rollback-public-release.sh"
install -o root -g root -m 0755 "$root/with-office-activation-lock.py" "$fixture/bin/with-office-activation-lock.py"
cat > "$fixture/bin/validator" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "${FAKE_LOG:?}"
[[ ${1:-} == offline || ${1:-} == smoke ]]
[[ ${FAIL_SMOKE:-0} != 1 || ${1:-} != smoke ]]
SH
cat > "$fixture/bin/nginx" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
printf 'nginx %s\n' "$*" >> "${FAKE_LOG:?}"
[[ ${FAIL_NGINX:-0} != 1 ]]
SH
chmod 755 "$fixture/bin/validator" "$fixture/bin/nginx"
make_release(){ local sha=$1 index asset; mkdir -p "$fixture/site/releases/$sha/dist/assets"; printf '%s\n' "$sha" > "$fixture/site/releases/$sha/dist/index.html"; printf x > "$fixture/site/releases/$sha/dist/assets/a.js"; index=$(sha256sum "$fixture/site/releases/$sha/dist/index.html" | awk '{print $1}'); asset=$(sha256sum "$fixture/site/releases/$sha/dist/assets/a.js" | awk '{print $1}'); printf '{"source_sha":"%s","files":{"index.html":"%s","assets/a.js":"%s"}}\n' "$sha" "$index" "$asset" > "$fixture/site/releases/$sha/manifest.json"; chown -R root:root "$fixture/site/releases/$sha"; chmod -R go-w "$fixture/site/releases/$sha"; }
old=1111111111111111111111111111111111111111; new=2222222222222222222222222222222222222222
make_release "$old"; make_release "$new"
printf 'old-vhost\n' > "$fixture/vhosts/office.conf"; ln -s "$fixture/site/releases/$old" "$fixture/site/current"
printf 'new-vhost\n' > "$fixture/template.conf"; chown root:root "$fixture/template.conf"; chmod 644 "$fixture/template.conf"
export FAKE_LOG="$fixture/log"
args=(--site-root "$fixture/site" --releases-root "$fixture/site/releases" --release "$fixture/site/releases/$new" --vhost-source "$fixture/template.conf" --vhost-path "$fixture/vhosts/office.conf" --backup-root "$fixture/backups" --validator "$fixture/bin/validator" --nginx "$fixture/bin/nginx" --expected-source-sha "$new" --public-ip 127.0.0.1 --expected-index-sha aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa --asset-path /assets/a.js --expected-asset-sha bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb)
"$fixture/bin/activate" "${args[@]}" >/dev/null
[[ $(readlink -f "$fixture/site/current") == "$fixture/site/releases/$new" ]]; grep -qx 'new-vhost' "$fixture/vhosts/office.conf"
lock_inode=$(stat -c '%d:%i' "$fixture/site/.activation.lock")
backup=$(find "$fixture/backups" -mindepth 1 -maxdepth 1 -type d | head -1)
"$fixture/bin/rollback-public-release.sh" --site-root "$fixture/site" --vhost-path "$fixture/vhosts/office.conf" --backup "$backup" --nginx "$fixture/bin/nginx" --validator "$fixture/bin/validator" --public-ip 127.0.0.1 --new-index-sha256 aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa >/dev/null
[[ $(readlink -f "$fixture/site/current") == "$fixture/site/releases/$old" ]]; grep -qx 'old-vhost' "$fixture/vhosts/office.conf"
# The lock is persistent and a concurrent command cannot acquire a different inode.
"$fixture/bin/with-office-activation-lock.py" --lock "$fixture/site/.activation.lock" -- /bin/sh -c 'sleep 1' & holder=$!
sleep 0.1
if "$fixture/bin/activate" "${args[@]}" >/dev/null 2>&1; then echo 'concurrent activation unexpectedly ran' >&2; exit 1; fi
wait "$holder"
[[ $(stat -c '%d:%i' "$fixture/site/.activation.lock") == "$lock_inode" ]]
# A stale snapshot cannot overwrite a later state.
if "$fixture/bin/rollback-public-release.sh" --site-root "$fixture/site" --vhost-path "$fixture/vhosts/office.conf" --backup "$backup" --nginx "$fixture/bin/nginx" --validator "$fixture/bin/validator" --public-ip 127.0.0.1 --new-index-sha256 aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa >/dev/null 2>&1; then echo 'stale rollback unexpectedly ran' >&2; exit 1; fi

# A smoke failure after switching must restore the old release automatically.
export FAIL_SMOKE=1
if "$fixture/bin/activate" "${args[@]}" >/dev/null 2>&1; then echo 'activation unexpectedly survived smoke failure' >&2; exit 1; fi
unset FAIL_SMOKE
[[ $(readlink -f "$fixture/site/current") == "$fixture/site/releases/$old" ]]; grep -qx 'old-vhost' "$fixture/vhosts/office.conf"

# First deploy snapshots absence and rollback removes only what activation added.
rm -f "$fixture/site/current" "$fixture/vhosts/office.conf"; : > "$fixture/log"
"$fixture/bin/activate" "${args[@]}" >/dev/null
first=$(find "$fixture/backups" -mindepth 2 -maxdepth 2 -name CURRENT_ABSENT -printf '%h\n')
[[ -f $first/VHOST_ABSENT && -f $first/CURRENT_ABSENT ]]
# curl is deliberately replaced with a no-byte response for this isolated fixture.
cat > "$fixture/bin/curl" <<'SH'
#!/usr/bin/env bash
printf 'previous default host\n'
SH
chmod 755 "$fixture/bin/curl"
PATH="$fixture/bin:$PATH" "$fixture/bin/rollback-public-release.sh" --site-root "$fixture/site" --vhost-path "$fixture/vhosts/office.conf" --backup "$first" --nginx "$fixture/bin/nginx" --validator "$fixture/bin/validator" --public-ip 127.0.0.1 --new-index-sha256 aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa >/dev/null
[[ ! -e $fixture/site/current && ! -e $fixture/vhosts/office.conf ]]
grep -q 'nginx -t' "$fixture/log"; grep -q 'nginx -s reload' "$fixture/log"
echo 'public release activation/rollback fixtures passed'
