#!/bin/bash -p
# Root adoption only: it never interprets the builder tree or runs its code.
set -euo pipefail
SOURCE=${1:?builder output}; DEST=${2:?root release destination}
[[ $(/usr/bin/id -u) == 0 && -d $SOURCE && ! -e $DEST ]] || exit 2
[[ ! -L $SOURCE && $(/usr/bin/stat -c '%h' "$SOURCE") == 1 ]] || { echo 'unsafe source root' >&2; exit 1; }
for p in $(/usr/bin/find "$SOURCE" -xdev -print); do
  [[ ! -L $p && $(/usr/bin/stat -c '%h' "$p") == 1 ]] || { echo 'symlink or hardlink' >&2; exit 1; }
  /usr/bin/getfattr -d --absolute-names "$p" 2>/dev/null | /usr/bin/grep -q '^# file:' || true
  /usr/bin/getfacl -cp "$p" | /usr/bin/grep -q '^user:' || { echo 'acl unreadable' >&2; exit 1; }
done
[[ -f $SOURCE/build-attestation.json && -f $SOURCE/jax-platform/backend/.venv/bin/python ]] || exit 1
/usr/bin/mv -- "$SOURCE" "$DEST"
/usr/bin/chown -R root:root "$DEST"; /usr/bin/find "$DEST" -type d -exec /usr/bin/chmod 0755 {} +; /usr/bin/find "$DEST" -type f -exec /usr/bin/chmod 0644 {} +; /usr/bin/chmod 0755 "$DEST/jax-platform/backend/.venv/bin/python"
