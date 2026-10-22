#!/usr/bin/env bash
# Materialize one sealed JAX + jax-platform pair.  Inputs are never executed.
set -euo pipefail
umask 022
ROOT=/opt/jax-platform-pair
JXP_REPOSITORY= JXP_REVISION= JAX_REPOSITORY= JAX_REVISION= WHEELHOUSE= LOCK= PYTHON=/usr/bin/python3
while [[ $# -gt 0 ]]; do
  case "$1" in
    --root) ROOT=${2:?}; shift;; --platform-repository) JXP_REPOSITORY=${2:?}; shift;; --platform-revision) JXP_REVISION=${2:?}; shift;;
    --jax-repository) JAX_REPOSITORY=${2:?}; shift;; --jax-revision) JAX_REVISION=${2:?}; shift;; --wheelhouse) WHEELHOUSE=${2:?}; shift;; --lock) LOCK=${2:?}; shift;; --python) PYTHON=${2:?}; shift;;
    -h|--help) echo 'usage: install-platform-pair-release.sh --platform-repository DIR --platform-revision SHA --jax-repository DIR --jax-revision SHA --wheelhouse ROOT_OWNED_DIR --lock HASHED_LOCK [--python /usr/bin/python3] [--root /opt/jax-platform-pair]'; exit 0;;
    *) echo "unknown argument: $1" >&2; exit 2;;
  esac; shift
done
[[ $(id -u) == 0 && $ROOT == /opt/jax-platform-pair && $JXP_REVISION =~ ^[0-9a-f]{40}$ && $JAX_REVISION =~ ^[0-9a-f]{40}$ && -d $WHEELHOUSE && -f $LOCK && -x $PYTHON ]] || { echo 'root, exact revisions, root-owned wheelhouse and hashed lock required' >&2; exit 2; }
for repo in "$JXP_REPOSITORY" "$JAX_REPOSITORY"; do [[ -d $repo/.git ]] || { echo 'repository input invalid' >&2; exit 2; }; done
for path in "$WHEELHOUSE" "$LOCK" "$JXP_REPOSITORY" "$JAX_REPOSITORY" "$PYTHON"; do [[ ! -L $path && $(stat -c '%U:%G' "$path") == root:root && $((8#$(stat -c '%a' "$path") & 0022)) == 0 ]] || { echo 'input is mutable or untrusted' >&2; exit 1; }; done
grep -q -- '--hash=sha256:' "$LOCK" || { echo 'dependency lock lacks SHA-256 hashes' >&2; exit 1; }
for pair in "$JXP_REPOSITORY:$JXP_REVISION" "$JAX_REPOSITORY:$JAX_REVISION"; do repo=${pair%%:*}; revision=${pair#*:}; git -C "$repo" cat-file -e "$revision^{commit}"; [[ $(git -C "$repo" rev-parse "$revision^{commit}") == "$revision" ]] || exit 1; done
install -d -o root -g root -m 0755 "$ROOT/releases"
stage=$(mktemp -d "$ROOT/releases/.new.XXXXXX"); cleanup() { [[ -n ${stage:-} ]] && rm -rf -- "$stage"; }; trap cleanup EXIT
mkdir "$stage/jax-platform" "$stage/jax"
git -C "$JXP_REPOSITORY" archive --format=tar "$JXP_REVISION" | tar -x -C "$stage/jax-platform"
git -C "$JAX_REPOSITORY" archive --format=tar "$JAX_REVISION" | tar -x -C "$stage/jax"
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
PAIR_HELPER_DIR="$(dirname "${BASH_SOURCE[0]}")" python3 - "$stage" "$JXP_REVISION" "$JAX_REVISION" "$lock_digest" "$wheelhouse_digest" <<'PY'
import json, os, sys
from pathlib import Path
sys.path.insert(0, os.environ['PAIR_HELPER_DIR'])
from pair_artifact_readiness import SCHEMA, _inventory
root=Path(sys.argv[1]); (root/'pair-manifest.json').write_text(json.dumps({'schema':SCHEMA,'jax_platform_revision':sys.argv[2],'jax_revision':sys.argv[3],'dependency_lock_sha256':sys.argv[4],'wheelhouse_manifest_sha256':sys.argv[5],'files':_inventory(root, owner_uid=0)}, separators=(',',':'))+'\n')
PY
name="$JXP_REVISION-$JAX_REVISION"; target="$ROOT/releases/$name"; [[ ! -e $target ]] || { echo 'release already exists' >&2; exit 1; }; mv "$stage" "$target"; stage=
old=$(readlink "$ROOT/current" 2>/dev/null || true); ln -s "releases/$name" "$ROOT/.current.new"; mv -T "$ROOT/.current.new" "$ROOT/current"
if ! "$(dirname "${BASH_SOURCE[0]}")/pair-artifact-readiness.sh" "$ROOT" "$JXP_REVISION" "$JAX_REVISION"; then [[ -n $old ]] && ln -s "$old" "$ROOT/.current.rollback" && mv -T "$ROOT/.current.rollback" "$ROOT/current" || rm -f "$ROOT/current"; exit 1; fi
echo 'sealed JAX/platform pair installed and selected'
