#!/usr/bin/env bash
# Read-only root smoke for the fixed production admin transport.
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
HELPER="$ROOT_DIR/ops/virtualoffice/office-mariadb-admin-transport.sh"
[[ $(id -u) == 0 ]] || { printf '%s\n' 'run this smoke with sudo -n only' >&2; exit 2; }
[[ -x $HELPER && ! -L $HELPER ]] || { printf '%s\n' 'admin transport is not executable' >&2; exit 2; }
docker inspect mariadb-12-3-jax >/dev/null
[[ $(printf 'SELECT 1;\n' | "$HELPER") == 1 ]] || { printf '%s\n' 'admin transport did not return the read-only probe' >&2; exit 1; }
printf '%s\n' 'office MariaDB admin transport smoke passed'
