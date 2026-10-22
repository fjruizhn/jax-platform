#!/usr/bin/env bash
# Install one already-built Virtual Office frontend release without changing current.
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: install-public-release.sh --built-release DIR --site-root DIR --releases-root DIR \
  --operational-root DIR --config FILE --validator FILE --expected-source-sha SHA \
  --expected-manifest-sha256 SHA256 [--nginx-user USER]

The caller builds DIR with ops/frontend/build.py from the independently audited
SHA. This command only creates releases/SHA; it never changes current or nginx.
USAGE
}

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
need() { [[ $# -eq 2 && -n $2 ]] || die "missing value for $1"; }

[[ ${EUID:-$(id -u)} == 0 ]] || die 'must run as root to create an immutable release'

built_release= site_root= releases_root= operational_root= config= validator= expected_source_sha= expected_manifest_sha256= nginx_user=www
while [[ $# -gt 0 ]]; do
  case $1 in
    --built-release) need "$1" "${2:-}"; built_release=$2; shift 2 ;;
    --site-root) need "$1" "${2:-}"; site_root=$2; shift 2 ;;
    --releases-root) need "$1" "${2:-}"; releases_root=$2; shift 2 ;;
    --operational-root) need "$1" "${2:-}"; operational_root=$2; shift 2 ;;
    --config) need "$1" "${2:-}"; config=$2; shift 2 ;;
    --validator) need "$1" "${2:-}"; validator=$2; shift 2 ;;
    --expected-source-sha) need "$1" "${2:-}"; expected_source_sha=$2; shift 2 ;;
    --expected-manifest-sha256) need "$1" "${2:-}"; expected_manifest_sha256=$2; shift 2 ;;
    --nginx-user) need "$1" "${2:-}"; nginx_user=$2; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

[[ -n $built_release && -n $site_root && -n $releases_root && -n $operational_root && -n $config && -n $validator ]] || { usage >&2; exit 2; }
[[ $expected_source_sha =~ ^[0-9a-f]{40}$ ]] || die '--expected-source-sha must be a full lowercase Git SHA'
[[ $expected_manifest_sha256 =~ ^[0-9a-f]{64}$ ]] || die '--expected-manifest-sha256 must be a lowercase SHA-256'
[[ -d $built_release && -f $built_release/manifest.json && -d $built_release/dist ]] || die 'built release needs manifest.json and dist/'
[[ ! -L $built_release && ! -L $built_release/manifest.json && ! -L $built_release/dist ]] || die 'built release contains a top-level symlink'
find -P "$built_release" -type l -print -quit | grep -q . && die 'built release contains a symlink'
find -P "$built_release" \( -type b -o -type c -o -type p -o -type s \) -print -quit | grep -q . && die 'built release contains a special file'
[[ $(sha256sum "$built_release/manifest.json" | awk '{print $1}') == "$expected_manifest_sha256" ]] || die 'built release manifest differs from independently audited manifest digest'

site_root=$(readlink -f -- "$site_root") || die 'site root cannot be resolved'
[[ -d $site_root ]] || die "site root is not a directory: $site_root"
operational_root=$(readlink -f -- "$operational_root") || die 'operational root cannot be resolved'
[[ -d $operational_root ]] || die "operational root is not a directory: $operational_root"
case "$site_root" in "$operational_root"/*) ;; *) die 'site root escapes operational root' ;; esac
releases_root=$(readlink -f -- "$releases_root") || die 'releases root cannot be resolved'
case "$releases_root" in "$site_root"/*) ;; *) die 'releases root escapes site root' ;; esac
[[ -f $config ]] || die "config does not exist: $config"
validator=$(readlink -f -- "$validator") || die 'validator cannot be resolved'
[[ -x $validator && ! -L $validator ]] || die 'validator must be a regular executable'

verify_root_owned_path() {
  local path=$1 label=$2 uid mode
  uid=$(stat -c %u "$path"); mode=$(stat -c %a "$path")
  [[ $uid == 0 ]] || die "$label is not owned by root: $path"
  (( (8#$mode & 8#022) == 0 )) || die "$label is group/other writable: $path"
}
verify_operational_chain() {
  local path=$1 label=$2
  while :; do
    verify_root_owned_path "$path" "$label"
    [[ $path == "$operational_root" ]] && return
    path=$(dirname -- "$path")
    [[ $path == / ]] && die "$label escapes operational root"
  done
}
self=$(readlink -f -- "$0") || die 'installer cannot be resolved'
verify_root_owned_path "$self" 'installer'
verify_root_owned_path "$validator" 'validator'
verify_operational_chain "$site_root" 'site parent'
verify_operational_chain "$releases_root" 'releases parent'

source_sha=$(python3 - "$built_release/manifest.json" <<'PY'
import json,re,sys
try:
    manifest=json.load(open(sys.argv[1], encoding='utf-8'))
except (OSError, ValueError) as error:
    raise SystemExit(f'invalid manifest: {error}')
sha=manifest.get('source_sha')
if not isinstance(sha, str) or not re.fullmatch(r'[0-9a-f]{40}', sha):
    raise SystemExit('manifest source_sha is not a full lowercase Git SHA')
print(sha)
PY
) || die 'could not read source SHA from manifest'
[[ $source_sha == "$expected_source_sha" ]] || die 'built release source SHA differs from independently audited SHA'
[[ $(basename -- "$built_release") == "$source_sha" ]] || die 'built release directory name does not match manifest source_sha'

install -d -o root -g root -m 0755 "$site_root" "$releases_root"
target="$releases_root/$source_sha"
stage="$releases_root/.$source_sha.new"
[[ ! -e $target && ! -L $target ]] || die "immutable release already exists: $target"
[[ ! -e $stage && ! -L $stage ]] || die "staging path already exists: $stage"

cleanup() { [[ -d ${stage:-} ]] && rm -rf -- "$stage"; }
trap cleanup EXIT
install -d -o root -g root -m 0755 "$stage/dist"
cp -a --no-preserve=mode,ownership "$built_release/dist/." "$stage/dist/"
install -o root -g root -m 0644 "$built_release/manifest.json" "$stage/manifest.json"
chown -R root:root "$stage"
find -P "$stage" -type d -exec chmod 0755 {} +
find -P "$stage" -type f -exec chmod 0644 {} +
find -P "$stage" -type l -print -quit | grep -q . && die 'copied release contains a symlink'
find -P "$stage" \( -type b -o -type c -o -type p -o -type s \) -print -quit | grep -q . && die 'copied release contains a special file'

verify_operational_chain "$releases_root" 'releases parent'
mv -- "$stage" "$target"
stage=
verify_operational_chain "$site_root" 'site parent'
verify_operational_chain "$releases_root" 'releases parent'
[[ $(sha256sum "$target/manifest.json" | awk '{print $1}') == "$expected_manifest_sha256" ]] || die 'installed manifest differs from independently audited manifest digest'
"$validator" offline \
  --config "$config" --site-root "$site_root" --releases-root "$releases_root" \
  --release "$target" --expected-source-sha "$expected_source_sha" \
  --nginx-root "$site_root/current/dist" --nginx-user "$nginx_user"
trap - EXIT
printf 'immutable public release installed: %s\n' "$target"
