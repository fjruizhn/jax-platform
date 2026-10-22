#!/usr/bin/env bash
# Root-only installer. It never replaces an existing authority body.
set -euo pipefail
DB= ADMIN_TRANSPORT=
while [[ $# -gt 0 ]]; do case "$1" in --db) DB=${2:?}; shift;; --admin-transport) ADMIN_TRANSPORT=${2:?}; shift;; *) exit 2;; esac; shift; done
[[ $DB =~ ^[A-Za-z0-9_]{1,64}$ && -n $ADMIN_TRANSPORT && -x $ADMIN_TRANSPORT ]] || exit 2
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
count=$(printf "SELECT COUNT(*) FROM information_schema.ROUTINES WHERE ROUTINE_SCHEMA='%s' AND ROUTINE_TYPE='PROCEDURE' AND ROUTINE_NAME IN ('office_invitation_claim_next_v1','office_invitation_expire_due_v1','office_invitation_settle_delivery_v1');\n" "$DB" | "$ADMIN_TRANSPORT")
case "$count" in
  0) { printf 'USE `%s`;\n' "$DB"; cat "$ROOT/ops/virtualoffice/office-invitation-delivery-v1.sql"; } | "$ADMIN_TRANSPORT" ;;
  1|2) printf '%s\n' 'partial invitation delivery component is denied' >&2; exit 1 ;;
  3) JAX_INVITATION_WORKER_DB_USER=${JAX_INVITATION_WORKER_DB_USER:-jaxinvite} "$ROOT/ops/virtualoffice/attest-invitation-delivery-routines.sh" --db "$DB" --admin-transport "$ADMIN_TRANSPORT" ;;
  *) printf '%s\n' 'ambiguous invitation delivery component is denied' >&2; exit 1 ;;
esac
