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
if [[ ${1:-} == offline && -n ${MUTATE_SOURCE:-} ]]; then printf 'replaced-after-open\n' > "$MUTATE_SOURCE"; fi
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
export MUTATE_SOURCE="$fixture/template.conf"
"$fixture/bin/activate" "${args[@]}" >/dev/null
unset MUTATE_SOURCE
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
out= status=${FAKE_HTTP_STATUS:-200}
while (($#)); do case $1 in --output) out=$2; shift 2;; *) shift;; esac; done
[[ -n $out ]] && printf 'previous default host\n' > "$out"
printf '%s' "$status"
SH
chmod 755 "$fixture/bin/curl"
export FAKE_HTTP_STATUS=500
if PATH="$fixture/bin:$PATH" "$fixture/bin/rollback-public-release.sh" --site-root "$fixture/site" --vhost-path "$fixture/vhosts/office.conf" --backup "$first" --nginx "$fixture/bin/nginx" --validator "$fixture/bin/validator" --public-ip 127.0.0.1 --new-index-sha256 aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa >/dev/null 2>&1; then echo '500 rollback verification unexpectedly passed' >&2; exit 1; fi
unset FAKE_HTTP_STATUS
# Recreate the absent state so the accepted closed/default response is also proved.
"$fixture/bin/activate" "${args[@]}" >/dev/null
first=$(find "$fixture/backups" -mindepth 2 -maxdepth 2 -name CURRENT_ABSENT -printf '%h\n' | tail -1)
PATH="$fixture/bin:$PATH" "$fixture/bin/rollback-public-release.sh" --site-root "$fixture/site" --vhost-path "$fixture/vhosts/office.conf" --backup "$first" --nginx "$fixture/bin/nginx" --validator "$fixture/bin/validator" --public-ip 127.0.0.1 --new-index-sha256 aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa >/dev/null
[[ ! -e $fixture/site/current && ! -e $fixture/vhosts/office.conf ]]
grep -q 'nginx -t' "$fixture/log"; grep -q 'nginx -s reload' "$fixture/log"

# Kill after the vhost mv but before current mv: only the durable marker may
# authorize recovery, and a foreign vhost must never be overwritten.
ln -s "$fixture/site/releases/$old" "$fixture/site/current"
printf 'old-vhost\n' > "$fixture/vhosts/office.conf"
printf 'new-vhost\n' > "$fixture/template.conf"; chown root:root "$fixture/template.conf"; chmod 644 "$fixture/template.conf"
mkdir "$fixture/kill-bin"
cat > "$fixture/kill-bin/mv" <<'SH'
#!/usr/bin/env bash
/bin/mv "$@"
src=${@: -2:1}
[[ $src == *office.conf.new ]] && kill -KILL "$PPID"
SH
chmod 755 "$fixture/kill-bin/mv"
if PATH="$fixture/kill-bin:$PATH" "$fixture/bin/activate" "${args[@]}" >/dev/null 2>&1; then echo 'activation unexpectedly survived SIGKILL hook' >&2; exit 1; fi
killed=$(find "$fixture/backups" -mindepth 2 -maxdepth 2 -name ACTIVATION_IN_PROGRESS -printf '%h\n' | tail -1)
[[ -f $killed/ACTIVATION_IN_PROGRESS ]]
[[ $(readlink -f "$fixture/site/current") == "$fixture/site/releases/$old" ]]; grep -qx 'new-vhost' "$fixture/vhosts/office.conf"
cp "$killed/vhost-source" "$fixture/exact-new-vhost"
rm "$fixture/site/current"; : > "$fixture/site/current"
if "$fixture/bin/rollback-public-release.sh" --recover-incomplete --site-root "$fixture/site" --vhost-path "$fixture/vhosts/office.conf" --backup "$killed" --nginx "$fixture/bin/nginx" --validator "$fixture/bin/validator" --public-ip 127.0.0.1 --new-index-sha256 aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa >/dev/null 2>&1; then echo 'recovery accepted regular current' >&2; exit 1; fi
rm "$fixture/site/current"; ln -s "$fixture/site/releases/missing" "$fixture/site/current"
if "$fixture/bin/rollback-public-release.sh" --recover-incomplete --site-root "$fixture/site" --vhost-path "$fixture/vhosts/office.conf" --backup "$killed" --nginx "$fixture/bin/nginx" --validator "$fixture/bin/validator" --public-ip 127.0.0.1 --new-index-sha256 aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa >/dev/null 2>&1; then echo 'recovery accepted dangling current' >&2; exit 1; fi
rm "$fixture/site/current"; ln -s "$fixture/site/releases/$old" "$fixture/site/current"
rm "$fixture/vhosts/office.conf"
if "$fixture/bin/rollback-public-release.sh" --recover-incomplete --site-root "$fixture/site" --vhost-path "$fixture/vhosts/office.conf" --backup "$killed" --nginx "$fixture/bin/nginx" --validator "$fixture/bin/validator" --public-ip 127.0.0.1 --new-index-sha256 aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa >/dev/null 2>&1; then echo 'recovery accepted missing prior vhost' >&2; exit 1; fi
ln -s "$fixture/vhosts/missing" "$fixture/vhosts/office.conf"
if "$fixture/bin/rollback-public-release.sh" --recover-incomplete --site-root "$fixture/site" --vhost-path "$fixture/vhosts/office.conf" --backup "$killed" --nginx "$fixture/bin/nginx" --validator "$fixture/bin/validator" --public-ip 127.0.0.1 --new-index-sha256 aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa >/dev/null 2>&1; then echo 'recovery accepted dangling vhost' >&2; exit 1; fi
rm "$fixture/vhosts/office.conf"; ln -s "$fixture/exact-new-vhost" "$fixture/vhosts/office.conf"
if "$fixture/bin/rollback-public-release.sh" --recover-incomplete --site-root "$fixture/site" --vhost-path "$fixture/vhosts/office.conf" --backup "$killed" --nginx "$fixture/bin/nginx" --validator "$fixture/bin/validator" --public-ip 127.0.0.1 --new-index-sha256 aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa >/dev/null 2>&1; then echo 'recovery accepted matching symlink vhost' >&2; exit 1; fi
rm "$fixture/vhosts/office.conf"
cp "$fixture/exact-new-vhost" "$fixture/vhosts/office.conf"
printf 'foreign-vhost\n' > "$fixture/vhosts/office.conf"
if "$fixture/bin/rollback-public-release.sh" --recover-incomplete --site-root "$fixture/site" --vhost-path "$fixture/vhosts/office.conf" --backup "$killed" --nginx "$fixture/bin/nginx" --validator "$fixture/bin/validator" --public-ip 127.0.0.1 --new-index-sha256 aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa >/dev/null 2>&1; then echo 'recovery overwrote foreign vhost' >&2; exit 1; fi
grep -qx 'foreign-vhost' "$fixture/vhosts/office.conf"
cp "$fixture/exact-new-vhost" "$fixture/vhosts/office.conf"
"$fixture/bin/rollback-public-release.sh" --recover-incomplete --site-root "$fixture/site" --vhost-path "$fixture/vhosts/office.conf" --backup "$killed" --nginx "$fixture/bin/nginx" --validator "$fixture/bin/validator" --public-ip 127.0.0.1 --new-index-sha256 aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa >/dev/null
[[ $(readlink -f "$fixture/site/current") == "$fixture/site/releases/$old" ]]; grep -qx 'old-vhost' "$fixture/vhosts/office.conf"; [[ ! -f $killed/ACTIVATION_IN_PROGRESS ]]

# A manual rollback killed after vhost restoration must be resumed only from its
# durable rollback marker; a foreign current state remains untouched.
printf 'new-vhost\n' > "$fixture/template.conf"; chown root:root "$fixture/template.conf"; chmod 644 "$fixture/template.conf"
activation_output=$("$fixture/bin/activate" "${args[@]}")
rollback_snapshot=$(awk '/activation snapshot/{print $NF}' <<<"$activation_output")
[[ -d $rollback_snapshot ]]
if PATH="$fixture/kill-bin:$PATH" "$fixture/bin/rollback-public-release.sh" --site-root "$fixture/site" --vhost-path "$fixture/vhosts/office.conf" --backup "$rollback_snapshot" --nginx "$fixture/bin/nginx" --validator "$fixture/bin/validator" --public-ip 127.0.0.1 --new-index-sha256 aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa >/dev/null 2>&1; then echo 'rollback unexpectedly survived SIGKILL hook' >&2; exit 1; fi
[[ -f $rollback_snapshot/ROLLBACK_IN_PROGRESS ]]
[[ $(readlink -f "$fixture/site/current") == "$fixture/site/releases/$new" ]]; grep -qx 'old-vhost' "$fixture/vhosts/office.conf"
rm "$fixture/site/current"; : > "$fixture/site/current"
if "$fixture/bin/rollback-public-release.sh" --recover-rollback --site-root "$fixture/site" --vhost-path "$fixture/vhosts/office.conf" --backup "$rollback_snapshot" --nginx "$fixture/bin/nginx" --validator "$fixture/bin/validator" --public-ip 127.0.0.1 --new-index-sha256 aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa >/dev/null 2>&1; then echo 'rollback recovery accepted foreign current' >&2; exit 1; fi
[[ -f $fixture/site/current ]]; grep -qx 'old-vhost' "$fixture/vhosts/office.conf"
rm "$fixture/site/current"; ln -s "$fixture/site/releases/$new" "$fixture/site/current"
"$fixture/bin/rollback-public-release.sh" --recover-rollback --site-root "$fixture/site" --vhost-path "$fixture/vhosts/office.conf" --backup "$rollback_snapshot" --nginx "$fixture/bin/nginx" --validator "$fixture/bin/validator" --public-ip 127.0.0.1 --new-index-sha256 aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa >/dev/null
[[ $(readlink -f "$fixture/site/current") == "$fixture/site/releases/$old" ]]; grep -qx 'old-vhost' "$fixture/vhosts/office.conf"; [[ ! -f $rollback_snapshot/ROLLBACK_IN_PROGRESS ]]

# First deploy has no prior vhost: killing rollback after removal still resumes.
rm "$fixture/site/current" "$fixture/vhosts/office.conf"
printf 'new-vhost\n' > "$fixture/template.conf"; chown root:root "$fixture/template.conf"; chmod 644 "$fixture/template.conf"
first_output=$("$fixture/bin/activate" "${args[@]}")
first_rollback=$(awk '/activation snapshot/{print $NF}' <<<"$first_output")
cat > "$fixture/kill-bin/rm" <<'SH'
#!/usr/bin/env bash
/bin/rm "$@"
target=${@: -1}
[[ $target == *office.conf ]] && kill -KILL "$PPID"
SH
chmod 755 "$fixture/kill-bin/rm"
if PATH="$fixture/kill-bin:$PATH" "$fixture/bin/rollback-public-release.sh" --site-root "$fixture/site" --vhost-path "$fixture/vhosts/office.conf" --backup "$first_rollback" --nginx "$fixture/bin/nginx" --validator "$fixture/bin/validator" --public-ip 127.0.0.1 --new-index-sha256 aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa >/dev/null 2>&1; then echo 'first rollback unexpectedly survived SIGKILL hook' >&2; exit 1; fi
[[ -f $first_rollback/ROLLBACK_IN_PROGRESS && ! -e $fixture/vhosts/office.conf && -L $fixture/site/current ]]
PATH="$fixture/bin:$PATH" "$fixture/bin/rollback-public-release.sh" --recover-rollback --site-root "$fixture/site" --vhost-path "$fixture/vhosts/office.conf" --backup "$first_rollback" --nginx "$fixture/bin/nginx" --validator "$fixture/bin/validator" --public-ip 127.0.0.1 --new-index-sha256 aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa >/dev/null
[[ ! -e $fixture/site/current && ! -e $fixture/vhosts/office.conf && ! -f $first_rollback/ROLLBACK_IN_PROGRESS ]]
echo 'public release activation/rollback fixtures passed'
