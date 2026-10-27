#!/usr/bin/env bash
# Build an immutable, root-owned runtime snapshot for the invitation timer.
# This intentionally never executes code from the mutable jaxsvc checkout.
set -euo pipefail
umask 022

ORIGIN=https://github.com/fjruizhn/jax-platform.git
REVISION=
RELEASE=/opt/axioma-office-invitation-worker
PYTHON=python3
WHEELHOUSE=
LOCK=
BUILD_USER=jaxbuild
while [[ $# -gt 0 ]]; do
  case "$1" in
    --origin) ORIGIN=${2:?}; shift ;;
    --revision) REVISION=${2:?}; shift ;;
    --release) RELEASE=${2:?}; shift ;;
    --python) PYTHON=${2:?}; shift ;;
    --wheelhouse) WHEELHOUSE=${2:?}; shift ;;
    --lock) LOCK=${2:?}; shift ;;
    --build-user) BUILD_USER=${2:?}; shift ;;
    -h|--help) echo 'usage: install-invitation-worker-release.sh --revision SHA --wheelhouse DIR --lock HASHED_LOCK [--origin URL] [--release DIR] [--python PYTHON] [--build-user USER]'; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done
[[ $(id -u) == 0 ]] || { echo 'must run as root' >&2; exit 1; }
[[ $REVISION =~ ^[0-9a-f]{40}$ && -d $WHEELHOUSE && -f $LOCK ]] || { echo 'full revision, wheelhouse, and hashed lock are required' >&2; exit 2; }
getent passwd "$BUILD_USER" >/dev/null || { echo 'unprivileged build user is absent' >&2; exit 2; }
case "$RELEASE" in
  /opt/axioma-office-invitation-worker) timer=axioma-office-invitation-worker.timer; service=axioma-office-invitation-worker.service ;;
  /opt/axioma-office-dedicated-delivery) timer=axioma-office-delivery.timer; service=axioma-office-delivery.service ;;
  *) echo 'unsafe release path' >&2; exit 2 ;;
