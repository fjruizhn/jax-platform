#!/usr/bin/env bash
# Run as the unprivileged release builder.  Root later verifies the emitted
# archive digests; it never reads or executes a mutable checkout.
set -euo pipefail
PATH=/usr/sbin:/usr/bin:/sbin:/bin
[[ $(/usr/bin/id -u) != 0 ]] || { echo 'source preparation must not run as root' >&2; exit 2; }
REPO=${1:?repository}; REVISION=${2:?full revision}; OUTPUT=${3:?output tar}
[[ $REVISION =~ ^[0-9a-f]{40}$ && -d $REPO/.git && ! -e $OUTPUT ]] || exit 2
/usr/bin/git -C "$REPO" cat-file -e "$REVISION^{commit}"
[[ $(/usr/bin/git -C "$REPO" rev-parse "$REVISION^{commit}") == "$REVISION" ]] || exit 1
/usr/bin/git -C "$REPO" archive --format=tar "$REVISION" >"$OUTPUT"
/usr/bin/sha256sum "$OUTPUT"
