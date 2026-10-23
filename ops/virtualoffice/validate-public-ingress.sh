#!/usr/bin/env bash
# Offline and pre-DNS verification for the Virtual Office public ingress.
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  validate-public-ingress.sh offline --config FILE --site-root DIR --releases-root DIR --release DIR_OR_CURRENT --expected-source-sha SHA --nginx-root DIR [--nginx-user USER]
  validate-public-ingress.sh syntax --nginx NGINX --config COMPLETE_NGINX_CONF
  validate-public-ingress.sh smoke --public-ip IP --expected-index-sha SHA256 --asset-path /assets/FILE --expected-asset-sha SHA256
USAGE
}
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
need() { [[ $# -eq 2 && -n $2 ]] || die "missing value for $1"; }
has_header() { printf '%s\n' "$1" | tr -d '\r' | grep -Eiq "^$2: *$3$"; }

cmd=${1:-}; shift || true
config= release= site_root= releases_root= nginx= nginx_root= nginx_user=www public_ip= expected_index_sha= expected_source_sha= expected_asset_sha= asset_path=
while [[ $# -gt 0 ]]; do
  case $1 in
    --config) need "$1" "${2:-}"; config=$2; shift 2 ;;
    --release) need "$1" "${2:-}"; release=$2; shift 2 ;;
    --site-root) need "$1" "${2:-}"; site_root=$2; shift 2 ;;
    --releases-root) need "$1" "${2:-}"; releases_root=$2; shift 2 ;;
    --nginx) need "$1" "${2:-}"; nginx=$2; shift 2 ;;
    --nginx-root) need "$1" "${2:-}"; nginx_root=$2; shift 2 ;;
    --nginx-user) need "$1" "${2:-}"; nginx_user=$2; shift 2 ;;
    --public-ip) need "$1" "${2:-}"; public_ip=$2; shift 2 ;;
    --expected-index-sha) need "$1" "${2:-}"; expected_index_sha=$2; shift 2 ;;
    --expected-source-sha) need "$1" "${2:-}"; expected_source_sha=$2; shift 2 ;;
    --expected-asset-sha) need "$1" "${2:-}"; expected_asset_sha=$2; shift 2 ;;
    --asset-path) need "$1" "${2:-}"; asset_path=$2; shift 2 ;;
    *) die "unknown argument: $1" ;;
  esac
done

host=virtualoffice.axioma-ia.io
headers=('X-Content-Type-Options: nosniff' 'Referrer-Policy: same-origin' 'X-Frame-Options: DENY' 'Strict-Transport-Security: max-age=31536000')

verify_manifest() {
  local target=$1
  [[ -d $target/dist && -f $target/manifest.json ]] || die "release needs dist/ and manifest.json: $target"
  find -P "$target" -type l -print -quit | grep -q . && die 'release contains an unexpected symlink'
  python3 - "$target" "$2" <<'PY'
import hashlib,json,re,sys
from pathlib import Path
r=Path(sys.argv[1]); expected_source_sha=sys.argv[2]
try: m=json.loads((r/'manifest.json').read_text(encoding='utf-8'))
except (OSError,ValueError) as e: raise SystemExit(f'invalid release manifest: {e}')
sha=m.get('source_sha'); files=m.get('files')
if not isinstance(sha,str) or not re.fullmatch(r'[0-9a-f]{40}',sha): raise SystemExit('manifest source_sha is not a full lowercase Git SHA')
if sha != expected_source_sha: raise SystemExit('manifest source_sha differs from independently audited SHA')
if r.name != sha: raise SystemExit('release directory name does not match manifest source_sha')
if not isinstance(files,dict) or not files: raise SystemExit('manifest files is empty or invalid')
d=r/'dist'; actual={p.relative_to(d).as_posix() for p in d.rglob('*') if p.is_file()}
if actual != set(files): raise SystemExit('release files differ from source-bound manifest')
for name,expected in files.items():
 if not isinstance(name,str) or name.startswith('/') or '..' in Path(name).parts: raise SystemExit(f'unsafe manifest name: {name!r}')
 if not isinstance(expected,str) or not re.fullmatch(r'[0-9a-f]{64}',expected): raise SystemExit(f'invalid digest: {name!r}')
 if hashlib.sha256((d/name).read_bytes()).hexdigest()!=expected: raise SystemExit(f'release digest differs from manifest: {name}')
print(f'source_sha={sha}'); print(f'index_sha256={files.get("index.html","")}')
PY
}

