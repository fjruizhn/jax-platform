#!/usr/bin/env bash
# Fail closed before systemd starts a delivery-capable Python process.
set -euo pipefail

env_file=${1:-/etc/jax/invitation-worker.env}
[[ -f $env_file && ! -L $env_file && $(stat -c '%U:%G:%a' "$env_file") == root:root:600 ]] || {
  printf '%s\n' 'invitation worker env ownership or mode is unsafe' >&2; exit 1;
}
required=(JAX_DB_HOST JAX_DB_PORT JAX_DB_NAME JAX_DB_USER JAX_DB_PASSWORD FERNET_KEY JAX_ENVIRONMENT OFFICE_PUBLIC_ORIGIN OFFICE_INVITATION_LIFETIME_SECONDS OFFICE_INVITATION_CLAIM_TIMEOUT_SECONDS OFFICE_INVITATION_STALE_CLAIM_BATCH_LIMIT OFFICE_INVITATION_EXPIRY_SWEEP_BATCH_LIMIT OFFICE_INVITATION_DELIVERY_BATCH_LIMIT OFFICE_INVITATION_PUBLIC_BASE_URL)
for key in "${required[@]}"; do [[ -n ${!key:-} ]] || { printf '%s\n' "invitation worker environment lacks $key" >&2; exit 1; }; done
[[ $JAX_ENVIRONMENT == production && $OFFICE_INVITATION_PUBLIC_BASE_URL == "$OFFICE_PUBLIC_ORIGIN" ]] || {
  printf '%s\n' 'invitation worker origin binding is unsafe' >&2; exit 1;
}
[[ -z ${JAX_MIGRATE_DB_USER:-} && -z ${JAX_RUNTIME_DB_USER:-} ]] || {
  printf '%s\n' 'invitation worker env must not expose migration or web principal names' >&2; exit 1;
}
# The shared timer is only ready when both disjoint durable queues have their
# fenced delivery authority.  The check itself is SELECT-only under jaxinvite.
routine_count=$(MYSQL_PWD="$JAX_DB_PASSWORD" mariadb -h "$JAX_DB_HOST" -P "$JAX_DB_PORT" -u "$JAX_DB_USER" --batch --skip-column-names "$JAX_DB_NAME" -e "SELECT COUNT(*) FROM information_schema.ROUTINES WHERE ROUTINE_SCHEMA=DATABASE() AND ROUTINE_NAME IN ('office_invitation_claim_next_v1','office_invitation_expire_due_v1','office_invitation_settle_delivery_v1','office_account_invitation_claim_next_v1','office_account_invitation_expire_due_v1','office_account_invitation_settle_delivery_v1','office_account_invitation_settle_stale_v1')" 2>/dev/null) || {
  printf '%s\n' 'invitation worker routine readiness query failed' >&2; exit 1;
}
[[ $routine_count == 7 ]] || { printf '%s\n' 'invitation worker delivery authority is incomplete' >&2; exit 1; }
