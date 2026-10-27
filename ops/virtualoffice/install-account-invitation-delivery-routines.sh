#!/usr/bin/env bash
# Installs the separate Office-account delivery authority after the base worker
# principal has been provisioned.  It never starts or enables a timer.
set -euo pipefail
DB= ADMIN_TRANSPORT= WORKER_DB_USER=jaxinvite WORKER_HOST= SQL_FILE= ATTESTOR=
while [[ $# -gt 0 ]]; do case "$1" in --db) DB=${2:?}; shift;; --admin-transport) ADMIN_TRANSPORT=${2:?}; shift;; --worker-db-user) WORKER_DB_USER=${2:?}; shift;; --worker-host) WORKER_HOST=${2:?}; shift;; --sql-file) SQL_FILE=${2:?}; shift;; --attestor) ATTESTOR=${2:?}; shift;; *) exit 2;; esac; shift; done
[[ $DB =~ ^[A-Za-z0-9_]{1,64}$ && -x $ADMIN_TRANSPORT && $WORKER_DB_USER =~ ^[a-z][a-z0-9_]{0,31}$ && $WORKER_HOST =~ ^[A-Za-z0-9.:-]{1,255}$ && -f $SQL_FILE && ! -L $SQL_FILE && $(stat -c '%U:%G:%a' "$SQL_FILE") == root:root:644 && $(sha256sum "$SQL_FILE" | awk '{print $1}') == 7cc9d8a4f654f4d2cee9a0257a71021e90063d63847d25af7cf8a2cd6f7b7dc8 && -x $ATTESTOR ]] || exit 2
owner="'jaxoffice_invitation_owner'@'localhost'"; worker="'$WORKER_DB_USER'@'$WORKER_HOST'"
count=$(printf "SELECT COUNT(*) FROM information_schema.ROUTINES WHERE ROUTINE_SCHEMA='%s' AND ROUTINE_TYPE='PROCEDURE' AND ROUTINE_NAME IN ('office_account_invitation_claim_next_v1','office_account_invitation_expire_due_v1','office_account_invitation_settle_delivery_v1','office_account_invitation_settle_stale_v1');\n" "$DB" | "$ADMIN_TRANSPORT")
[[ $count == 0 || $count == 4 ]] || { echo 'partial account invitation delivery component denied' >&2; exit 1; }
if [[ $count == 0 ]]; then
  printf "GRANT SELECT ON \`%s\`.\`office_account_invitation\` TO %s;\nGRANT SELECT ON \`%s\`.\`office_account_invitation_outbox\` TO %s;\nGRANT UPDATE (status,version) ON \`%s\`.\`office_account_invitation\` TO %s;\nGRANT UPDATE (state,attempt_id,attempt_count,payload_ciphertext,message_id,result_code,claimed_at,finished_at) ON \`%s\`.\`office_account_invitation_outbox\` TO %s;\n" "$DB" "$owner" "$DB" "$owner" "$DB" "$owner" "$DB" "$owner" | "$ADMIN_TRANSPORT"
  { printf 'USE `%s`;\n' "$DB"; cat "$SQL_FILE"; } | "$ADMIN_TRANSPORT"
  for proc in office_account_invitation_claim_next_v1 office_account_invitation_expire_due_v1 office_account_invitation_settle_delivery_v1 office_account_invitation_settle_stale_v1; do
    printf "GRANT EXECUTE ON PROCEDURE \`%s\`.\`%s\` TO %s;\n" "$DB" "$proc" "$owner" | "$ADMIN_TRANSPORT"
    printf "GRANT EXECUTE ON PROCEDURE \`%s\`.\`%s\` TO %s;\n" "$DB" "$proc" "$worker" | "$ADMIN_TRANSPORT"
  done
fi
# Re-read the server metadata after the authority transition.  SQL SECURITY
# DEFINER and the locked capability owner are not inferred from source text.
metadata=$(printf "SELECT COUNT(*) FROM information_schema.ROUTINES WHERE ROUTINE_SCHEMA='%s' AND DEFINER='jaxoffice_invitation_owner@localhost' AND SECURITY_TYPE='DEFINER' AND SQL_DATA_ACCESS='MODIFIES SQL DATA' AND ROUTINE_NAME IN ('office_account_invitation_claim_next_v1','office_account_invitation_expire_due_v1','office_account_invitation_settle_delivery_v1','office_account_invitation_settle_stale_v1');\n" "$DB" | "$ADMIN_TRANSPORT")
[[ $metadata == 4 ]] || { echo 'account invitation routine metadata drift' >&2; exit 1; }
owner_grants=$(printf "SHOW GRANTS FOR 'jaxoffice_invitation_owner'@'localhost';\n" | "$ADMIN_TRANSPORT" | tr '\140' '\047')
worker_grants=$(printf "SHOW GRANTS FOR '%s'@'%s';\n" "$WORKER_DB_USER" "$WORKER_HOST" | "$ADMIN_TRANSPORT" | tr '\140' '\047')
for proc in office_account_invitation_claim_next_v1 office_account_invitation_expire_due_v1 office_account_invitation_settle_delivery_v1 office_account_invitation_settle_stale_v1; do
  grep -Fqx "GRANT EXECUTE ON PROCEDURE '$DB'.'$proc' TO 'jaxoffice_invitation_owner'@'localhost'" <<<"$owner_grants" || { echo 'account invitation owner grant drift' >&2; exit 1; }
  grep -Fqx "GRANT EXECUTE ON PROCEDURE '$DB'.'$proc' TO '$WORKER_DB_USER'@'$WORKER_HOST'" <<<"$worker_grants" || { echo 'account invitation worker grant drift' >&2; exit 1; }
done
JAX_INVITATION_WORKER_DB_USER=$WORKER_DB_USER "$ATTESTOR" --db "$DB" --admin-transport "$ADMIN_TRANSPORT" --worker-host "$WORKER_HOST"
printf '%s\n' 'account invitation delivery routines installed; run readiness before enabling timer'
