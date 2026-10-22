#!/usr/bin/env bash
# Provision the least-privileged OS and database principal for invitation mail.
# Run only after the Office migration, using the established root-only MariaDB
# admin transport.  It does not issue invitations and never starts the timer.
set -euo pipefail
umask 077

ENV_FILE=/etc/jax/runtime.env
STATE_ROOT=/etc/jax
ADMIN_TRANSPORT=
PREPARE_OS=1
WORKER_DB_USER=jaxinvite
WORKER_OS_USER=jaxinvite
while [[ $# -gt 0 ]]; do
  case "$1" in
    --env) ENV_FILE=${2:?}; shift ;;
    --state-root) STATE_ROOT=${2:?}; shift ;;
    --admin-transport) ADMIN_TRANSPORT=${2:?}; shift ;;
    --skip-os-identities) PREPARE_OS=0 ;;
    --worker-db-user) WORKER_DB_USER=${2:?}; shift ;;
    --worker-os-user) WORKER_OS_USER=${2:?}; shift ;;
    -h|--help) printf '%s\n' 'usage: provision-invitation-worker.sh [--env FILE] [--state-root DIR] [--admin-transport FILE] [--skip-os-identities] [--worker-db-user USER] [--worker-os-user USER]'; exit 0 ;;
    *) printf '%s\n' "unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done
[[ $(id -u) == 0 ]] || { printf '%s\n' 'must run as root' >&2; exit 1; }
[[ "$WORKER_DB_USER" =~ ^[a-z][a-z0-9_]{0,31}$ && "$WORKER_OS_USER" =~ ^[a-z][a-z0-9_-]{0,31}$ ]] || { printf '%s\n' 'unsafe worker user name' >&2; exit 1; }
[[ -r "$ENV_FILE" ]] || { printf '%s\n' 'runtime environment is unreadable' >&2; exit 1; }
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
for key in JAX_DB_HOST JAX_DB_PORT JAX_DB_NAME JAX_DB_GRANTEE_HOST FERNET_KEY OFFICE_INVITATION_LIFETIME_SECONDS OFFICE_INVITATION_CLAIM_TIMEOUT_SECONDS OFFICE_INVITATION_STALE_CLAIM_BATCH_LIMIT OFFICE_INVITATION_EXPIRY_SWEEP_BATCH_LIMIT OFFICE_INVITATION_DELIVERY_BATCH_LIMIT OFFICE_INVITATION_PUBLIC_BASE_URL; do
  [[ -n ${cfg[$key]:-} ]] || { printf '%s\n' "missing $key" >&2; exit 1; }
done
host=${cfg[JAX_DB_GRANTEE_HOST]}; db=${cfg[JAX_DB_NAME]}
[[ "$host" != *%* && "$host" != *_* ]] || { printf '%s\n' 'wildcard DB host is forbidden' >&2; exit 1; }
if [[ "$PREPARE_OS" == 1 ]]; then getent passwd "$WORKER_OS_USER" >/dev/null || useradd --system --user-group --home-dir /nonexistent --shell /usr/sbin/nologin "$WORKER_OS_USER"; fi
# Never chown or chmod an existing shared /etc/jax: jaxsvc/jaxweb must retain
# traversal.  We create only absent state roots in the explicit fixture path.
if [[ ! -d "$STATE_ROOT" ]]; then install -d -m 0750 -o root -g root "$STATE_ROOT"; fi
SECRET_DIR="$STATE_ROOT/secrets"
if [[ ! -d "$SECRET_DIR" ]]; then install -d -m 0700 -o root -g root "$SECRET_DIR"; fi
secret="$SECRET_DIR/$WORKER_DB_USER.password"
[[ -f "$secret" ]] || { openssl rand -base64 48 | tr -d '\n' > "$secret"; chown root:root "$secret"; chmod 600 "$secret"; }
[[ $(stat -c '%U:%G:%a' "$secret") == root:root:600 ]] || { printf '%s\n' 'worker DB secret is unsafe' >&2; exit 1; }
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
printf "REVOKE ALL PRIVILEGES, GRANT OPTION FROM %s;\n" "$principal" | admin_sql >/dev/null || true
for table in axioma_config office_invitation office_invitation_issuance office_invitation_outbox office_invitation_event; do
  printf "GRANT SELECT ON \`%s\`.\`%s\` TO %s;\n" "$db" "$table" "$principal" | admin_sql >/dev/null
