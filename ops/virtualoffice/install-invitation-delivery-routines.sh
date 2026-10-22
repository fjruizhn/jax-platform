#!/usr/bin/env bash
# Root-only installer. It never replaces an existing authority body.
set -euo pipefail
DB= ADMIN_TRANSPORT= WORKER_DB_USER=jaxinvite WORKER_HOST= SQL_FILE=
while [[ $# -gt 0 ]]; do case "$1" in --db) DB=${2:?}; shift;; --admin-transport) ADMIN_TRANSPORT=${2:?}; shift;; --worker-db-user) WORKER_DB_USER=${2:?}; shift;; --worker-host) WORKER_HOST=${2:?}; shift;; --sql-file) SQL_FILE=${2:?}; shift;; *) exit 2;; esac; shift; done
[[ $DB =~ ^[A-Za-z0-9_]{1,64}$ && -n $ADMIN_TRANSPORT && -x $ADMIN_TRANSPORT ]] || exit 2
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
SQL_FILE=${SQL_FILE:-"$SQL_FILE"}
[[ -f $SQL_FILE && ! -L $SQL_FILE && $(stat -c %U:%G:%a "$SQL_FILE") == root:root:644 ]] || { echo "unsafe routine SQL" >&2; exit 2; }
count=$(printf "SELECT COUNT(*) FROM information_schema.ROUTINES WHERE ROUTINE_SCHEMA='%s' AND ROUTINE_TYPE='PROCEDURE' AND ROUTINE_NAME IN ('office_invitation_claim_next_v1','office_invitation_expire_due_v1','office_invitation_settle_delivery_v1');\n" "$DB" | "$ADMIN_TRANSPORT")
case "$count" in
  0) { printf 'USE `%s`;\n' "$DB"; cat "$SQL_FILE"; } | "$ADMIN_TRANSPORT" ;;
  1)
    actual=$(printf 'USE `%s`; SHOW CREATE PROCEDURE `office_invitation_claim_next_v1`;\n' "$DB" | "$ADMIN_TRANSPORT" | sha256sum | awk '{print $1}')
    [[ $actual == 097e55aac42e0735ad8284053e663c90dfa354c455aefb5411fc07b75e7eaf0b ]] || { printf '%s\n' 'unknown invitation delivery prefix is denied' >&2; exit 1; }
    # SQL file has SET, DELIMITER, claim, expire, settle, DELIMITER.  Preserve
    # the mode and delimiter while emitting only the missing exact suffix.
    { sed -n '1,2p;4,6p' "$SQL_FILE"; } | { printf 'USE `%s`;\n' "$DB"; cat; } | "$ADMIN_TRANSPORT" ;;
  2) printf '%s\n' 'partial invitation delivery component is denied' >&2; exit 1 ;;
  3) [[ -n $WORKER_HOST ]] || { echo "worker host required" >&2; exit 2; }; JAX_INVITATION_WORKER_DB_USER=$WORKER_DB_USER "$ROOT/ops/virtualoffice/attest-invitation-delivery-routines.sh" --db "$DB" --admin-transport "$ADMIN_TRANSPORT" --worker-host "$WORKER_HOST" ;;
  *) printf '%s\n' 'ambiguous invitation delivery component is denied' >&2; exit 1 ;;
esac
