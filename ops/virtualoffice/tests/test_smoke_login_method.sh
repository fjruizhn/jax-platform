#!/usr/bin/env bash
# The public smoke must probe the real POST login route, not GET /api/auth/login.
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
fixture=$(mktemp -d)
trap 'rm -rf -- "$fixture"' EXIT
cat > "$fixture/curl" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
method=GET; output=body; host_override=; url=
while (($#)); do
  case $1 in
    --data|-d) method=POST; shift 2 ;;
    -X|--request) method=$2; shift 2 ;;
    --head|-I) output=head; shift ;;
    --write-out|-w) output=status; shift 2 ;;
    -H|--header) [[ $2 == 'Host: invalid-host.axioma-ia.io' ]] && host_override=invalid; shift 2 ;;
    --resolve|--noproxy|--max-time|--output|-o) shift 2 ;;
    http*) url=$1; shift ;;
    *) shift ;;
  esac
done
case $url in
  https://invalid-sni.axioma-ia.io/*) code=421 ;;
  */api/auth/login) if [[ $method == POST ]]; then code=${LOGIN_POST_STATUS:-422}; else code=404; fi ;;
  */api/virtual-office/session) code=401 ;;
  */api/apariencia) if [[ $method == POST ]]; then code=404; else code=200; fi ;;
  */assets/index-test.js|https://virtualoffice.axioma-ia.io/) code=200 ;;
  *) code=404 ;;
esac
[[ $host_override == invalid ]] && code=421
case $output in
  status) printf '%s' "$code" ;;
  head) printf 'HTTP/2 %s\r\nX-Content-Type-Options: nosniff\r\nReferrer-Policy: same-origin\r\nX-Frame-Options: DENY\r\nStrict-Transport-Security: max-age=31536000\r\n' "$code" ;;
  body) case $url in */assets/index-test.js) printf 'asset\n' ;; *) printf '<html>office</html>\n' ;; esac ;;
esac
SH
chmod +x "$fixture/curl"
export PATH="$fixture:$PATH"
index=$(printf '<html>office</html>\n' | sha256sum | cut -d' ' -f1)
asset=$(printf 'asset\n' | sha256sum | cut -d' ' -f1)
smoke() { "$root/validate-public-ingress.sh" smoke --public-ip 127.0.0.1 --expected-index-sha "$index" --asset-path /assets/index-test.js --expected-asset-sha "$asset"; }
smoke >/dev/null
for bad in 404 503; do
  if LOGIN_POST_STATUS=$bad smoke >/dev/null 2>&1; then
    echo "smoke accepted login POST HTTP $bad" >&2
    exit 1
  fi
done
echo 'POST-only login smoke fixtures passed'