done
for table in office_invitation office_invitation_outbox; do
  printf "GRANT UPDATE ON \`%s\`.\`%s\` TO %s;\n" "$db" "$table" "$principal" | admin_sql >/dev/null
done
printf "GRANT INSERT ON \`%s\`.\`office_invitation_event\` TO %s;\n" "$db" "$principal" | admin_sql >/dev/null
grants=$(printf "SHOW GRANTS FOR %s;\n" "$principal" | admin_sql | sed -E "s/ IDENTIFIED BY (PASSWORD )?'[^']*'//g" | tr '\140' '\047')
expected_grants=(
  "GRANT USAGE ON *.* TO '$WORKER_DB_USER'@'$host'"
  "GRANT SELECT ON '$db'.'axioma_config' TO '$WORKER_DB_USER'@'$host'"
  "GRANT SELECT, UPDATE ON '$db'.'office_invitation' TO '$WORKER_DB_USER'@'$host'"
  "GRANT SELECT ON '$db'.'office_invitation_issuance' TO '$WORKER_DB_USER'@'$host'"
  "GRANT SELECT, INSERT ON '$db'.'office_invitation_event' TO '$WORKER_DB_USER'@'$host'"
  "GRANT SELECT, UPDATE ON '$db'.'office_invitation_outbox' TO '$WORKER_DB_USER'@'$host'"
)
for grant in "${expected_grants[@]}"; do printf '%s\n' "$grants" | grep -Fqx "$grant" || { printf '%s\n' 'worker database grants are not exact' >&2; exit 1; }; done
[[ $(printf '%s\n' "$grants" | grep -c '^GRANT ') == ${#expected_grants[@]} ]] || {
  printf '%s\n' 'worker database principal has an extra grant, role, routine, or column privilege' >&2; exit 1;
}
client_tmp=$(mktemp "$SECRET_DIR/$WORKER_DB_USER.client.XXXXXX")
printf '[client]\nuser=%s\npassword=%s\nhost=%s\nport=%s\n' "$WORKER_DB_USER" "$password" "${cfg[JAX_DB_HOST]}" "${cfg[JAX_DB_PORT]}" > "$client_tmp"
chmod 600 "$client_tmp"
current=$(mariadb "--defaults-extra-file=$client_tmp" --batch --skip-column-names "$db" -e 'SELECT CURRENT_USER()') || { unlink "$client_tmp"; printf '%s\n' 'worker principal login failed' >&2; exit 1; }
unlink "$client_tmp"
[[ "$current" == "$WORKER_DB_USER@$host" ]] || { printf '%s\n' 'worker principal resolved to an unexpected host' >&2; exit 1; }
env_tmp=$(mktemp "$STATE_ROOT/invitation-worker.env.XXXXXX")
for key in JAX_DB_HOST JAX_DB_PORT JAX_DB_NAME FERNET_KEY OFFICE_INVITATION_LIFETIME_SECONDS OFFICE_INVITATION_CLAIM_TIMEOUT_SECONDS OFFICE_INVITATION_STALE_CLAIM_BATCH_LIMIT OFFICE_INVITATION_EXPIRY_SWEEP_BATCH_LIMIT OFFICE_INVITATION_DELIVERY_BATCH_LIMIT OFFICE_INVITATION_PUBLIC_BASE_URL; do printf '%s=%s\n' "$key" "${cfg[$key]}" >> "$env_tmp"; done
printf 'JAX_DB_USER=%s\nJAX_DB_PASSWORD=%s\n' "$WORKER_DB_USER" "$password" >> "$env_tmp"
chown root:root "$env_tmp"; chmod 600 "$env_tmp"; mv -f "$env_tmp" "$STATE_ROOT/invitation-worker.env"
printf '%s\n' 'invitation worker principal and env provisioned; install audited units, daemon-reload, then enable timer separately'
