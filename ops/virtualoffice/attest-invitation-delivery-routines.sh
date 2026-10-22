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
