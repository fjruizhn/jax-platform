#!/usr/bin/env bash
# Invoke a separately installed bootstrap against a root-owned package. The
# bootstrap and its effective-contract validator are intentionally outside the
# package: this shell never executes a helper supplied by the package itself.
set -euo pipefail
PATH=/usr/sbin:/usr/bin:/sbin:/bin

if [[ $(/usr/bin/id -u) != 0 || $# != 12 ]]; then
  echo 'usage: install-platform-pair-runtime.sh --package DIR --expected-package-digest SHA256 --bootstrap FILE --bootstrap-sha256 SHA256 --effective-validator FILE --effective-validator-sha256 SHA256' >&2
  exit 2
fi

[[ $1 == --package && $3 == --expected-package-digest && $5 == --bootstrap && $7 == --bootstrap-sha256 && $9 == --effective-validator && ${11} == --effective-validator-sha256 ]] || {
  echo 'invalid runtime bootstrap arguments' >&2
  exit 2
}
PACKAGE=$2
PACKAGE_DIGEST=$4
BOOTSTRAP=$6
BOOTSTRAP_DIGEST=$8
VALIDATOR=${10}
VALIDATOR_DIGEST=${12}

[[ $PACKAGE == /* && -d $PACKAGE && ! -L $PACKAGE ]] || { echo 'package must be an absolute directory' >&2; exit 1; }
for digest in "$PACKAGE_DIGEST" "$BOOTSTRAP_DIGEST" "$VALIDATOR_DIGEST"; do
  [[ $digest =~ ^[0-9a-f]{64}$ ]] || { echo 'digest must be lowercase SHA-256' >&2; exit 1; }
done
for program in "$BOOTSTRAP" "$VALIDATOR"; do
  [[ $program == /* && -f $program && ! -L $program && $(stat -c '%U:%G' "$program") == root:root && $((8#$(stat -c '%a' "$program") & 0022)) == 0 ]] || {
    echo 'bootstrap dependency must be absolute, root-owned and non-writable' >&2
    exit 1
  }
done
exec {bootstrap_fd}<"$BOOTSTRAP"
[[ $(/usr/bin/sha256sum -- "/proc/self/fd/$bootstrap_fd" | /usr/bin/awk '{print $1}') == "$BOOTSTRAP_DIGEST" ]] || { echo 'bootstrap digest mismatch' >&2; exit 1; }

backup_parent=/var/lib/jax-platform-pair/runtime-imports
/usr/bin/install -d -o root -g root -m 0700 "$backup_parent"
backup=$(/usr/bin/mktemp -d "$backup_parent/runtime.XXXXXXXX")
exec /usr/bin/python3 -I -S "/proc/self/fd/$bootstrap_fd" import \
  --package "$PACKAGE" --expected-package-digest "$PACKAGE_DIGEST" --backup "$backup" \
  --effective-validator "$VALIDATOR" --effective-validator-sha256 "$VALIDATOR_DIGEST"