verify_release_permissions() {
  local target=$1 root=$2 path uid mode
  [[ $(stat -c %u "$root") == 0 ]] || die "releases root is not owned by root: $root"
  while IFS= read -r -d '' path; do
    uid=$(stat -c %u "$path"); mode=$(stat -c %a "$path")
    [[ $uid == 0 ]] || die "release tree is not owned by root: $path"
    (( (8#$mode & 8#022) == 0 )) || die "release tree is group/other writable: $path"
  done < <(find -P "$target" -xdev -print0)
  id "$nginx_user" >/dev/null 2>&1 || die "nginx user does not exist: $nginx_user"
  runuser -u "$nginx_user" -- test -r "$target/manifest.json" || die "nginx cannot read manifest"
  runuser -u "$nginx_user" -- test -r "$target/dist/index.html" || die "nginx cannot read index"
  while IFS= read -r -d '' path; do
    if [[ -d $path ]]; then runuser -u "$nginx_user" -- test -x "$path" || die "nginx cannot traverse: $path"
    else runuser -u "$nginx_user" -- test -r "$path" || die "nginx cannot read: $path"; fi
  done < <(find -P "$target" -xdev -print0)
}

verify_safe_directory() {
  local path=$1 label=$2 uid mode
  [[ -d $path ]] || die "$label is not a directory: $path"
  uid=$(stat -c %u "$path"); mode=$(stat -c %a "$path")
  [[ $uid == 0 ]] || die "$label is not owned by root: $path"
  (( (8#$mode & 8#022) == 0 )) || die "$label is group/other writable: $path"
  runuser -u "$nginx_user" -- test -x "$path" || die "nginx cannot traverse $label: $path"
}

verify_managed_chain() {
  local child=$1 stop=$2 label=$3
  while :; do
    verify_safe_directory "$child" "$label"
    [[ $child == "$stop" ]] && return
    child=$(dirname "$child")
    [[ $child == / ]] && die "$label escapes site root"
  done
}

check_proxy_allowlist() {
  local expected actual
  expected=$(cat <<'ROUTES' | LC_ALL=C sort
location = /api/apariencia {
location = /api/auth/forgot-password { proxy_pass http://virtualoffice_jax_api; }
location = /api/auth/login { proxy_pass http://virtualoffice_jax_api; }
location = /api/auth/logout { proxy_pass http://virtualoffice_jax_api; }
location = /api/auth/me { proxy_pass http://virtualoffice_jax_api; }
location = /api/auth/me/password { proxy_pass http://virtualoffice_jax_api; }
location = /api/auth/mfa/disable { proxy_pass http://virtualoffice_jax_api; }
location = /api/auth/mfa/enrollment/confirm { proxy_pass http://virtualoffice_jax_api; }
location = /api/auth/mfa/enrollment/start { proxy_pass http://virtualoffice_jax_api; }
location = /api/auth/mfa/login/verify { proxy_pass http://virtualoffice_jax_api; }
location = /api/auth/mfa/recover { proxy_pass http://virtualoffice_jax_api; }
location = /api/auth/mfa/recovery-codes/regenerate { proxy_pass http://virtualoffice_jax_api; }
location = /api/auth/mfa/recovery/enrollment/start { proxy_pass http://virtualoffice_jax_api; }
location = /api/auth/mfa/reenrollment/start { proxy_pass http://virtualoffice_jax_api; }
location = /api/auth/mfa/status { proxy_pass http://virtualoffice_jax_api; }
location = /api/auth/mfa/step-up { proxy_pass http://virtualoffice_jax_api; }
location = /api/auth/mfa/step-up/verify { proxy_pass http://virtualoffice_jax_api; }
location = /api/auth/refresh { proxy_pass http://virtualoffice_jax_api; }
location = /api/auth/reset-password { proxy_pass http://virtualoffice_jax_api; }
location = /api/virtual-office { proxy_pass http://virtualoffice_jax_api; }
location ^~ /api/virtual-office/ { proxy_pass http://virtualoffice_jax_api; }
ROUTES
)
  actual=$(awk '
 /^[[:space:]]*location[[:space:]]/ { line=$0; sub(/^[[:space:]]*/,"",line); open=line; if(line ~ /proxy_pass[[:space:]]/) print line; active=1; next }
 active && /proxy_pass[[:space:]]/ { print open }
 active && /^    }/ { active=0 }
 ' "$config" | LC_ALL=C sort -u)
  [[ $actual == "$expected" ]] || die 'proxy locations differ from the closed allowlist'
  [[ $(grep -Ec 'proxy_pass[[:space:]]+' "$config") -eq 21 ]] || die 'unexpected proxy_pass count'
}

case $cmd in
 offline)
  [[ -f $config ]] || die "config does not exist: $config"; [[ -n $release && -n $releases_root && -n $site_root && -n $nginx_root ]] || die '--site-root, --release, --releases-root and --nginx-root are required'; [[ $expected_source_sha =~ ^[0-9a-f]{40}$ ]] || die '--expected-source-sha must be a full lowercase Git SHA'
  site=$(readlink -f -- "$site_root") || die "site root cannot be resolved: $site_root"; [[ -d $site ]] || die "resolved site root is not a directory: $site"
  nginx_root=$(python3 - "$nginx_root" <<'PY'
import os
import sys
print(os.path.abspath(os.path.normpath(sys.argv[1])))
PY
) || die "nginx root cannot be normalized: $nginx_root"
  [[ $nginx_root == "$site/current/dist" ]] || die 'nginx root must be the site current/dist path'
  root=$(readlink -f -- "$releases_root") || die "releases root cannot be resolved: $releases_root"; [[ -d $root ]] || die "resolved releases root is not a directory: $root"
  case "$root" in "$site"/*) ;; *) die "releases root escapes site root: $root" ;; esac
  verify_managed_chain "$root" "$site" 'managed release ancestor'
  release_parent=$(readlink -f -- "$(dirname "$release")") || die 'release parent cannot be resolved'
  case "$release_parent" in "$site"|"$site"/*) ;; *) die "release parent escapes site root: $release_parent" ;; esac
  verify_managed_chain "$release_parent" "$site" 'current container ancestor'
  if [[ -L $release ]]; then [[ $(stat -c %u "$release") == 0 ]] || die "current link is not owned by root: $release"; fi
  resolved=$(readlink -f -- "$release") || die "release cannot be resolved: $release"; [[ -d $resolved ]] || die "resolved release is not a directory: $resolved"
  case "$resolved" in "$root"/*) ;; *) die "release escapes releases root: $resolved" ;; esac
  verify_manifest "$resolved" "$expected_source_sha"
  verify_release_permissions "$resolved" "$root"
  [[ $(readlink -f -- "$release") == "$resolved" ]] || die 'release target changed during verification'
  grep -Fqx '    server_name virtualoffice.axioma-ia.io;' "$config" || die 'exact public host is absent'
  grep -Fqx "    root $nginx_root;" "$config" || die 'nginx root is not bound to this site current/dist'
  grep -Fqx '    location ^~ /api/ { return 404; }' "$config" || die 'non-Office API is not denied'
  grep -Fqx '    location ^~ /ws/ { return 404; }' "$config" || die 'websocket namespace is not denied'
  grep -Fqx '        if ($request_method != GET) { return 404; }' "$config" || die 'public appearance route does not deny non-GET methods'
  grep -Fq 'proxy_set_header Host $host;' "$config" || die 'public Host is not forwarded'
  grep -Fq 'proxy_set_header X-Forwarded-Proto https;' "$config" || die 'HTTPS proto is not forwarded'
  grep -Fq 'proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;' "$config" || die 'client chain is not forwarded'
  grep -Fq 'Strict-Transport-Security "max-age=31536000" always;' "$config" || die 'HSTS is absent or over-broad'
  check_proxy_allowlist; printf 'offline validation passed for resolved release %s\n' "$resolved"
  ;;
 syntax) [[ -n $nginx && -x $nginx && -f $config ]] || die 'syntax needs executable --nginx and file --config'; "$nginx" -t -c "$config" ;;
 smoke)
  [[ -n $public_ip && $expected_index_sha =~ ^[0-9a-f]{64}$ && $expected_asset_sha =~ ^[0-9a-f]{64}$ ]] || die 'smoke needs public IP plus lowercase index and asset SHA-256'
  [[ $asset_path =~ ^/assets/[A-Za-z0-9._/-]+$ && $asset_path != *..* ]] || die '--asset-path must be a safe /assets/ path'
  resolve=(--resolve "$host:443:$public_ip"); base="https://$host"
  actual=$(curl --noproxy '*' --fail --silent --show-error --max-time 10 "${resolve[@]}" "$base/" | sha256sum | awk '{print $1}'); [[ $actual == "$expected_index_sha" ]] || die 'index hash mismatch'
  status(){ curl --noproxy '*' --silent --show-error --output /dev/null --write-out '%{http_code}' --max-time 10 "${resolve[@]}" "$base$1"; }
  blocked(){ [[ $(status "$1") == 404 ]] || die "blocked route is exposed: $1"; }
  [[ $(status /api/virtual-office/session) == 401 ]] || die 'Office session must be 401 without credentials'
  blocked /api/admin/users; blocked /api/chat; blocked /ws/; blocked /api/auth/mfa/root-reset; blocked /api%2fauth%2fmfa%2froot-reset; blocked /api//admin/users
  [[ $(curl --noproxy '*' --path-as-is --silent --show-error --output /dev/null --write-out '%{http_code}' --max-time 10 "${resolve[@]}" "$base/api/virtual-office/%2e%2e/admin/users") == 404 ]] || die 'encoded traversal is exposed'
  [[ $(status /api/apariencia) == 200 ]] || die 'public appearance route is unavailable'
  [[ $(curl --noproxy '*' --silent --show-error --output /dev/null --write-out '%{http_code}' --max-time 10 -X POST "${resolve[@]}" "$base/api/apariencia") == 404 ]] || die 'public appearance route accepts a write method'
  [[ $(status /api/auth/login) != 404 ]] || die 'login route is unavailable'
  ih=$(curl --noproxy '*' --silent --show-error --head --max-time 10 "${resolve[@]}" "$base/"); ah=$(curl --noproxy '*' --silent --show-error --head --max-time 10 "${resolve[@]}" "$base$asset_path")
  grep -Eiq '^HTTP/[^ ]+ 200' <<<"$ah" || die 'asset path did not return HTTP 200'
  asset=$(curl --noproxy '*' --fail --silent --show-error --max-time 10 "${resolve[@]}" "$base$asset_path" | sha256sum | awk '{print $1}')
  [[ $asset == "$expected_asset_sha" ]] || die 'asset hash mismatch'
  for h in "${headers[@]}"; do n=${h%%:*}; v=${h#*: }; has_header "$ih" "$n" "$v" || die "index missing $n"; has_header "$ah" "$n" "$v" || die "asset missing $n"; done
  foreign_closed() { [[ $1 == 000 || $1 == 400 || $1 == 403 || $1 == 404 || $1 == 421 ]] || die "$2 was not closed (HTTP $1)"; }
  mismatch=invalid-sni.axioma-ia.io
  badsni=$(curl --noproxy '*' -k --silent --show-error --output /dev/null --write-out '%{http_code}' --max-time 10 --resolve "$mismatch:443:$public_ip" "https://$mismatch/" || true)
  foreign_closed "$badsni" 'unknown SNI'
  badhost=$(curl --noproxy '*' -k --silent --show-error --output /dev/null --write-out '%{http_code}' --max-time 10 -H 'Host: invalid-host.axioma-ia.io' "${resolve[@]}" "$base/" || true)
  foreign_closed "$badhost" 'mismatched Host'
  printf 'pre-DNS smoke passed for %s via %s\n' "$host" "$public_ip"
  ;;
 *) usage >&2; exit 2 ;;
esac
