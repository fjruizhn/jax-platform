#!/usr/bin/env bash
# Structural/body attestation for the human-account delivery capability.
set -euo pipefail
DB= ADMIN_TRANSPORT= WORKER_HOST=localhost
while [[ $# -gt 0 ]]; do case "$1" in --db) DB=${2:?}; shift;; --admin-transport) ADMIN_TRANSPORT=${2:?}; shift;; --worker-host) WORKER_HOST=${2:?}; shift;; *) exit 2;; esac; shift; done
[[ $DB =~ ^[A-Za-z0-9_]{1,64}$ && $WORKER_HOST =~ ^[A-Za-z0-9.:-]{1,255}$ && -x ${ADMIN_TRANSPORT:-/missing} ]] || exit 2
q() { printf '%s\n' "$1" | "$ADMIN_TRANSPORT"; }
names="'office_account_invitation_claim_next_v1','office_account_invitation_expire_due_v1','office_account_invitation_settle_delivery_v1','office_account_invitation_settle_stale_v1'"
[[ $(q "SELECT COUNT(*) FROM information_schema.ROUTINES WHERE ROUTINE_SCHEMA='$DB' AND ROUTINE_TYPE='PROCEDURE' AND ROUTINE_NAME IN ($names);") == 4 ]] || { echo 'account delivery component incomplete' >&2; exit 1; }
mode='STRICT_ALL_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_AUTO_CREATE_USER,NO_ENGINE_SUBSTITUTION'
metadata=$(q "SELECT DEFINER,SECURITY_TYPE,SQL_DATA_ACCESS,SQL_MODE FROM information_schema.ROUTINES WHERE ROUTINE_SCHEMA='$DB' AND ROUTINE_NAME IN ($names);")
while IFS=$'\t' read -r definer security access sqlmode; do [[ $definer == jaxoffice_invitation_owner@localhost && $security == DEFINER && $access == 'MODIFIES SQL DATA' && $sqlmode == "$mode" ]] || { echo 'account delivery routine metadata drift' >&2; exit 1; }; done <<<"$metadata"
declare -A expected=(
 [office_account_invitation_claim_next_v1]=8163c7c2c0c3197b8dbc5f3e7db89944aa4768e57072d867b176c18f7e6391c4
 [office_account_invitation_expire_due_v1]=3461ee4dbae376f423060fdb26f4325867a835fc5c02cedbca2440f9f04bc3f9
 [office_account_invitation_settle_delivery_v1]=679668c9db3456def20791077e181ec6800c30f45f16c82adbc54d02e558682b
 [office_account_invitation_settle_stale_v1]=eba4f4c3bec579961ea26be1bb2c432ca4695f39458cb4d555cb34d927a89bd1
)
for routine in "${!expected[@]}"; do actual=$(q "USE \`$DB\`; SHOW CREATE PROCEDURE \`$routine\`;" | sha256sum | awk '{print $1}'); [[ $actual == "${expected[$routine]}" ]] || { echo 'account delivery routine body drift' >&2; exit 1; }; done
owner=$(q "SELECT CONCAT(User,'@',Host,':',JSON_UNQUOTE(JSON_EXTRACT(Priv,'$.account_locked'))) FROM mysql.global_priv WHERE User='jaxoffice_invitation_owner' AND Host='localhost';")
[[ $owner == 'jaxoffice_invitation_owner@localhost:true' ]] || { echo 'account delivery owner is not locked' >&2; exit 1; }
worker=${JAX_INVITATION_WORKER_DB_USER:-jaxinvite}
grants=$(q "SHOW GRANTS FOR '$worker'@'$WORKER_HOST';" | tr '\140' '\047')
for routine in "${!expected[@]}"; do grep -Fqx "GRANT EXECUTE ON PROCEDURE '$DB'.'$routine' TO '$worker'@'$WORKER_HOST'" <<<"$grants" || { echo 'account delivery worker grant drift' >&2; exit 1; }; done
