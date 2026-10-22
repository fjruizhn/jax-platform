#!/usr/bin/env bash
# Fail closed before systemd starts a delivery-capable Python process.
set -euo pipefail

env_file=${1:-/etc/jax/invitation-worker.env}
[[ -f $env_file && ! -L $env_file && $(stat -c '%U:%G:%a' "$env_file") == root:root:600 ]] || {
  printf '%s\n' 'invitation worker env ownership or mode is unsafe' >&2; exit 1;
}
required=(JAX_DB_HOST JAX_DB_PORT JAX_DB_NAME JAX_DB_USER JAX_DB_PASSWORD FERNET_KEY OFFICE_INVITATION_LIFETIME_SECONDS OFFICE_INVITATION_CLAIM_TIMEOUT_SECONDS OFFICE_INVITATION_STALE_CLAIM_BATCH_LIMIT OFFICE_INVITATION_EXPIRY_SWEEP_BATCH_LIMIT OFFICE_INVITATION_DELIVERY_BATCH_LIMIT OFFICE_INVITATION_PUBLIC_BASE_URL)
for key in "${required[@]}"; do [[ -n ${!key:-} ]] || { printf '%s\n' "invitation worker environment lacks $key" >&2; exit 1; }; done
[[ -z ${JAX_MIGRATE_DB_USER:-} && -z ${JAX_RUNTIME_DB_USER:-} ]] || {
  printf '%s\n' 'invitation worker env must not expose migration or web principal names' >&2; exit 1;
}
