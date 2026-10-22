#!/usr/bin/env bash
# Root-only installer. It never replaces an existing authority body.
set -euo pipefail
DB= ADMIN_TRANSPORT= WORKER_DB_USER=jaxinvite WORKER_HOST= SQL_FILE= ATTESTOR=
while [[ $# -gt 0 ]]; do case "$1" in --db) DB=${2:?}; shift;; --admin-transport) ADMIN_TRANSPORT=${2:?}; shift;; --worker-db-user) WORKER_DB_USER=${2:?}; shift;; --worker-host) WORKER_HOST=${2:?}; shift;; --sql-file) SQL_FILE=${2:?}; shift;; --attestor) ATTESTOR=${2:?}; shift;; *) exit 2;; esac; shift; done
[[ $DB =~ ^[A-Za-z0-9_]{1,64}$ && -n $ADMIN_TRANSPORT && -x $ADMIN_TRANSPORT && $WORKER_DB_USER =~ ^[a-z][a-z0-9_]{0,31}$ && $WORKER_HOST =~ ^[A-Za-z0-9._-]{1,255}$ ]] || exit 2
# This helper is installed in /usr/local/libexec.  Never derive an authority
# helper from its own path: there is no audited repository under /usr.
[[ -f $SQL_FILE && ! -L $SQL_FILE && $(stat -c %U:%G:%a "$SQL_FILE") == root:root:644 && $(sha256sum "$SQL_FILE" | awk '{print $1}') == 39ebe406f1a42e8daac93fde3bd6427cbaaee1bcbbad1ac0ff2cd0438710c32d ]] || { echo "unsafe routine SQL" >&2; exit 2; }
[[ -f $ATTESTOR && ! -L $ATTESTOR && -x $ATTESTOR && $(stat -c %U:%G:%a "$ATTESTOR") == root:root:750 ]] || { echo "unsafe routine attestor" >&2; exit 2; }
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
  3) : ;;
  *) printf '%s\n' 'ambiguous invitation delivery component is denied' >&2; exit 1 ;;
esac
# MariaDB requires the locked SQL SECURITY DEFINER account to hold EXECUTE on
# every procedure it defines. Install the routines first, then grant exactly
# those procedures; repeated installation cannot widen that authority.
owner_principal="'jaxoffice_invitation_owner'@'localhost'"
for routine in office_invitation_claim_next_v1 office_invitation_expire_due_v1 office_invitation_settle_delivery_v1; do
  printf "GRANT EXECUTE ON PROCEDURE \`%s\`.\`%s\` TO %s;\n" "$DB" "$routine" "$owner_principal" | "$ADMIN_TRANSPORT"
done
# Every successful path, including a fresh component, must attest its complete
# body, metadata, and the two closed privilege sets before reporting success.
JAX_INVITATION_WORKER_DB_USER=$WORKER_DB_USER "$ATTESTOR" --db "$DB" --admin-transport "$ADMIN_TRANSPORT" --worker-host "$WORKER_HOST"
