#!/usr/bin/env bash
# Atomically activate an attested Office release and restore the prior state on failure.
set -euo pipefail

usage() { cat <<'USAGE'
Usage: activate-public-release.sh --site-root DIR --releases-root DIR --release DIR --vhost-source FILE --vhost-path FILE --backup-root DIR --validator FILE --nginx FILE --expected-source-sha SHA --public-ip IP --expected-index-sha SHA256 --asset-path /assets/FILE --expected-asset-sha SHA256 [--nginx-user USER]
USAGE
}
die(){ printf 'ERROR: %s\n' "$*" >&2; exit 1; }
need(){ [[ $# == 2 && -n $2 ]] || die "missing value for $1"; }
[[ ${EUID:-$(id -u)} == 0 ]] || die 'must run as root'

site= releases= release= source= vhost= backups= validator= nginx= sha= ip= index= asset= asset_sha= nginx_user=www
while (($#)); do case $1 in
  --site-root) need "$1" "${2:-}"; site=$2; shift 2;; --releases-root) need "$1" "${2:-}"; releases=$2; shift 2;;
  --release) need "$1" "${2:-}"; release=$2; shift 2;; --vhost-source) need "$1" "${2:-}"; source=$2; shift 2;;
  --vhost-path) need "$1" "${2:-}"; vhost=$2; shift 2;; --backup-root) need "$1" "${2:-}"; backups=$2; shift 2;;
  --validator) need "$1" "${2:-}"; validator=$2; shift 2;; --nginx) need "$1" "${2:-}"; nginx=$2; shift 2;;
  --expected-source-sha) need "$1" "${2:-}"; sha=$2; shift 2;; --public-ip) need "$1" "${2:-}"; ip=$2; shift 2;;
  --expected-index-sha) need "$1" "${2:-}"; index=$2; shift 2;; --asset-path) need "$1" "${2:-}"; asset=$2; shift 2;;
  --expected-asset-sha) need "$1" "${2:-}"; asset_sha=$2; shift 2;; --nginx-user) need "$1" "${2:-}"; nginx_user=$2; shift 2;;
  -h|--help) usage; exit 0;; *) die "unknown argument: $1";; esac; done
[[ -n $site && -n $releases && -n $release && -n $source && -n $vhost && -n $backups && -n $validator && -n $nginx && -n $sha && -n $ip && -n $index && -n $asset && -n $asset_sha ]] || { usage >&2; exit 2; }
[[ $sha =~ ^[0-9a-f]{40}$ && $index =~ ^[0-9a-f]{64}$ && $asset_sha =~ ^[0-9a-f]{64}$ ]] || die 'invalid digest'
[[ -x $validator && -x $nginx && -f $source ]] || die 'validator, nginx, or vhost source is unavailable'
site=$(readlink -f -- "$site"); releases=$(readlink -f -- "$releases"); release=$(readlink -f -- "$release")
case "$release" in "$releases"/*) ;; *) die 'release escapes releases root';; esac
"$validator" offline --config "$source" --site-root "$site" --releases-root "$releases" --release "$release" --expected-source-sha "$sha" --nginx-user "$nginx_user"

install -d -o root -g root -m 0700 "$backups"
backup=$(mktemp -d "$backups/office-activation.XXXXXXXX")
chmod 0700 "$backup"; chown root:root "$backup"
current="$site/current"
if [[ -e $vhost || -L $vhost ]]; then cp -a -- "$vhost" "$backup/vhost"; else : > "$backup/VHOST_ABSENT"; fi
if [[ -e $current || -L $current ]]; then
  [[ -L $current ]] || die 'existing current is not a symlink'
  readlink -f -- "$current" > "$backup/current-target"
  cp -a -- "$(readlink -f -- "$current")/manifest.json" "$backup/previous-manifest.json"
else : > "$backup/CURRENT_ABSENT"; fi
printf '%s\n' "$release" > "$backup/new-release"
printf '%s\n' "$index" > "$backup/new-index-sha256"

rollback="$(dirname "$(readlink -f -- "$0")")/rollback-public-release.sh"
[[ -x $rollback ]] || die 'rollback executable is unavailable'
changed=0
restore_on_error() { local code=$?; if ((changed)); then "$rollback" --site-root "$site" --vhost-path "$vhost" --backup "$backup" --nginx "$nginx" --validator "$validator" --public-ip "$ip" --new-index-sha256 "$index" || true; fi; exit "$code"; }
trap restore_on_error ERR INT TERM

install -d -o root -g root -m 0755 "$(dirname -- "$vhost")"
install -o root -g root -m 0644 "$source" "$vhost.new"
ln -s -- "$release" "$site/.current.new"
chown -h root:root "$site/.current.new"
mv -f -- "$vhost.new" "$vhost"
mv -Tf -- "$site/.current.new" "$current"
changed=1
"$validator" offline --config "$vhost" --site-root "$site" --releases-root "$releases" --release "$current" --expected-source-sha "$sha" --nginx-user "$nginx_user"
"$nginx" -t
"$nginx" -s reload
"$validator" smoke --public-ip "$ip" --expected-index-sha "$index" --asset-path "$asset" --expected-asset-sha "$asset_sha"
trap - ERR INT TERM
printf 'public Office activation passed; rollback snapshot: %s\n' "$backup"
