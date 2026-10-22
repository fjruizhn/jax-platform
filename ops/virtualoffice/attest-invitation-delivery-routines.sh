#!/usr/bin/env bash
# Root-admin attestation for the immutable delivery-v1 SQL authority.
set -euo pipefail
DB= ADMIN_TRANSPORT=
while [[ $# -gt 0 ]]; do case "$1" in --db) DB=${2:?}; shift;; --admin-transport) ADMIN_TRANSPORT=${2:?}; shift;; *) exit 2;; esac; shift; done
[[ $DB =~ ^[A-Za-z0-9_]{1,64}$ && -x ${ADMIN_TRANSPORT:-/missing} ]] || exit 2
q() { printf '%s\n' "$1" | "$ADMIN_TRANSPORT"; }
read -r count <<<"$(q "SELECT COUNT(*) FROM information_schema.ROUTINES WHERE ROUTINE_SCHEMA='$DB' AND ROUTINE_TYPE='PROCEDURE' AND ROUTINE_NAME IN ('office_invitation_claim_next_v1','office_invitation_expire_due_v1','office_invitation_settle_delivery_v1');")"
[[ $count == 3 ]] || { echo 'delivery routine component incomplete' >&2; exit 1; }
expected_mode='STRICT_ALL_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_AUTO_CREATE_USER,NO_ENGINE_SUBSTITUTION'
while IFS=$'\t' read -r name definer security access mode; do
 [[ $definer == jaxoffice_invitation_owner@localhost && $security == DEFINER && $access == "MODIFIES SQL DATA" && $mode == "$expected_mode" ]] || { echo 'delivery routine metadata drift' >&2; exit 1; }
done < <(q "SELECT ROUTINE_NAME,DEFINER,SECURITY_TYPE,SQL_DATA_ACCESS,SQL_MODE FROM information_schema.ROUTINES WHERE ROUTINE_SCHEMA='$DB' AND ROUTINE_TYPE='PROCEDURE' AND ROUTINE_NAME IN ('office_invitation_claim_next_v1','office_invitation_expire_due_v1','office_invitation_settle_delivery_v1') ORDER BY ROUTINE_NAME;")
owner=$(q "SELECT CONCAT(User,'@',Host,':',JSON_UNQUOTE(JSON_EXTRACT(Priv,'$.account_locked'))) FROM mysql.global_priv WHERE User='jaxoffice_invitation_owner' AND Host='localhost';")
[[ $owner == 'jaxoffice_invitation_owner@localhost:true' ]] || { echo 'delivery definer is not locked' >&2; exit 1; }
# Parameter shape is authority: a same-named procedure with a widened input is
# rejected before an unprivileged worker can execute it.
params=$(q "SELECT SPECIFIC_NAME,ORDINAL_POSITION,PARAMETER_MODE,PARAMETER_NAME,DTD_IDENTIFIER,CHARACTER_SET_NAME,COLLATION_NAME FROM information_schema.PARAMETERS WHERE SPECIFIC_SCHEMA='$DB' AND SPECIFIC_NAME IN ('office_invitation_claim_next_v1','office_invitation_expire_due_v1','office_invitation_settle_delivery_v1') ORDER BY SPECIFIC_NAME,ORDINAL_POSITION;")
expected_params=$'office_invitation_claim_next_v1\t1\tOUT\tp_disposition\tvarchar(16)\tascii\tascii_bin\noffice_invitation_claim_next_v1\t2\tOUT\tp_outbox_id\tchar(36)\tascii\tascii_bin\noffice_invitation_claim_next_v1\t3\tOUT\tp_attempt_id\tchar(36)\tascii\tascii_bin\noffice_invitation_claim_next_v1\t4\tOUT\tp_invitation_id\tchar(36)\tascii\tascii_bin\noffice_invitation_claim_next_v1\t5\tOUT\tp_payload\tmediumblob\tNULL\tNULL\noffice_invitation_expire_due_v1\t1\tIN\tp_limit\tsmallint(5) unsigned\tNULL\tNULL\noffice_invitation_expire_due_v1\t2\tOUT\tp_expired\tsmallint(5) unsigned\tNULL\tNULL\noffice_invitation_settle_delivery_v1\t1\tIN\tp_outbox_id\tchar(36)\tascii\tascii_bin\noffice_invitation_settle_delivery_v1\t2\tIN\tp_attempt_id\tchar(36)\tascii\tascii_bin\noffice_invitation_settle_delivery_v1\t3\tIN\tp_result\tvarchar(16)\tascii\tascii_bin\noffice_invitation_settle_delivery_v1\t4\tIN\tp_message_id\tvarchar(320)\tascii\tascii_bin\noffice_invitation_settle_delivery_v1\t5\tOUT\tp_disposition\tvarchar(16)\tascii\tascii_bin'
[[ $params == "$expected_params" ]] || { echo 'delivery routine parameter drift' >&2; exit 1; }
