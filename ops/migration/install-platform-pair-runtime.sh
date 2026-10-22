#!/usr/bin/env bash
# Install the immutable, root-owned runtime surface.  This copies bytes only;
# activation is deliberately a separate operation with an externally supplied
# manifest digest.
set -euo pipefail
PATH=/usr/sbin:/usr/bin:/sbin:/bin

SOURCE=${1:-}
[[ $(/usr/bin/id -u) == 0 && -n $SOURCE && -d $SOURCE ]] || {
  echo 'usage: install-platform-pair-runtime.sh ROOT_OWNED_SOURCE_DIRECTORY' >&2
  exit 2
}
[[ ! -L $SOURCE && $(stat -c '%U:%G' "$SOURCE") == root:root && $((8#$(stat -c '%a' "$SOURCE") & 0022)) == 0 ]] || {
  echo 'runtime source must be root-owned and not writable by group or other' >&2
  exit 1
}

for file in pair_artifact_readiness.py pair-artifact-readiness.sh jax-platform-pair-exec \
            install-platform-pair-release.sh activate-platform-pair-release.sh; do
  [[ -f $SOURCE/$file && ! -L $SOURCE/$file ]] || { echo "missing trusted runtime file: $file" >&2; exit 1; }
done
for unit in jax-platform.service jax-platform-migrate.service; do
  [[ -f $SOURCE/systemd-units/$unit && ! -L $SOURCE/systemd-units/$unit ]] || { echo "missing unit: $unit" >&2; exit 1; }
  /usr/bin/systemd-analyze verify "$SOURCE/systemd-units/$unit"
done

/usr/bin/install -d -o root -g root -m 0755 /usr/local/libexec
/usr/bin/install -o root -g root -m 0644 "$SOURCE/pair_artifact_readiness.py" /usr/local/libexec/pair_artifact_readiness.py
for file in pair-artifact-readiness.sh jax-platform-pair-exec install-platform-pair-release.sh activate-platform-pair-release.sh; do
  /usr/bin/install -o root -g root -m 0755 "$SOURCE/$file" "/usr/local/libexec/$file"
done
for unit in jax-platform.service jax-platform-migrate.service; do
  /usr/bin/install -o root -g root -m 0644 "$SOURCE/systemd-units/$unit" "/etc/systemd/system/$unit"
done
/usr/bin/systemctl daemon-reload
/usr/bin/systemd-analyze verify /etc/systemd/system/jax-platform.service /etc/systemd/system/jax-platform-migrate.service
echo 'installed immutable pair runtime; no release was activated'
