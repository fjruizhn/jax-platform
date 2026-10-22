#!/usr/bin/env bash
# Root-only atomic activation. current is observational: units receive literals.
set -euo pipefail
ROOT=/opt/jax-platform-pair RELEASE= EXPECTED= PREVIOUS=
while [[ $# -gt 0 ]]; do
 case "$1" in
  --release) RELEASE=${2:?}; shift;; --expected-manifest-sha256) EXPECTED=${2:?}; shift;; --previous-manifest-sha256) PREVIOUS=${2:?}; shift;;
  -h|--help) echo 'usage: activate-platform-pair-release.sh --release /opt/jax-platform-pair/releases/JXP-JAX --expected-manifest-sha256 SHA [--previous-manifest-sha256 SHA]'; exit 0;; *) exit 2;; esac; shift
done
[[ $(id -u) == 0 && $RELEASE == "$ROOT/releases/"* && $EXPECTED =~ ^[0-9a-f]{64}$ ]] || exit 2
exec 9>"$ROOT/.activation.lock"; flock -n 9 || { echo 'pair activation already in progress' >&2; exit 1; }
install -o root -g root -m 0644 /dev/null "$ROOT/.runtime.lock"
[[ ! -L $RELEASE && -d $RELEASE ]] || { echo 'unsafe release' >&2; exit 1; }
actual=$(sha256sum "$RELEASE/pair-manifest.json" | cut -d' ' -f1); [[ $actual == "$EXPECTED" ]] || { echo 'unexpected manifest digest' >&2; exit 1; }
"$(dirname "${BASH_SOURCE[0]}")/pair_artifact_readiness.py" "$RELEASE" || exit 1
if [[ -n $PREVIOUS ]]; then
 current=$(readlink -f "$ROOT/current" 2>/dev/null || true)
 [[ -n $current && $(sha256sum "$current/pair-manifest.json" | cut -d' ' -f1) == "$PREVIOUS" ]] || { echo 'predecessor digest mismatch' >&2; exit 1; }
fi
exec 8>"$ROOT/.runtime.lock"; flock -x 8
systemctl stop jax-platform.service
systemctl stop jax-platform-migrate.service
python3 - "$RELEASE" "$EXPECTED" <<'PY' | install -o root -g root -m 0644 /dev/stdin /etc/jax/platform-pair-selection.json
import json,sys
x=json.load(open(sys.argv[1]+'/pair-manifest.json'))
print(json.dumps({'release':sys.argv[1],'jax_platform_revision':x['jax_platform_revision'],'jax_revision':x['jax_revision'],'manifest_sha256':sys.argv[2]},separators=(',',':')))
PY
systemctl start jax-platform-migrate.service
systemctl start jax-platform.service
ln -sfn "releases/$(basename "$RELEASE")" "$ROOT/.current.new"; mv -T "$ROOT/.current.new" "$ROOT/current"
