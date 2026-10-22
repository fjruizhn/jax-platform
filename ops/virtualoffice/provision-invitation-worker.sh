#!/usr/bin/env bash
# Provision the least-privileged OS and database principal for invitation mail.
# Run before and after the Office migration: the locked definer must exist
# before CREATE PROCEDURE; a second idempotent run grants EXECUTE afterwards.  It does not issue invitations and never starts the timer.
set -euo pipefail
umask 077

ENV_FILE=/etc/jax/runtime.env
STATE_ROOT=/etc/jax
ADMIN_TRANSPORT=
PREPARE_OS=1
WORKER_DB_USER=jaxinvite
WORKER_OS_USER=jaxinvite
MEASURED_GRANTEE_HOST=
while [[ $# -gt 0 ]]; do
  case "$1" in
    --env) ENV_FILE=${2:?}; shift ;;
    --state-root) STATE_ROOT=${2:?}; shift ;;
    --admin-transport) ADMIN_TRANSPORT=${2:?}; shift ;;
    --skip-os-identities) PREPARE_OS=0 ;;
    --worker-db-user) WORKER_DB_USER=${2:?}; shift ;;
    --worker-os-user) WORKER_OS_USER=${2:?}; shift ;;
    --measured-grantee-host) MEASURED_GRANTEE_HOST=${2:?}; shift ;;
    -h|--help) printf '%s\n' 'usage: provision-invitation-worker.sh --measured-grantee-host HOST [--env FILE] [--state-root DIR] [--admin-transport FILE] [--skip-os-identities] [--worker-db-user USER] [--worker-os-user USER]'; exit 0 ;;
    *) printf '%s\n' "unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done
