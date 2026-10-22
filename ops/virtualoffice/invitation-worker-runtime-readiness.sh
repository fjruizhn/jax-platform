#!/usr/bin/env bash
# Runs as jaxinvite.  It rejects an executable release that any service user
# could replace, and does not read the root-only EnvironmentFile itself.
set -euo pipefail
deep=${1:-}
[[ -z $deep || $deep == --deep ]] || { echo 'usage: invitation-worker-runtime-readiness.sh [--deep]' >&2; exit 2; }
release=/opt/axioma-office-invitation-worker
python="$release/backend/.venv/bin/python"
manifest="$release/release-manifest.json"
for path in /opt "$release" "$release/backend" "$release/backend/virtual_office" "$python" "$manifest"; do
  [[ ! -L $path && -e $path ]] || { echo 'worker runtime path is absent or a symlink' >&2; exit 1; }
  [[ $(stat -c '%U:%G:%a' "$path") =~ ^root:root:([0-7][0-7][0-7])$ ]] || { echo 'worker runtime ownership is unsafe' >&2; exit 1; }
  mode=$(stat -c '%a' "$path")
  (( (8#$mode & 0022) == 0 )) || { echo 'worker runtime is writable by group or other' >&2; exit 1; }
done
if [[ $deep == --deep ]]; then
  unsafe_link=0
  while IFS= read -r -d '' link; do
    [[ $link == "$release/backend/.venv/lib64" && $(readlink "$link") == lib ]] || unsafe_link=1
  done < <(find "$release" -xdev -type l -print0)
  (( unsafe_link == 0 )) || { echo 'worker release contains a forbidden symlink' >&2; exit 1; }
  while IFS= read -r -d '' path; do
    [[ -L $path ]] && continue
    [[ $(stat -c '%U:%G' "$path") == root:root ]] || { echo 'worker release has nonroot content' >&2; exit 1; }
    mode=$(stat -c '%a' "$path")
    (( (8#$mode & 0022) == 0 )) || { echo 'worker release has writable content' >&2; exit 1; }
  done < <(find "$release" -xdev -print0)
fi
grep -Eq '^\{"schema":"axioma-office-invitation-worker-release/v1","source_revision":"[0-9a-f]{40}","lock_sha256":"[0-9a-f]{64}","wheelhouse_manifest_sha256":"[0-9a-f]{64}"\}$' "$manifest" || {
  echo 'worker release manifest is invalid' >&2; exit 1;
}
required=(JAX_DB_HOST JAX_DB_PORT JAX_DB_NAME JAX_DB_USER JAX_DB_PASSWORD FERNET_KEY OFFICE_INVITATION_LIFETIME_SECONDS OFFICE_INVITATION_CLAIM_TIMEOUT_SECONDS OFFICE_INVITATION_STALE_CLAIM_BATCH_LIMIT OFFICE_INVITATION_EXPIRY_SWEEP_BATCH_LIMIT OFFICE_INVITATION_DELIVERY_BATCH_LIMIT OFFICE_INVITATION_PUBLIC_BASE_URL)
for key in "${required[@]}"; do [[ -n ${!key:-} ]] || { echo "invitation worker environment lacks $key" >&2; exit 1; }; done
[[ -z ${JAX_MIGRATE_DB_USER:-} && -z ${JAX_RUNTIME_DB_USER:-} ]] || { echo 'invitation worker env exposes a nonworker principal' >&2; exit 1; }
