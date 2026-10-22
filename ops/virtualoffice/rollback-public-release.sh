#!/usr/bin/env bash
# Restore the exact vhost/current state captured by activate-public-release.sh.
set -euo pipefail
usage(){ echo 'Usage: rollback-public-release.sh [--allow-pre-activation] [--lock-held] --site-root DIR --vhost-path FILE --backup DIR --nginx FILE --validator FILE --public-ip IP --new-index-sha256 SHA256'; }
die(){ printf 'ERROR: %s\n' "$*" >&2; exit 1; }; need(){ [[ $# == 2 && -n $2 ]] || die "missing value for $1"; }
[[ ${EUID:-$(id -u)} == 0 ]] || die 'must run as root'
site= vhost= backup= nginx= validator= ip= new_index= allow_pre_activation=0 lock_held=0
while (($#)); do case $1 in
 --allow-pre-activation) allow_pre_activation=1; shift;;
 --lock-held) lock_held=1; shift;;
 --site-root) need "$1" "${2:-}"; site=$2; shift 2;; --vhost-path) need "$1" "${2:-}"; vhost=$2; shift 2;; --backup) need "$1" "${2:-}"; backup=$2; shift 2;; --nginx) need "$1" "${2:-}"; nginx=$2; shift 2;; --validator) need "$1" "${2:-}"; validator=$2; shift 2;; --public-ip) need "$1" "${2:-}"; ip=$2; shift 2;; --new-index-sha256) need "$1" "${2:-}"; new_index=$2; shift 2;; -h|--help) usage; exit 0;; *) die "unknown argument: $1";; esac; done
[[ -n $site && -n $vhost && -n $backup && -n $nginx && -n $validator && -n $ip && $new_index =~ ^[0-9a-f]{64}$ ]] || { usage >&2; exit 2; }
[[ -d $backup && -x $nginx && -x $validator ]] || die 'backup, nginx, or validator unavailable'
site=$(readlink -f -- "$site"); current="$site/current"
lock="$site/.activation.lock"
[[ ! -e $lock || ( $(stat -c %u "$lock") == 0 && $(stat -c %a "$lock") == 600 ) ]] || die 'activation lock is unsafe'
install -o root -g root -m 0600 /dev/null "$lock"
if (( ! lock_held )); then exec 9>"$lock"; flock -n 9 || die 'another Office activation or rollback is running'; fi
new_release=$(cat "$backup/new-release")
[[ -d $new_release ]] || die 'recorded new release disappeared'
if [[ -L $current ]]; then current_target=$(readlink -f -- "$current"); else current_target=ABSENT; fi
if [[ $current_target != "$new_release" ]]; then
  if (( ! allow_pre_activation )); then die 'current no longer names this activation release'; fi
  if [[ -f $backup/current-target ]]; then [[ $current_target == "$(cat "$backup/current-target")" ]] || die 'current changed before activation rollback'; else [[ $current_target == ABSENT ]] || die 'current changed before activation rollback'; fi
fi
if [[ -f $backup/vhost ]]; then install -o root -g root -m 0644 "$backup/vhost" "$vhost.new"; mv -f -- "$vhost.new" "$vhost"; elif [[ -f $backup/VHOST_ABSENT ]]; then rm -f -- "$vhost"; else die 'backup has no vhost state'; fi
if [[ -f $backup/current-target ]]; then target=$(cat "$backup/current-target"); [[ -d $target ]] || die 'previous release disappeared'; ln -s -- "$target" "$site/.current.rollback"; chown -h root:root "$site/.current.rollback"; mv -Tf -- "$site/.current.rollback" "$current"; elif [[ -f $backup/CURRENT_ABSENT ]]; then rm -f -- "$current"; else die 'backup has no current state'; fi
"$nginx" -t
"$nginx" -s reload
if [[ -f $backup/CURRENT_ABSENT ]]; then
  # The prior host must no longer return the newly activated bytes.
  body=$(mktemp "$backup/rollback-body.XXXXXXXX")
  curl --noproxy '*' -k --silent --show-error --max-time 10 --resolve "virtualoffice.axioma-ia.io:443:$ip" https://virtualoffice.axioma-ia.io/ > "$body" || die 'first-deploy rollback HTTP verification failed'
  [[ -s $body ]] || die 'first-deploy rollback HTTP response was empty'
  got=$(sha256sum "$body" | awk '{print $1}')
  rm -f -- "$body"
  [[ $got != "$new_index" ]] || die 'first-deploy rollback still serves the activated index'
else
  readarray -t prior < <(python3 - "$backup/previous-manifest.json" <<'PY'
import json,sys
m=json.load(open(sys.argv[1])); a=sorted(k for k in m['files'] if k.startswith('assets/'))[0]
print(m['source_sha']); print(m['files']['index.html']); print('/'+a); print(m['files'][a])
PY
)
  "$validator" smoke --public-ip "$ip" --expected-index-sha "${prior[1]}" --asset-path "${prior[2]}" --expected-asset-sha "${prior[3]}"
fi
printf 'public Office rollback passed from snapshot: %s\n' "$backup"