[[ $(id -u) == 0 ]] || { printf '%s\n' 'must run as root' >&2; exit 1; }
[[ "$WORKER_DB_USER" =~ ^[a-z][a-z0-9_]{0,31}$ && "$WORKER_OS_USER" =~ ^[a-z][a-z0-9_-]{0,31}$ ]] || { printf '%s\n' 'unsafe worker user name' >&2; exit 1; }
[[ -n "$MEASURED_GRANTEE_HOST" ]] || { printf '%s\n' 'an explicit measured grantee host is required' >&2; exit 1; }
[[ "$MEASURED_GRANTEE_HOST" != *%* && "$MEASURED_GRANTEE_HOST" != *_* && "$MEASURED_GRANTEE_HOST" =~ ^[A-Za-z0-9.:-]+$ ]] || {
  printf '%s\n' 'invalid measured grantee host' >&2; exit 1;
}
[[ -f $ENV_FILE && ! -L $ENV_FILE && -r $ENV_FILE && $(stat -c '%U:%G' "$ENV_FILE") == root:root ]] || {
  printf '%s\n' 'runtime environment is not a regular root-owned file' >&2; exit 1;
}
env_mode=$(stat -c '%a' "$ENV_FILE")
(( (8#$env_mode & 0022) == 0 )) || { printf '%s\n' 'runtime environment is writable by group or others' >&2; exit 1; }
declare -A cfg=()
while IFS= read -r line || [[ -n "$line" ]]; do
  [[ -z "$line" || "$line" == \#* ]] && continue
  [[ "$line" == *=* ]] || { printf '%s\n' 'unsafe runtime environment' >&2; exit 1; }
  key=${line%%=*}; value=${line#*=}
  [[ "$key" =~ ^[A-Z][A-Z0-9_]*$ && -n "$value" && "$value" != *[[:space:]#\\\"\']* ]] || {
    printf '%s\n' 'unsafe runtime environment' >&2; exit 1;
  }
  cfg[$key]=$value
done < "$ENV_FILE"
for key in JAX_DB_HOST JAX_DB_PORT JAX_DB_NAME FERNET_KEY JAX_ENVIRONMENT OFFICE_PUBLIC_ORIGIN OFFICE_INVITATION_LIFETIME_SECONDS OFFICE_INVITATION_CLAIM_TIMEOUT_SECONDS OFFICE_INVITATION_STALE_CLAIM_BATCH_LIMIT OFFICE_INVITATION_EXPIRY_SWEEP_BATCH_LIMIT OFFICE_INVITATION_DELIVERY_BATCH_LIMIT OFFICE_INVITATION_PUBLIC_BASE_URL; do
  [[ -n ${cfg[$key]:-} ]] || { printf '%s\n' "missing $key" >&2; exit 1; }
done
host=$MEASURED_GRANTEE_HOST; db=${cfg[JAX_DB_NAME]}
[[ $db =~ ^[A-Za-z0-9_]{1,64}$ ]] || { printf '%s\n' 'unsafe database name' >&2; exit 1; }
if [[ "$PREPARE_OS" == 1 ]]; then
  validate_system_account() {
    local account=$1 account_uid account_gid account_home account_shell account_group
    getent passwd "$account" >/dev/null || useradd --system --user-group --home-dir /nonexistent --shell /usr/sbin/nologin "$account"
    IFS=: read -r _ _ account_uid account_gid _ account_home account_shell < <(getent passwd "$account")
    account_group=$(getent group "$account_gid" | cut -d: -f1)
    [[ $account_uid =~ ^[0-9]+$ && $account_uid -lt 1000 && $account_home == /nonexistent && $account_shell == /usr/sbin/nologin && $account_group == "$account" ]] || {
      printf '%s\n' 'worker OS account is not a dedicated system identity' >&2; exit 1;
    }
    [[ $(id -nG "$account") == "$account" ]] || { printf '%s\n' 'worker OS account has supplementary groups' >&2; exit 1; }
  }
  validate_system_account "$WORKER_OS_USER"
  validate_system_account jaxbuild
fi
# Never chown or chmod an existing shared /etc/jax: jaxsvc/jaxweb must retain
# traversal.  We create only absent state roots in the explicit fixture path.
if [[ ! -d "$STATE_ROOT" ]]; then install -d -m 0750 -o root -g root "$STATE_ROOT"; fi
[[ ! -L $STATE_ROOT && $(stat -c '%U:%G' "$STATE_ROOT") == root:root ]] || { printf '%s\n' 'worker state root is unsafe' >&2; exit 1; }
state_mode=$(stat -c '%a' "$STATE_ROOT"); (( (8#$state_mode & 0022) == 0 )) || { printf '%s\n' 'worker state root is writable' >&2; exit 1; }
SECRET_DIR="$STATE_ROOT/secrets"
if [[ ! -d "$SECRET_DIR" ]]; then install -d -m 0700 -o root -g root "$SECRET_DIR"; fi
[[ ! -L $SECRET_DIR && $(stat -c '%U:%G:%a' "$SECRET_DIR") == root:root:700 ]] || { printf '%s\n' 'worker secrets root is unsafe' >&2; exit 1; }
secret="$SECRET_DIR/$WORKER_DB_USER.password"
[[ -f "$secret" ]] || { openssl rand -base64 48 | tr -d '\n' > "$secret"; chown root:root "$secret"; chmod 600 "$secret"; }
[[ -f $secret && ! -L $secret && $(stat -c '%U:%G:%a' "$secret") == root:root:600 ]] || {
  printf '%s\n' 'worker DB secret is unsafe' >&2; exit 1;
}
password=$(<"$secret")
[[ -n "$password" ]] || { printf '%s\n' 'worker DB secret is empty' >&2; exit 1; }
admin_sql() {
  if [[ -n "$ADMIN_TRANSPORT" ]]; then "$ADMIN_TRANSPORT"; else docker exec -i mariadb-12-3-jax sh -ec 'MYSQL_PWD="$(cat /run/secrets/mariadb_root_password)" exec mariadb -uroot --batch --skip-column-names'; fi
}
quote() { local q=$1; q=${q//\'/\'\'}; printf "'%s'" "$q"; }
principal="$(quote "$WORKER_DB_USER")@$(quote "$host")"
exists=$(printf "SELECT COUNT(*) FROM mysql.user WHERE User=%s AND Host=%s;\n" "$(quote "$WORKER_DB_USER")" "$(quote "$host")" | admin_sql)
[[ "$exists" == 0 || "$exists" == 1 ]] || { printf '%s\n' 'ambiguous worker principal lookup' >&2; exit 1; }
if [[ "$exists" == 1 ]]; then
  stored=$(printf "SELECT JSON_UNQUOTE(JSON_EXTRACT(Priv, '$.authentication_string')) = PASSWORD(%s) FROM mysql.global_priv WHERE User=%s AND Host=%s;\n" "$(quote "$password")" "$(quote "$WORKER_DB_USER")" "$(quote "$host")" | admin_sql)
  [[ "$stored" == 1 ]] || { printf '%s\n' 'existing worker principal secret does not match' >&2; exit 1; }
else
  printf "CREATE USER %s IDENTIFIED BY %s;\n" "$principal" "$(quote "$password")" | admin_sql >/dev/null
fi
owner_principal="'jaxoffice_invitation_owner'@'localhost'"
printf "CREATE USER IF NOT EXISTS %s ACCOUNT LOCK;\n" "$owner_principal" | admin_sql >/dev/null
owner_grant_count=$(printf "SHOW GRANTS FOR %s;\n" "$owner_principal" | admin_sql | grep -c '^GRANT ' || true)
if (( owner_grant_count > 1 )); then printf "REVOKE ALL PRIVILEGES, GRANT OPTION FROM %s;\n" "$owner_principal" | admin_sql >/dev/null; fi
# The definer owns only the three authority transitions.  It has enough table
# privilege for the trigger-guarded statements inside them, never login.
printf "GRANT SELECT ON \`%s\`.\`office_invitation\` TO %s;\nGRANT SELECT ON \`%s\`.\`office_invitation_issuance\` TO %s;\nGRANT SELECT ON \`%s\`.\`office_invitation_event\` TO %s;\nGRANT SELECT ON \`%s\`.\`office_invitation_outbox\` TO %s;\nGRANT INSERT ON \`%s\`.\`office_invitation_event\` TO %s;\nGRANT UPDATE (status,version) ON \`%s\`.\`office_invitation\` TO %s;\nGRANT UPDATE (state,attempt_id,attempt_count,payload_ciphertext,message_id,result_code,claimed_at,finished_at,updated_at) ON \`%s\`.\`office_invitation_outbox\` TO %s;\n" "$db" "$owner_principal" "$db" "$owner_principal" "$db" "$owner_principal" "$db" "$owner_principal" "$db" "$owner_principal" "$db" "$owner_principal" "$db" "$owner_principal" | admin_sql >/dev/null
worker_grant_count=$(printf "SHOW GRANTS FOR %s;\n" "$principal" | admin_sql | grep -c '^GRANT ' || true)
if (( worker_grant_count > 1 )); then printf "REVOKE ALL PRIVILEGES, GRANT OPTION FROM %s;\n" "$principal" | admin_sql >/dev/null; fi
printf "GRANT SELECT ON \`%s\`.\`axioma_config\` TO %s;\nGRANT SELECT (outbox_id,invitation_id,state,attempt_id,claimed_at,created_at) ON \`%s\`.\`office_invitation_outbox\` TO %s;\n" "$db" "$principal" "$db" "$principal" | admin_sql >/dev/null
routine_count=$(printf "SELECT COUNT(*) FROM information_schema.ROUTINES WHERE ROUTINE_SCHEMA=%s AND ROUTINE_TYPE='PROCEDURE' AND ROUTINE_NAME IN ('office_invitation_claim_next_v1','office_invitation_expire_due_v1','office_invitation_settle_delivery_v1');\n" "$(quote "$db")" | admin_sql)
[[ "$routine_count" =~ ^[0-3]$ ]] || { printf '%s\n' 'ambiguous invitation delivery routine lookup' >&2; exit 1; }
if [[ "$routine_count" == 3 ]]; then
  # MariaDB checks EXECUTE before it runs a SQL SECURITY DEFINER body. The
  # locked definer therefore needs these exact three procedure grants too.
  for routine in office_invitation_claim_next_v1 office_invitation_expire_due_v1 office_invitation_settle_delivery_v1; do
    printf "GRANT EXECUTE ON PROCEDURE \`%s\`.\`%s\` TO %s;\n" "$db" "$routine" "$owner_principal" | admin_sql >/dev/null
    printf "GRANT EXECUTE ON PROCEDURE \`%s\`.\`%s\` TO %s;\n" "$db" "$routine" "$principal" | admin_sql >/dev/null
  done
fi
# A pre-migration run deliberately has no EXECUTE yet; a post-migration rerun
# is required and checked by readiness before the timer can be enabled.
client_tmp=$(mktemp "$SECRET_DIR/$WORKER_DB_USER.client.XXXXXX")
printf '[client]\nuser=%s\npassword=%s\nhost=%s\nport=%s\n' "$WORKER_DB_USER" "$password" "${cfg[JAX_DB_HOST]}" "${cfg[JAX_DB_PORT]}" > "$client_tmp"
chmod 600 "$client_tmp"
current=$(mariadb "--defaults-extra-file=$client_tmp" --batch --skip-column-names "$db" -e 'SELECT CURRENT_USER()') || { unlink "$client_tmp"; printf '%s\n' 'worker principal login failed' >&2; exit 1; }
unlink "$client_tmp"
[[ "$current" == "$WORKER_DB_USER@$host" ]] || { printf '%s\n' 'worker principal resolved to an unexpected host' >&2; exit 1; }
env_tmp=$(mktemp "$STATE_ROOT/invitation-worker.env.XXXXXX")
for key in JAX_DB_HOST JAX_DB_PORT JAX_DB_NAME FERNET_KEY JAX_ENVIRONMENT OFFICE_PUBLIC_ORIGIN OFFICE_INVITATION_LIFETIME_SECONDS OFFICE_INVITATION_CLAIM_TIMEOUT_SECONDS OFFICE_INVITATION_STALE_CLAIM_BATCH_LIMIT OFFICE_INVITATION_EXPIRY_SWEEP_BATCH_LIMIT OFFICE_INVITATION_DELIVERY_BATCH_LIMIT OFFICE_INVITATION_PUBLIC_BASE_URL; do printf '%s=%s\n' "$key" "${cfg[$key]}" >> "$env_tmp"; done
printf 'JAX_DB_USER=%s\nJAX_DB_PASSWORD=%s\n' "$WORKER_DB_USER" "$password" >> "$env_tmp"
chown root:root "$env_tmp"; chmod 600 "$env_tmp"; mv -f "$env_tmp" "$STATE_ROOT/invitation-worker.env"
printf '%s\n' 'invitation worker principal and env provisioned; install audited units, daemon-reload, then enable timer separately'
if [[ "$routine_count" == 3 ]]; then
# Full grant sets are a contract.  Count alone is insufficient because a
# different sixth privilege can change authority.
worker_grants=$(printf "SHOW GRANTS FOR %s;\n" "$principal" | admin_sql | sed -E "s/ IDENTIFIED BY (PASSWORD )?'[^']*'//g" | tr '\140' '\047')
expected_worker=$(cat <<EOF2
GRANT USAGE ON *.* TO '$WORKER_DB_USER'@'$host'
GRANT SELECT ('attempt_id', 'outbox_id', 'invitation_id', 'state', 'created_at', 'claimed_at') ON '$db'.'office_invitation_outbox' TO '$WORKER_DB_USER'@'$host'
GRANT SELECT ON '$db'.'axioma_config' TO '$WORKER_DB_USER'@'$host'
GRANT EXECUTE ON PROCEDURE '$db'.'office_invitation_claim_next_v1' TO '$WORKER_DB_USER'@'$host'
GRANT EXECUTE ON PROCEDURE '$db'.'office_invitation_expire_due_v1' TO '$WORKER_DB_USER'@'$host'
GRANT EXECUTE ON PROCEDURE '$db'.'office_invitation_settle_delivery_v1' TO '$WORKER_DB_USER'@'$host'
EOF2
)
[[ $(printf '%s\n' "$worker_grants" | sort) == $(printf '%s\n' "$expected_worker" | sort) ]] || { printf '%s\n' 'worker database grants are not exact' >&2; printf '%s\n' "$worker_grants" >&2; exit 1; }
owner_grants=$(printf "SHOW GRANTS FOR %s;\n" "$owner_principal" | admin_sql | tr '\140' '\047')
expected_owner=$(cat <<EOF2
GRANT USAGE ON *.* TO 'jaxoffice_invitation_owner'@'localhost'
GRANT SELECT, UPDATE ('result_code', 'state', 'finished_at', 'attempt_id', 'updated_at', 'attempt_count', 'payload_ciphertext', 'message_id', 'claimed_at') ON '$db'.'office_invitation_outbox' TO 'jaxoffice_invitation_owner'@'localhost'
GRANT SELECT ON '$db'.'office_invitation_issuance' TO 'jaxoffice_invitation_owner'@'localhost'
GRANT SELECT, UPDATE ('version', 'status') ON '$db'.'office_invitation' TO 'jaxoffice_invitation_owner'@'localhost'
GRANT SELECT, INSERT ON '$db'.'office_invitation_event' TO 'jaxoffice_invitation_owner'@'localhost'
GRANT EXECUTE ON PROCEDURE '$db'.'office_invitation_claim_next_v1' TO 'jaxoffice_invitation_owner'@'localhost'
GRANT EXECUTE ON PROCEDURE '$db'.'office_invitation_expire_due_v1' TO 'jaxoffice_invitation_owner'@'localhost'
GRANT EXECUTE ON PROCEDURE '$db'.'office_invitation_settle_delivery_v1' TO 'jaxoffice_invitation_owner'@'localhost'
EOF2
)
[[ $(printf '%s\n' "$owner_grants" | sort) == $(printf '%s\n' "$expected_owner" | sort) ]] || { printf '%s\n' 'delivery definer database grants are not exact' >&2; printf '%s\n' "$owner_grants" >&2; exit 1; }
fi
