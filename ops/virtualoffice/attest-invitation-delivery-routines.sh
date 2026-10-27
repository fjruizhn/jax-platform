#!/usr/bin/env bash
# Root-admin attestation for the immutable delivery-v1 SQL authority.
set -euo pipefail
DB= ADMIN_TRANSPORT= WORKER_HOST=localhost STRUCTURAL=0
while [[ $# -gt 0 ]]; do case "$1" in --db) DB=${2:?}; shift;; --admin-transport) ADMIN_TRANSPORT=${2:?}; shift;; --worker-host) WORKER_HOST=${2:?}; shift;; --structural) STRUCTURAL=1;; *) exit 2;; esac; shift; done
[[ $DB =~ ^[A-Za-z0-9_]{1,64}$ && $WORKER_HOST =~ ^[A-Za-z0-9.:-]{1,255}$ && $WORKER_HOST != *'%'* && $WORKER_HOST != *'_'* && -x ${ADMIN_TRANSPORT:-/missing} ]] || exit 2
q() { printf '%s\n' "$1" | "$ADMIN_TRANSPORT"; }
read -r count <<<"$(q "SELECT COUNT(*) FROM information_schema.ROUTINES WHERE ROUTINE_SCHEMA='$DB' AND ROUTINE_TYPE='PROCEDURE' AND ROUTINE_NAME IN ('office_invitation_claim_next_v1','office_invitation_expire_due_v1','office_invitation_settle_delivery_v1');")"
[[ $count == 3 ]] || { echo 'delivery routine component incomplete' >&2; exit 1; }
expected_mode='STRICT_ALL_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_AUTO_CREATE_USER,NO_ENGINE_SUBSTITUTION'
metadata=$(q "SELECT ROUTINE_NAME,DEFINER,SECURITY_TYPE,SQL_DATA_ACCESS,SQL_MODE FROM information_schema.ROUTINES WHERE ROUTINE_SCHEMA='$DB' AND ROUTINE_TYPE='PROCEDURE' AND ROUTINE_NAME IN ('office_invitation_claim_next_v1','office_invitation_expire_due_v1','office_invitation_settle_delivery_v1') ORDER BY ROUTINE_NAME;")
[[ $(printf '%s\n' "$metadata" | wc -l) == 3 ]] || { echo 'delivery routine metadata incomplete' >&2; exit 1; }
while IFS=$'\t' read -r name definer security access mode; do
 [[ $definer == jaxoffice_invitation_owner@localhost && $security == DEFINER && $access == "MODIFIES SQL DATA" && $mode == "$expected_mode" ]] || { echo 'delivery routine metadata drift' >&2; exit 1; }
done <<<"$metadata"
owner=$(q "SELECT CONCAT(User,'@',Host,':',JSON_UNQUOTE(JSON_EXTRACT(Priv,'$.account_locked'))) FROM mysql.global_priv WHERE User='jaxoffice_invitation_owner' AND Host='localhost';")
[[ $owner == 'jaxoffice_invitation_owner@localhost:true' ]] || { echo 'delivery definer is not locked' >&2; exit 1; }
# Parameter shape is authority: a same-named procedure with a widened input is
# rejected before an unprivileged worker can execute it.
params=$(q "SELECT SPECIFIC_NAME,ORDINAL_POSITION,PARAMETER_MODE,PARAMETER_NAME,DTD_IDENTIFIER,CHARACTER_SET_NAME,COLLATION_NAME FROM information_schema.PARAMETERS WHERE SPECIFIC_SCHEMA='$DB' AND SPECIFIC_NAME IN ('office_invitation_claim_next_v1','office_invitation_expire_due_v1','office_invitation_settle_delivery_v1') ORDER BY SPECIFIC_NAME,ORDINAL_POSITION;")
expected_params=$'office_invitation_claim_next_v1\t1\tOUT\tp_disposition\tvarchar(16)\tascii\tascii_bin\noffice_invitation_claim_next_v1\t2\tOUT\tp_outbox_id\tchar(36)\tascii\tascii_bin\noffice_invitation_claim_next_v1\t3\tOUT\tp_attempt_id\tchar(36)\tascii\tascii_bin\noffice_invitation_claim_next_v1\t4\tOUT\tp_invitation_id\tchar(36)\tascii\tascii_bin\noffice_invitation_claim_next_v1\t5\tOUT\tp_payload\tmediumblob\tNULL\tNULL\noffice_invitation_expire_due_v1\t1\tIN\tp_limit\tsmallint(5) unsigned\tNULL\tNULL\noffice_invitation_expire_due_v1\t2\tOUT\tp_expired\tsmallint(5) unsigned\tNULL\tNULL\noffice_invitation_settle_delivery_v1\t1\tIN\tp_outbox_id\tchar(36)\tascii\tascii_bin\noffice_invitation_settle_delivery_v1\t2\tIN\tp_attempt_id\tchar(36)\tascii\tascii_bin\noffice_invitation_settle_delivery_v1\t3\tIN\tp_result\tvarchar(16)\tascii\tascii_bin\noffice_invitation_settle_delivery_v1\t4\tIN\tp_message_id\tvarchar(320)\tascii\tascii_bin\noffice_invitation_settle_delivery_v1\t5\tOUT\tp_disposition\tvarchar(16)\tascii\tascii_bin'
[[ $params == "$expected_params" ]] || { echo 'delivery routine parameter drift' >&2; exit 1; }
# SHOW CREATE is the body authority, including handler order and transition
# conditions. Hash the server's tab-separated canonical output.
declare -A expected_create=(
 [office_invitation_claim_next_v1]=097e55aac42e0735ad8284053e663c90dfa354c455aefb5411fc07b75e7eaf0b
 [office_invitation_expire_due_v1]=dc7b3e0f0478b9cb61379c65572e57420fdae31e7b5bac5abcf1a7bc6e6ff29b
 [office_invitation_settle_delivery_v1]=6fbe652d342915dbc9cd62e46c3eeeb3c478f77d809f23c1716ec0b751c40414
)
for routine in "${!expected_create[@]}"; do
 actual=$(q "USE \`$DB\`; SHOW CREATE PROCEDURE \`$routine\`;" | sha256sum | awk '{print $1}')
 [[ $actual == "${expected_create[$routine]}" ]] || { echo 'delivery routine body drift' >&2; exit 1; }
done
[[ $STRUCTURAL == 0 ]] || exit 0
# Both principals have closed grant sets. The locked definer needs table
# authority plus its own three procedures; the worker can only observe the
# queue and invoke those procedures.
owner_grants=$(q "SHOW GRANTS FOR 'jaxoffice_invitation_owner'@'localhost';" | tr '\140' '\047')
expected_owner=$(cat <<EOF
GRANT USAGE ON *.* TO 'jaxoffice_invitation_owner'@'localhost'
GRANT SELECT, UPDATE ('result_code', 'state', 'finished_at', 'attempt_id', 'updated_at', 'attempt_count', 'payload_ciphertext', 'message_id', 'claimed_at') ON '$DB'.'office_invitation_outbox' TO 'jaxoffice_invitation_owner'@'localhost'
GRANT SELECT ON '$DB'.'office_invitation_issuance' TO 'jaxoffice_invitation_owner'@'localhost'
GRANT SELECT, UPDATE ('version', 'status') ON '$DB'.'office_invitation' TO 'jaxoffice_invitation_owner'@'localhost'
GRANT SELECT, INSERT ON '$DB'.'office_invitation_event' TO 'jaxoffice_invitation_owner'@'localhost'
GRANT EXECUTE ON PROCEDURE '$DB'.'office_invitation_claim_next_v1' TO 'jaxoffice_invitation_owner'@'localhost'
GRANT EXECUTE ON PROCEDURE '$DB'.'office_invitation_expire_due_v1' TO 'jaxoffice_invitation_owner'@'localhost'
GRANT EXECUTE ON PROCEDURE '$DB'.'office_invitation_settle_delivery_v1' TO 'jaxoffice_invitation_owner'@'localhost'
EOF
)
account_count=$(q "SELECT COUNT(*) FROM information_schema.ROUTINES WHERE ROUTINE_SCHEMA='$DB' AND ROUTINE_NAME IN ('office_account_invitation_claim_next_v1','office_account_invitation_expire_due_v1','office_account_invitation_settle_delivery_v1','office_account_invitation_settle_stale_v1');")
if [[ $account_count == 4 ]]; then
 expected_owner+=$'\n'"GRANT SELECT, UPDATE ('version', 'status') ON '$DB'.'office_account_invitation' TO 'jaxoffice_invitation_owner'@'localhost'"$'\n'"GRANT SELECT, UPDATE ('result_code', 'state', 'finished_at', 'attempt_id', 'claimed_at', 'attempt_count', 'payload_ciphertext', 'message_id') ON '$DB'.'office_account_invitation_outbox' TO 'jaxoffice_invitation_owner'@'localhost'"
 for p in office_account_invitation_claim_next_v1 office_account_invitation_expire_due_v1 office_account_invitation_settle_delivery_v1 office_account_invitation_settle_stale_v1; do expected_owner+=$'\n'"GRANT EXECUTE ON PROCEDURE '$DB'.'$p' TO 'jaxoffice_invitation_owner'@'localhost'"; done
fi
[[ $(printf '%s\n' "$owner_grants" | sort) == $(printf '%s\n' "$expected_owner" | sort) ]] || { echo 'delivery definer grants are not exact' >&2; exit 1; }
# The delivery user has configuration/outbox observation plus exactly these
# procedures; any lifecycle/event DML is an authority escalation.
worker=${JAX_INVITATION_WORKER_DB_USER:-jaxinvite}
[[ $worker =~ ^[a-z][a-z0-9_]{0,31}$ ]] || { echo 'unsafe delivery worker principal' >&2; exit 1; }
grants=$(q "SHOW GRANTS FOR '$worker'@'$WORKER_HOST';" | sed -E "s/ IDENTIFIED BY (PASSWORD )?'[^']*'//g" | tr '\140' '\047')
expected_worker=$(cat <<EOF
GRANT USAGE ON *.* TO '$worker'@'$WORKER_HOST'
GRANT SELECT ('attempt_id', 'outbox_id', 'invitation_id', 'state', 'created_at', 'claimed_at') ON '$DB'.'office_invitation_outbox' TO '$worker'@'$WORKER_HOST'
GRANT SELECT ON '$DB'.'axioma_config' TO '$worker'@'$WORKER_HOST'
GRANT EXECUTE ON PROCEDURE '$DB'.'office_invitation_claim_next_v1' TO '$worker'@'$WORKER_HOST'
GRANT EXECUTE ON PROCEDURE '$DB'.'office_invitation_expire_due_v1' TO '$worker'@'$WORKER_HOST'
GRANT EXECUTE ON PROCEDURE '$DB'.'office_invitation_settle_delivery_v1' TO '$worker'@'$WORKER_HOST'
EOF
)
if [[ $account_count == 4 ]]; then
 for p in office_account_invitation_claim_next_v1 office_account_invitation_expire_due_v1 office_account_invitation_settle_delivery_v1 office_account_invitation_settle_stale_v1; do expected_worker+=$'\n'"GRANT EXECUTE ON PROCEDURE '$DB'.'$p' TO '$worker'@'$WORKER_HOST'"; done
fi
[[ $(printf '%s\n' "$grants" | sort) == $(printf '%s\n' "$expected_worker" | sort) ]] || { echo 'delivery worker grants are not exact' >&2; exit 1; }
printf '%s\n' "$grants" | grep -E "(UPDATE|INSERT|DELETE).*office_invitation|SELECT.*office_invitation(_issuance|_event)?['\`]" >/dev/null && { echo 'delivery direct lifecycle privilege denied' >&2; exit 1; }
# Definer is a locked, role-free capability account; inherited roles would
# make SHOW GRANTS insufficient as an authority boundary.
role_count=$(q "SELECT COUNT(*) FROM mysql.roles_mapping WHERE User='jaxoffice_invitation_owner' AND Host='localhost';")
[[ $role_count == 0 ]] || { echo 'delivery definer roles denied' >&2; exit 1; }
# No additional delivery-named stored program may be executable by the worker.
visible=$(printf '%s\n' "$grants" | grep -c 'EXECUTE ON PROCEDURE')
[[ $visible == $((3 + account_count)) ]] || { echo 'delivery worker routine privilege set is not exact' >&2; exit 1; }
