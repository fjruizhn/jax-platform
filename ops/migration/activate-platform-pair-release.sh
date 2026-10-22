#!/usr/bin/env bash
# Root-only atomic activation. current is observational: units receive literals.
set -euo pipefail
PATH=/usr/sbin:/usr/bin:/sbin:/bin
ROOT=/opt/jax-platform-pair RELEASE= EXPECTED= PREVIOUS= SELECTION=/etc/jax/platform-pair-selection.json SYSTEMCTL=/usr/bin/systemctl HELPER=/usr/local/libexec/pair_artifact_readiness.py
while [[ $# -gt 0 ]]; do
 case "$1" in
  --release) RELEASE=${2:?}; shift;; --expected-manifest-sha256) EXPECTED=${2:?}; shift;; --previous-manifest-sha256) PREVIOUS=${2:?}; shift;; --root) ROOT=${2:?}; shift;;
  -h|--help) echo 'usage: activate-platform-pair-release.sh --release /opt/jax-platform-pair/releases/JXP-JAX --expected-manifest-sha256 SHA [--previous-manifest-sha256 SHA]'; exit 0;; *) exit 2;; esac; shift
done
if [[ $ROOT != /opt/jax-platform-pair ]]; then
  [[ ${JAX_PAIR_TEST_MODE:-} == 1 && $(/usr/bin/id -u) == 0 && $ROOT == /tmp/* ]] || { echo 'test root rejected' >&2; exit 2; }
  SELECTION="$ROOT/platform-pair-selection.json"; HELPER="$ROOT/libexec/pair_artifact_readiness.py"
  [[ -n ${JAX_PAIR_SYSTEMCTL_BIN:-} && -x ${JAX_PAIR_SYSTEMCTL_BIN} ]] || { echo 'test systemctl rejected' >&2; exit 2; }
  SYSTEMCTL=$JAX_PAIR_SYSTEMCTL_BIN
fi
[[ $(/usr/bin/id -u) == 0 && $RELEASE == "$ROOT/releases/"* && $EXPECTED =~ ^[0-9a-f]{64}$ ]] || exit 2
exec 9>"$ROOT/.activation.lock"; /usr/bin/flock -n 9 || { echo 'pair activation already in progress' >&2; exit 1; }
install -o root -g root -m 0644 /dev/null "$ROOT/.runtime.lock"
[[ ! -L $RELEASE && -d $RELEASE ]] || { echo 'unsafe release' >&2; exit 1; }
actual=$(/usr/bin/sha256sum "$RELEASE/pair-manifest.json" | /usr/bin/cut -d' ' -f1); [[ $actual == "$EXPECTED" ]] || { echo 'unexpected manifest digest' >&2; exit 1; }
/usr/bin/python3 -Es "$HELPER" "$RELEASE" || exit 1
if [[ -n $PREVIOUS ]]; then
 current=$(readlink -f "$ROOT/current" 2>/dev/null || true)
 [[ -n $current && $(/usr/bin/sha256sum "$current/pair-manifest.json" | /usr/bin/cut -d' ' -f1) == "$PREVIOUS" ]] || { echo 'predecessor digest mismatch' >&2; exit 1; }
fi
exec 8>"$ROOT/.runtime.lock"; /usr/bin/flock -x 8
$SYSTEMCTL stop jax-platform.service
$SYSTEMCTL stop jax-platform-migrate.service
for unit in jax-platform.service jax-platform-migrate.service; do
  if $SYSTEMCTL is-active --quiet "$unit"; then
    echo "service did not stop: $unit" >&2
    exit 1
  fi
done
selection_tmp=$(/usr/bin/mktemp "$(dirname "$SELECTION")/.platform-pair-selection.XXXXXX")
trap 'rm -f -- "$selection_tmp"' EXIT
/usr/bin/python3 -Es - "$RELEASE" "$EXPECTED" <<'PY' >"$selection_tmp"
import json,sys
x=json.load(open(sys.argv[1]+'/pair-manifest.json'))
print(json.dumps({'release':sys.argv[1],'jax_platform_revision':x['jax_platform_revision'],'jax_revision':x['jax_revision'],'manifest_sha256':sys.argv[2]},separators=(',',':')))
PY
/usr/bin/chown root:root "$selection_tmp"; /usr/bin/chmod 0644 "$selection_tmp"; /usr/bin/mv -fT "$selection_tmp" "$SELECTION"
trap - EXIT
# The selection is now durable.  Do not retain the exclusive runtime lock while
# systemd synchronously runs the migration launcher: it needs the shared lock.
/usr/bin/flock -u 8
exec 8>&-
$SYSTEMCTL start jax-platform-migrate.service
$SYSTEMCTL start jax-platform.service
ln -sfn "releases/$(basename "$RELEASE")" "$ROOT/.current.new"; mv -T "$ROOT/.current.new" "$ROOT/current"
