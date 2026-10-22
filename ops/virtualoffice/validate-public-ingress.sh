#!/usr/bin/env bash
# Offline and pre-DNS verification for the Virtual Office public ingress.
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  validate-public-ingress.sh offline --config FILE --dist DIR --manifest FILE
  validate-public-ingress.sh syntax --nginx NGINX --config FILE
  validate-public-ingress.sh smoke --public-ip IP --expected-index-sha SHA256

`offline` never contacts a server. `syntax` runs nginx -t with the operator's
complete aaPanel config. `smoke` is the post-install, pre-DNS probe and uses
curl --resolve for virtualoffice.axioma-ia.io only.
USAGE
}

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
require_value() { [[ $# -eq 2 && -n $2 ]] || die "missing value for $1"; }

command_name=${1:-}
shift || true
config=
dist=
nginx=
public_ip=
expected_index_sha=
manifest=
while [[ $# -gt 0 ]]; do
  case $1 in
    --config) require_value "$1" "${2:-}"; config=$2; shift 2 ;;
    --dist) require_value "$1" "${2:-}"; dist=$2; shift 2 ;;
    --nginx) require_value "$1" "${2:-}"; nginx=$2; shift 2 ;;
    --public-ip) require_value "$1" "${2:-}"; public_ip=$2; shift 2 ;;
    --expected-index-sha) require_value "$1" "${2:-}"; expected_index_sha=$2; shift 2 ;;
    --manifest) require_value "$1" "${2:-}"; manifest=$2; shift 2 ;;
    *) die "unknown argument: $1" ;;
  esac
done

host=virtualoffice.axioma-ia.io
case $command_name in
  offline)
    [[ -f $config ]] || die "config does not exist: $config"
    [[ -f $dist/index.html ]] || die "dist/index.html does not exist: $dist"
    [[ -n $manifest ]] || die '--manifest is required to keep the immutable release unchanged'
    grep -Fqx '    server_name virtualoffice.axioma-ia.io;' "$config" || die 'exact public host is absent'
    grep -Fqx '    location ^~ /api/virtual-office/ { proxy_pass http://virtualoffice_jax_api; }' "$config" || die 'Office prefix is not proxied'
    grep -Fqx '    location ^~ /api/ { return 404; }' "$config" || die 'non-Office API is not denied'
    grep -Fqx '    location ^~ /ws/ { return 404; }' "$config" || die 'websocket namespace is not denied'
    auth_routes=(
      /api/auth/login /api/auth/refresh /api/auth/logout /api/auth/me
      /api/auth/me/password /api/auth/forgot-password /api/auth/reset-password
      /api/auth/mfa/status /api/auth/mfa/login/verify
      /api/auth/mfa/enrollment/start /api/auth/mfa/enrollment/confirm
      /api/auth/mfa/step-up /api/auth/mfa/step-up/verify /api/auth/mfa/recover
      /api/auth/mfa/recovery/enrollment/start /api/auth/mfa/reenrollment/start
      /api/auth/mfa/recovery-codes/regenerate /api/auth/mfa/disable
    )
    for route in "${auth_routes[@]}"; do
      grep -Fqx "    location = $route { proxy_pass http://virtualoffice_jax_api; }" "$config" || die "auth route is absent: $route"
    done
    grep -Fqx '    location = /api/apariencia {' "$config" || die 'public appearance route is absent'
    grep -Fqx '        if ($request_method != GET) { return 404; }' "$config" || die 'public appearance route does not deny non-GET methods'
    grep -Fq 'proxy_set_header Host $host;' "$config" || die 'public Host is not forwarded'
    grep -Fq 'proxy_set_header X-Forwarded-Proto https;' "$config" || die 'HTTPS proto is not forwarded'
    grep -Fq 'proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;' "$config" || die 'client chain is not forwarded'
    find "$dist" -type l -print -quit | grep -q . && die 'dist contains a symlink'
    printf 'index.html sha256: '
    sha256sum "$dist/index.html" | awk '{print $1}'
    mkdir -p "$(dirname "$manifest")"
    (cd "$dist" && find . -type f -printf '%P\0' | LC_ALL=C sort -z | xargs -0 sha256sum) > "$manifest"
    printf 'offline validation passed; manifest written to %s\n' "$manifest"
    ;;
  syntax)
    [[ -n $nginx && -x $nginx ]] || die '--nginx must be an executable nginx binary'
    [[ -f $config ]] || die "config does not exist: $config"
    "$nginx" -t -c "$config"
    ;;
  smoke)
    [[ -n $public_ip ]] || die '--public-ip is required'
    [[ $expected_index_sha =~ ^[0-9a-f]{64}$ ]] || die '--expected-index-sha must be lowercase SHA-256'
    resolve=(--resolve "$host:443:$public_ip")
    actual=$(curl --noproxy '*' --fail --silent --show-error --max-time 10 "${resolve[@]}" "https://$host/" | sha256sum | awk '{print $1}')
    [[ $actual == "$expected_index_sha" ]] || die "index hash mismatch: expected $expected_index_sha got $actual"
    status() { curl --noproxy '*' --silent --show-error --output /dev/null --write-out '%{http_code}' --max-time 10 "${resolve[@]}" "https://$host$1"; }
    [[ $(status /api/virtual-office/session) == 401 ]] || die 'Office session must be present and unauthenticated (401)'
    [[ $(status /api/admin/users) == 404 ]] || die 'admin route is exposed'
    [[ $(status /api/chat) == 404 ]] || die 'classic API route is exposed'
    [[ $(status /ws/) == 404 ]] || die 'websocket route is exposed'
    [[ $(status /api/apariencia) == 200 ]] || die 'public appearance route is unavailable'
    [[ $(curl --noproxy '*' --silent --show-error --output /dev/null --write-out '%{http_code}' --max-time 10 -X POST "${resolve[@]}" "https://$host/api/apariencia") == 404 ]] || die 'public appearance route accepts a write method'
    [[ $(status /api/auth/login) != 404 ]] || die 'login route is unavailable'
    printf 'pre-DNS smoke passed for %s via %s\n' "$host" "$public_ip"
    ;;
  *) usage >&2; exit 2 ;;
esac
