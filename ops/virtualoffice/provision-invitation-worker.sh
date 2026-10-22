#!/usr/bin/env bash
# Provision the least-privileged OS and database principal for invitation mail.
# Run only after the Office migration, using the established root-only MariaDB
# admin transport.  It does not issue invitations and never starts the timer.
set -euo pipefail
umask 077

ENV_FILE=${1:-/etc/jax/runtime.env}
[[ $(id -u) == 0 ]] || { printf '%s\n' 'must run as root' >&2; exit 1; }
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
getent passwd jaxinvite >/dev/null || useradd --system --user-group --home-dir /nonexistent --shell /usr/sbin/nologin jaxinvite
install -d -m 0750 -o root -g jaxinvite /etc/jax
secret=/etc/jax/secrets/jaxinvite.password
install -d -m 0700 -o root -g root /etc/jax/secrets
[[ -f "$secret" ]] || { openssl rand -base64 48 | tr -d '\n' > "$secret"; chown root:root "$secret"; chmod 600 "$secret"; }
[[ $(stat -c '%U:%G:%a' "$secret") == root:root:600 ]] || { printf '%s\n' 'worker DB secret is unsafe' >&2; exit 1; }
password=$(<"$secret")
[[ -n "$password" ]] || { printf '%s\n' 'worker DB secret is empty' >&2; exit 1; }
admin_sql() { docker exec -i mariadb-12-3-jax sh -ec 'MYSQL_PWD="$(cat /run/secrets/mariadb_root_password)" exec mariadb -uroot --batch --skip-column-names' ; }
quote() { local q=$1; q=${q//\'/\'\'}; printf "'%s'" "$q"; }
principal="$(quote jaxinvite)@$(quote "$host")"
printf "CREATE USER IF NOT EXISTS %s IDENTIFIED BY %s;\n" "$principal" "$(quote "$password")" | admin_sql >/dev/null
printf "REVOKE ALL PRIVILEGES, GRANT OPTION FROM %s;\n" "$principal" | admin_sql >/dev/null || true
for table in axioma_config office_invitation office_invitation_issuance office_invitation_outbox office_invitation_event; do
  printf "GRANT SELECT ON \`%s\`.\`%s\` TO %s;\n" "$db" "$table" "$principal" | admin_sql >/dev/null
done
for table in office_invitation office_invitation_outbox; do
  printf "GRANT UPDATE ON \`%s\`.\`%s\` TO %s;\n" "$db" "$table" "$principal" | admin_sql >/dev/null
done
printf "GRANT INSERT ON \`%s\`.\`office_invitation_event\` TO %s;\n" "$db" "$principal" | admin_sql >/dev/null
grants=$(printf "SHOW GRANTS FOR %s;\n" "$principal" | admin_sql | tr '\140' '\047')
expected_grants=(
  "GRANT USAGE ON *.* TO 'jaxinvite'@'$host'"
  "GRANT SELECT ON '$db'.'axioma_config' TO 'jaxinvite'@'$host'"
  "GRANT SELECT, UPDATE ON '$db'.'office_invitation' TO 'jaxinvite'@'$host'"
  "GRANT SELECT ON '$db'.'office_invitation_issuance' TO 'jaxinvite'@'$host'"
  "GRANT SELECT, INSERT ON '$db'.'office_invitation_event' TO 'jaxinvite'@'$host'"
  "GRANT SELECT, UPDATE ON '$db'.'office_invitation_outbox' TO 'jaxinvite'@'$host'"
)
for grant in "${expected_grants[@]}"; do printf '%s\n' "$grants" | grep -Fqx "$grant" || { printf '%s\n' 'worker database grants are not exact' >&2; exit 1; }; done
[[ $(printf '%s\n' "$grants" | grep -c '^GRANT ') == ${#expected_grants[@]} ]] || {
  printf '%s\n' 'worker database principal has an extra grant, role, routine, or column privilege' >&2; exit 1;
}
env_tmp=$(mktemp /etc/jax/invitation-worker.env.XXXXXX)
for key in JAX_DB_HOST JAX_DB_PORT JAX_DB_NAME FERNET_KEY OFFICE_INVITATION_LIFETIME_SECONDS OFFICE_INVITATION_CLAIM_TIMEOUT_SECONDS OFFICE_INVITATION_STALE_CLAIM_BATCH_LIMIT OFFICE_INVITATION_EXPIRY_SWEEP_BATCH_LIMIT OFFICE_INVITATION_DELIVERY_BATCH_LIMIT OFFICE_INVITATION_PUBLIC_BASE_URL; do printf '%s=%s\n' "$key" "${cfg[$key]}" >> "$env_tmp"; done
printf 'JAX_DB_USER=jaxinvite\nJAX_DB_PASSWORD=%s\n' "$password" >> "$env_tmp"
chown root:root "$env_tmp"; chmod 600 "$env_tmp"; mv -f "$env_tmp" /etc/jax/invitation-worker.env
printf '%s\n' 'invitation worker principal and env provisioned; install audited units, daemon-reload, then enable timer separately'
