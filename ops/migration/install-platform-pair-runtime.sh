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
  dropin="/etc/systemd/system/$unit.d"
  [[ ! -e $dropin ]] || { echo "refusing existing drop-in: $dropin" >&2; exit 1; }
done

backup=$(/usr/bin/mktemp -d /var/tmp/jax-platform-pair-units.XXXXXX)
committed=0
rollback() {
  status=$?
  if [[ $committed == 0 ]]; then
    for unit in jax-platform.service jax-platform-migrate.service; do
      if [[ -e $backup/$unit.absent ]]; then /usr/bin/rm -f -- "/etc/systemd/system/$unit"; else /usr/bin/cp -a -- "$backup/$unit" "/etc/systemd/system/$unit"; fi
    done
    /usr/bin/systemctl daemon-reload || true
  fi
  /usr/bin/rm -rf -- "$backup"
  exit "$status"
}
trap rollback EXIT
for unit in jax-platform.service jax-platform-migrate.service; do
  if [[ -e /etc/systemd/system/$unit ]]; then /usr/bin/cp -a -- "/etc/systemd/system/$unit" "$backup/$unit"; else /usr/bin/touch "$backup/$unit.absent"; fi
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
/usr/bin/python3 -Es "$SOURCE/artifact_readiness.py" / /usr/bin/busctl
committed=1
trap - EXIT
/usr/bin/rm -rf -- "$backup"
echo 'installed immutable pair runtime; no release was activated'
