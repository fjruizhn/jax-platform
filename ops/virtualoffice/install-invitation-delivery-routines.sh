#!/usr/bin/env bash
# Root-only installer; provision the locked definer first.
set -euo pipefail
DB= ADMIN_TRANSPORT=
while [[ $# -gt 0 ]]; do case "$1" in --db) DB=${2:?}; shift;; --admin-transport) ADMIN_TRANSPORT=${2:?}; shift;; *) exit 2;; esac; shift; done
[[ $DB =~ ^[A-Za-z0-9_]{1,64}$ && -n $ADMIN_TRANSPORT && -x $ADMIN_TRANSPORT ]] || exit 2
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
{ printf 'USE `%s`;\n' "$DB"; cat "$ROOT/ops/virtualoffice/office-invitation-delivery-v1.sql"; } | "$ADMIN_TRANSPORT"