esac
for path in "$WHEELHOUSE" "$LOCK"; do
  [[ ! -L $path && $(stat -c '%U:%G' "$path") == root:root ]] || { echo 'wheelhouse or lock ownership is unsafe' >&2; exit 1; }
  mode=$(stat -c '%a' "$path"); (( (8#$mode & 0022) == 0 )) || { echo 'wheelhouse or lock is writable' >&2; exit 1; }
done
[[ -d $WHEELHOUSE && -f $LOCK ]] || { echo 'wheelhouse or lock type is unsafe' >&2; exit 1; }
while IFS= read -r -d '' path; do
  [[ ! -L $path && $(stat -c '%U:%G' "$path") == root:root ]] || { echo 'wheelhouse contains unsafe owner or symlink' >&2; exit 1; }
  mode=$(stat -c '%a' "$path"); (( (8#$mode & 0022) == 0 )) || { echo 'wheelhouse contains writable content' >&2; exit 1; }
  [[ -f $path || -d $path ]] || { echo 'wheelhouse contains special file' >&2; exit 1; }
done < <(find "$WHEELHOUSE" -xdev -mindepth 1 -print0)
grep -q -- '--hash=sha256:' "$LOCK" || { echo 'wheelhouse lock lacks SHA-256 hashes' >&2; exit 1; }
parent=$(dirname "$RELEASE")
install -d -o root -g root -m 0755 "$parent"
[[ ! -L $parent && ! -L $RELEASE ]] || { echo 'release path symlink is forbidden' >&2; exit 1; }
if systemctl is-active --quiet "$timer" || systemctl is-active --quiet "$service"; then
  echo 'stop the worker timer and service before building its release' >&2; exit 1
fi
stage=$(mktemp -d "$parent/.axioma-office-invitation-worker.new.XXXXXX")
trusted_repo=$(mktemp -d /var/tmp/axioma-office-worker-source.XXXXXX)
reference=
cleanup() { if [[ -n ${stage:-} && -d $stage ]]; then rm -rf "$stage"; fi; if [[ -n ${reference:-} && -d $reference ]]; then rm -rf "$reference"; fi; if [[ -n ${trusted_repo:-} && -d $trusted_repo ]]; then rm -rf "$trusted_repo"; fi; }
trap cleanup EXIT
git -C "$trusted_repo" init -q
git -C "$trusted_repo" remote add origin "$ORIGIN"
GIT_NO_REPLACE_OBJECTS=1 git -C "$trusted_repo" fetch --no-tags origin "$REVISION"
[[ -z $(git -C "$trusted_repo" replace -l) ]] || { echo 'trusted clone has replacement refs' >&2; exit 1; }
[[ ! -e $trusted_repo/.git/objects/info/alternates ]] || { echo 'trusted clone has object alternates' >&2; exit 1; }
GIT_NO_REPLACE_OBJECTS=1 git -C "$trusted_repo" fsck --strict --no-dangling
[[ $(GIT_NO_REPLACE_OBJECTS=1 git -C "$trusted_repo" rev-parse FETCH_HEAD^{commit}) == "$REVISION" ]] || { echo 'fetched revision is not exact' >&2; exit 1; }
GIT_NO_REPLACE_OBJECTS=1 git -C "$trusted_repo" archive --format=tar "$REVISION" | tar -x -C "$stage"
[[ -f $stage/backend/requirements.txt && -f $stage/backend/constraints.txt ]] || { echo 'release lacks pinned backend dependencies' >&2; exit 1; }
release_lock="$stage/ops/virtualoffice/worker-requirements.lock"
[[ -f $release_lock ]] && cmp -s "$LOCK" "$release_lock" || { echo 'wheelhouse lock is not the audited revision lock' >&2; exit 1; }
unsafe_link=0
while IFS= read -r -d '' link; do
  # CPython's Linux venv creates this one relative compatibility link even
  # with --copies.  It remains inside the root-owned snapshot.
  [[ $link == "$stage/backend/.venv/lib64" && $(readlink "$link") == lib ]] || unsafe_link=1
done < <(find "$stage" -type l -print0)
(( unsafe_link == 0 )) || { echo 'release symlink is forbidden' >&2; exit 1; }
"$PYTHON" -m venv --copies "$stage/backend/.venv"
chmod 0755 "$stage" "$stage/backend"
chown -R "$BUILD_USER" "$stage/backend/.venv"
runuser -u "$BUILD_USER" -- "$stage/backend/.venv/bin/pip" install --only-binary=:all: --no-index --find-links "$WHEELHOUSE" --require-hashes --no-deps -r "$LOCK"
runuser -u "$BUILD_USER" -- "$stage/backend/.venv/bin/pip" check
lock_digest=$(sha256sum "$LOCK" | cut -d' ' -f1)
wheelhouse_digest=$(cd "$WHEELHOUSE" && find . -type f -printf '%P\n' | LC_ALL=C sort | while IFS= read -r wheel; do sha256sum "$wheel"; done | sha256sum | cut -d' ' -f1)
printf '{"schema":"axioma-office-invitation-worker-release/v1","source_revision":"%s","lock_sha256":"%s","wheelhouse_manifest_sha256":"%s"}\n' "$REVISION" "$lock_digest" "$wheelhouse_digest" > "$stage/release-manifest.json"
chown -R root:root "$stage"
find "$stage" -type d -exec chmod 0755 {} +
find "$stage" -type f -exec chmod 0644 {} +
chmod 0755 "$stage/backend/.venv/bin/python"
while IFS= read -r -d '' path; do
  [[ -L $path ]] && continue
  [[ $(stat -c '%U:%G' "$path") == root:root ]] || { echo 'release has nonroot content' >&2; exit 1; }
  mode=$(stat -c '%a' "$path")
  (( (8#$mode & 0022) == 0 )) || { echo "release has writable content: $path" >&2; exit 1; }
done < <(find "$stage" -xdev -print0)
reference=$(mktemp -d "$parent/.axioma-office-invitation-worker.reference.XXXXXX")
GIT_NO_REPLACE_OBJECTS=1 git -C "$trusted_repo" archive --format=tar "$REVISION" | tar -x -C "$reference"
diff -qr -x .venv -x release-manifest.json "$reference" "$stage" >/dev/null || { echo 'builder changed archived application bytes' >&2; exit 1; }
rm -rf "$reference"; reference=
unsafe_link=0
while IFS= read -r -d '' link; do
  [[ $link == "$stage/backend/.venv/lib64" && $(readlink "$link") == lib ]] || unsafe_link=1
done < <(find "$stage" -type l -print0)
(( unsafe_link == 0 )) || { echo 'release symlink is forbidden' >&2; exit 1; }
if systemctl is-active --quiet "$timer" || systemctl is-active --quiet "$service"; then
  echo 'stop the worker timer and service before replacing its release' >&2; exit 1
fi
if [[ -e $RELEASE ]]; then mv "$RELEASE" "${RELEASE}.previous.$(date +%s)"; fi
mv "$stage" "$RELEASE"
stage=
printf '%s\n' 'root-owned invitation worker release installed; verify readiness before enabling the timer'
