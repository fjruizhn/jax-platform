#!/usr/bin/env bash
# Materialize one sealed JAX + jax-platform pair.  Inputs are never executed.
set -euo pipefail
PATH=/usr/sbin:/usr/bin:/sbin:/bin
umask 022
ROOT=/opt/jax-platform-pair
JXP_SOURCE= JXP_SOURCE_SHA256= JXP_REVISION= JAX_SOURCE= JAX_SOURCE_SHA256= JAX_REVISION= WHEELHOUSE= LOCK= PYTHON=/usr/bin/python3
while [[ $# -gt 0 ]]; do
  case "$1" in
    --root) ROOT=${2:?}; shift;; --platform-source) JXP_SOURCE=${2:?}; shift;; --platform-source-sha256) JXP_SOURCE_SHA256=${2:?}; shift;; --platform-revision) JXP_REVISION=${2:?}; shift;;
    --jax-source) JAX_SOURCE=${2:?}; shift;; --jax-source-sha256) JAX_SOURCE_SHA256=${2:?}; shift;; --jax-revision) JAX_REVISION=${2:?}; shift;; --wheelhouse) WHEELHOUSE=${2:?}; shift;; --lock) LOCK=${2:?}; shift;; --python) PYTHON=${2:?}; shift;;
    -h|--help) echo 'usage: install-platform-pair-release.sh --platform-source TAR --platform-source-sha256 SHA --platform-revision SHA --jax-source TAR --jax-source-sha256 SHA --jax-revision SHA --wheelhouse ROOT_OWNED_DIR --lock HASHED_LOCK [--python /usr/bin/python3] [--root /opt/jax-platform-pair]'; exit 0;;
    *) echo "unknown argument: $1" >&2; exit 2;;
  esac; shift
done
[[ $(/usr/bin/id -u) == 0 && $ROOT == /opt/jax-platform-pair && $JXP_REVISION =~ ^[0-9a-f]{40}$ && $JAX_REVISION =~ ^[0-9a-f]{40}$ && $JXP_SOURCE_SHA256 =~ ^[0-9a-f]{64}$ && $JAX_SOURCE_SHA256 =~ ^[0-9a-f]{64}$ && -d $WHEELHOUSE && -f $LOCK && -x $PYTHON ]] || { echo 'root, exact source digests, revisions, wheelhouse and hashed lock required' >&2; exit 2; }
for path in "$WHEELHOUSE" "$LOCK" "$JXP_SOURCE" "$JAX_SOURCE" "$PYTHON"; do [[ ! -L $path && $(stat -c '%U:%G' "$path") == root:root && $((8#$(stat -c '%a' "$path") & 0022)) == 0 ]] || { echo 'input is mutable or untrusted' >&2; exit 1; }; done
grep -q -- '--hash=sha256:' "$LOCK" || { echo 'dependency lock lacks SHA-256 hashes' >&2; exit 1; }
[[ $(/usr/bin/sha256sum "$JXP_SOURCE" | /usr/bin/cut -d' ' -f1) == "$JXP_SOURCE_SHA256" && $(/usr/bin/sha256sum "$JAX_SOURCE" | /usr/bin/cut -d' ' -f1) == "$JAX_SOURCE_SHA256" ]] || { echo 'source archive digest mismatch' >&2; exit 1; }
install -d -o root -g root -m 0755 "$ROOT/releases"
stage=$(mktemp -d "$ROOT/releases/.new.XXXXXX"); cleanup() { [[ -n ${stage:-} ]] && rm -rf -- "$stage"; }; trap cleanup EXIT
mkdir "$stage/jax-platform" "$stage/jax"
/usr/bin/tar -tf "$JXP_SOURCE" >/dev/null && /usr/bin/tar -xf "$JXP_SOURCE" -C "$stage/jax-platform"
/usr/bin/tar -tf "$JAX_SOURCE" >/dev/null && /usr/bin/tar -xf "$JAX_SOURCE" -C "$stage/jax"
"$PYTHON" -m venv --copies "$stage/jax-platform/backend/.venv"
"$stage/jax-platform/backend/.venv/bin/pip" install --only-binary=:all: --no-index --find-links "$WHEELHOUSE" --require-hashes --no-deps -r "$LOCK"
"$stage/jax-platform/backend/.venv/bin/pip" check
[[ -x $stage/jax-platform/backend/.venv/bin/python && -x $stage/jax-platform/backend/.venv/bin/uvicorn ]] || { echo 'sealed venv lacks platform executables' >&2; exit 1; }
while IFS= read -r -d '' link; do
  [[ $link == "$stage/jax-platform/backend/.venv/lib64" && $(readlink "$link") == lib ]] || { echo 'release contains forbidden symlink' >&2; exit 1; }
done < <(find "$stage" -type l -print0)
chown -R root:root "$stage"; find "$stage" -type d -exec chmod 0755 {} +; find "$stage" -type f -exec chmod 0644 {} +; chmod 0755 "$stage/jax-platform/backend/.venv/bin/python" "$stage/jax-platform/backend/.venv/bin/uvicorn"
lock_digest=$(sha256sum "$LOCK" | cut -d' ' -f1)
wheelhouse_digest=$(cd "$WHEELHOUSE" && find . -type f -printf '%P\n' | LC_ALL=C sort | while IFS= read -r wheel; do sha256sum "$wheel"; done | sha256sum | cut -d' ' -f1)
PAIR_HELPER_DIR=/usr/local/libexec /usr/bin/python3 -Es - "$stage" "$JXP_REVISION" "$JAX_REVISION" "$lock_digest" "$wheelhouse_digest" <<'PY'
import json, os, sys
from pathlib import Path
sys.path.insert(0, os.environ['PAIR_HELPER_DIR'])
from pair_artifact_readiness import SCHEMA, _inventory
root=Path(sys.argv[1]); (root/'pair-manifest.json').write_text(json.dumps({'schema':SCHEMA,'jax_platform_revision':sys.argv[2],'jax_revision':sys.argv[3],'dependency_lock_sha256':sys.argv[4],'wheelhouse_manifest_sha256':sys.argv[5],'files':_inventory(root, owner_uid=0)}, separators=(',',':'))+'\n')
PY
name="$JXP_REVISION-$JAX_REVISION"; target="$ROOT/releases/$name"; [[ ! -e $target ]] || { echo 'release already exists' >&2; exit 1; }; /usr/bin/mv "$stage" "$target"; stage=
/usr/bin/python3 -Es /usr/local/libexec/pair_artifact_readiness.py "$target" "$JXP_REVISION" "$JAX_REVISION"
manifest_digest=$(/usr/bin/sha256sum "$target/pair-manifest.json" | /usr/bin/cut -d' ' -f1)
printf 'sealed JAX/platform pair installed: %s\nmanifest_sha256=%s\nactivate with /usr/local/libexec/activate-platform-pair-release.sh --release %s --expected-manifest-sha256 %s\n' "$target" "$manifest_digest" "$target" "$manifest_digest"
