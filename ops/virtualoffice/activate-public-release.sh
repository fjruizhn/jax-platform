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
original_args=("$@")

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
[[ -x $validator && -x $nginx && -f $source && ! -L $source ]] || die 'validator, nginx, or vhost source is unavailable'
source_mode=$(stat -c %a "$source")
[[ $(stat -c %u "$source") == 0 ]] && (( (8#$source_mode & 8#022) == 0 )) || die 'vhost source is not root-owned and non-writable'
site=$(readlink -f -- "$site"); releases=$(readlink -f -- "$releases"); release=$(readlink -f -- "$release")
case "$release" in "$releases"/*) ;; *) die 'release escapes releases root';; esac
lock="$site/.activation.lock"
locker="$(dirname "$(readlink -f -- "$0")")/with-office-activation-lock.py"
[[ -x $locker ]] || die 'activation lock helper is unavailable'
lock_is_held=0
if [[ -n ${JAX_OFFICE_LOCK_HELD:-} && -e /proc/self/fd/9 ]]; then
python3 - "$lock" "${JAX_OFFICE_LOCK_HELD}" <<'PY' && lock_is_held=1
import os,stat,sys
fd=os.fstat(9); path=os.stat(sys.argv[1]); expected=sys.argv[2]
assert stat.S_ISREG(fd.st_mode) and fd.st_uid == 0 and stat.S_IMODE(fd.st_mode) == 0o600
assert (fd.st_dev,fd.st_ino)==(path.st_dev,path.st_ino)==tuple(map(int,expected.split(':')))
PY
fi
if (( ! lock_is_held )); then exec "$locker" --lock "$lock" -- "$0" "${original_args[@]}"; fi
install -d -o root -g root -m 0700 "$backups"
backup=$(mktemp -d "$backups/office-activation.XXXXXXXX")
chmod 0700 "$backup"; chown root:root "$backup"
staged_source="$backup/vhost-source"
python3 - "$source" "$staged_source" <<'PY' || die 'could not pin trusted vhost source'
import os,stat,sys
src,dst=sys.argv[1:]
fd=os.open(src, os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
try:
 s=os.fstat(fd)
 if not stat.S_ISREG(s.st_mode) or s.st_uid != 0 or stat.S_IMODE(s.st_mode)&0o022: raise SystemExit('unsafe vhost source')
 out=os.open(dst,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
 try:
  while True:
   b=os.read(fd,65536)
   if not b: break
   os.write(out,b)
  os.fsync(out)
 finally: os.close(out)
finally: os.close(fd)
PY
[[ ! -L $staged_source && $(stat -c %u "$staged_source") == 0 ]] || die 'pinned vhost source is unsafe'
"$validator" offline --config "$staged_source" --site-root "$site" --releases-root "$releases" --release "$release" --expected-source-sha "$sha" --nginx-user "$nginx_user"
current="$site/current"
if [[ -e $vhost || -L $vhost ]]; then cp -a -- "$vhost" "$backup/vhost"; sha256sum "$vhost" | awk '{print $1}' > "$backup/previous-vhost-sha256"; else : > "$backup/VHOST_ABSENT"; fi
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
restore_on_error() { local code=$?; if ((changed)); then if ! "$rollback" --lock-held --allow-pre-activation --site-root "$site" --vhost-path "$vhost" --backup "$backup" --nginx "$nginx" --validator "$validator" --public-ip "$ip" --new-index-sha256 "$index"; then : > "$backup/RECOVERY_REQUIRED"; fi; fi; exit "$code"; }
trap restore_on_error ERR INT TERM

[[ -d $(dirname -- "$vhost") && $(stat -c %u "$(dirname -- "$vhost")") == 0 ]] || die 'vhost parent must already exist and be root-owned'
install -o root -g root -m 0644 "$staged_source" "$vhost.new"
sha256sum "$vhost.new" | awk '{print $1}' > "$backup/new-vhost-sha256"
ln -s -- "$release" "$site/.current.new"
chown -h root:root "$site/.current.new"
changed=1
mv -f -- "$vhost.new" "$vhost"
mv -Tf -- "$site/.current.new" "$current"
"$validator" offline --config "$vhost" --site-root "$site" --releases-root "$releases" --release "$current" --expected-source-sha "$sha" --nginx-user "$nginx_user"
"$nginx" -t
"$nginx" -s reload
"$validator" smoke --public-ip "$ip" --expected-index-sha "$index" --asset-path "$asset" --expected-asset-sha "$asset_sha"
trap - ERR INT TERM
printf 'public Office activation passed; rollback snapshot: %s\n' "$backup"
