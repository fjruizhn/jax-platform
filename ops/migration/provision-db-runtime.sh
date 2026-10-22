#!/usr/bin/env bash
# Provision the two untrusted JAX Platform database principals.
#
# This deliberately does not read an env file as shell code.  It is invoked by
# restore-hall9000.sh after the encrypted legacy env has been installed.
set -euo pipefail
umask 077

MODE=apply
ENV_FILE=/etc/jax/.env
RUNTIME_ENV=/etc/jax/runtime.env
MIGRATE_ENV=/etc/jax/migrate.env
SECRET_DIR=/etc/jax/secrets
MEASURED_HOST=
PREPARE_OS=1
ADMIN_CONTAINER="${JAX_DB_ADMIN_CONTAINER:-mariadb-12-3-jax}"
ADMIN_SECRET_FILE="${JAX_DB_ADMIN_SECRET_FILE:-/run/secrets/mariadb_root_password}"
ADMIN_TRANSPORT=
MANIFEST_FILE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/runtime-table-privileges-v1.txt"

usage() {
  printf '%s\n' 'usage: provision-db-runtime.sh [--plan] [--state-root DIR] [--admin-transport PATH] [--env FILE] --measured-grantee-host HOST [--skip-os-identities]'
}
while [ "$#" -gt 0 ]; do
  case "$1" in
    --plan) MODE=plan ;;
    --env) ENV_FILE=${2:?}; shift ;;
    --runtime-env) RUNTIME_ENV=${2:?}; shift ;;
    --migrate-env) MIGRATE_ENV=${2:?}; shift ;;
    --secret-dir) SECRET_DIR=${2:?}; shift ;;
    --state-root) STATE_ROOT=${2:?}; RUNTIME_ENV="$STATE_ROOT/runtime.env"; MIGRATE_ENV="$STATE_ROOT/migrate.env"; SECRET_DIR="$STATE_ROOT/secrets"; shift ;;
    --admin-transport) ADMIN_TRANSPORT=${2:?}; shift ;;
    --measured-grantee-host) MEASURED_HOST=${2:?}; shift ;;
    --skip-os-identities) PREPARE_OS=0 ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'unknown argument: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

die() { printf 'provision-db-runtime: %s\n' "$*" >&2; exit 1; }
note() { printf 'provision-db-runtime: %s\n' "$*"; }
[[ -r "$ENV_FILE" ]] || die "environment file is not readable"

declare -A CFG=()
parse_env() {
  local line key value line_no=0
  while IFS= read -r line || [ -n "$line" ]; do
    line_no=$((line_no + 1))
    [[ -z "$line" || "$line" == \#* ]] && continue
    [[ "$line" != *$'\r'* ]] || die "CR is not allowed in env line $line_no"
    [[ "$line" == *=* ]] || die "malformed env line $line_no"
    key=${line%%=*}; value=${line#*=}
    [[ "$key" =~ ^[A-Z][A-Z0-9_]*$ ]] || die "malformed env key on line $line_no"
    [[ ! -v "CFG[$key]" ]] || die "duplicate env key: $key"
    # systemd EnvironmentFile has no shell evaluation.  Reject the forms that
    # would change its parser rather than trying to quote legacy shell syntax.
    [[ "$value" != *[\"\'\\\$\#[:space:]]* ]] || die "unsafe EnvironmentFile value for $key"
    CFG["$key"]=$value
  done < "$ENV_FILE"
}
parse_env

need() { [[ -n ${CFG[$1]+yes} && -n ${CFG[$1]} ]] || die "missing required key: $1"; }
for key in JAX_DB_HOST JAX_DB_PORT JAX_DB_NAME JAX_DB_GRANTEE_HOST JAX_RUNTIME_DB_USER JAX_MIGRATE_DB_USER; do need "$key"; done
[[ "$MEASURED_HOST" ]] || die "an explicit measured grantee host is required"
[[ ${CFG[JAX_DB_GRANTEE_HOST]} != *'%'* ]] || die "wildcard database grantee hosts are forbidden"
[[ ${CFG[JAX_DB_GRANTEE_HOST]} != *'_'* ]] || die "wildcard database grantee hosts are forbidden"
[[ ${CFG[JAX_DB_GRANTEE_HOST]} == "$MEASURED_HOST" ]] || die "configured grantee host does not match the deployment measurement"
[[ ${CFG[JAX_DB_GRANTEE_HOST]} =~ ^[A-Za-z0-9.:-]+$ ]] || die "invalid database grantee host"
[[ ${CFG[JAX_DB_NAME]} =~ ^[A-Za-z0-9_]{1,64}$ ]] || die "invalid database name"
[[ ${CFG[JAX_DB_PORT]} =~ ^[0-9]{1,5}$ ]] || die "invalid database port"

RUNTIME_USER=${CFG[JAX_RUNTIME_DB_USER]}
MIGRATE_USER=${CFG[JAX_MIGRATE_DB_USER]}
HOST=${CFG[JAX_DB_GRANTEE_HOST]}
DB=${CFG[JAX_DB_NAME]}
[[ "$RUNTIME_USER" != "$MIGRATE_USER" ]] || die "database principal names must differ"
[[ "$RUNTIME_USER" =~ ^[A-Za-z0-9_]{1,32}$ && "$MIGRATE_USER" =~ ^[A-Za-z0-9_]{1,32}$ ]] || die "invalid database principal name"
[[ ${CFG[JAX_DB_USER]:-} != "$RUNTIME_USER" && ${CFG[JAX_DB_USER]:-} != "$MIGRATE_USER" ]] || die "legacy principal must remain distinct"

declare -a RUNTIME_TABLES=()
declare -A RUNTIME_TABLE_PRIVILEGES=()
load_runtime_manifest() {
  [[ -r "$MANIFEST_FILE" ]] || die "runtime table privilege manifest is not readable"
  local line table privileges extra previous=
  while IFS= read -r line || [ -n "$line" ]; do
    [[ -z "$line" || "$line" == \#* ]] && continue
    read -r table privileges extra <<< "$line"
    [[ -n "$table" && -n "$privileges" && -z ${extra:-} ]] || die "runtime table privilege manifest is malformed"
    [[ "$table" =~ ^[a-z][a-z0-9_]{0,63}$ ]] || die "runtime table privilege manifest has invalid table"
    [[ "$privileges" =~ ^(UPDATE|DELETE)(,(UPDATE|DELETE))?$ ]] || die "runtime table privilege manifest has invalid privileges"
    [[ "$table" != office_authorization_event && "$table" != office_mfa_security_event && "$table" != office_bootstrap_state && "$table" != office_schema_revision ]] || die "runtime table privilege manifest grants a protected table"
    [[ -z "$previous" || "$table" > "$previous" ]] || die "runtime table privilege manifest is not ordered"
    previous=$table
    RUNTIME_TABLES+=("$table")
    RUNTIME_TABLE_PRIVILEGES["$table"]=$privileges
  done < "$MANIFEST_FILE"
  [ "${#RUNTIME_TABLES[@]}" -gt 0 ] || die "runtime table privilege manifest is empty"
}
load_runtime_manifest

if [ "$MODE" = plan ]; then
  note "plan: preserve legacy principal; provision $RUNTIME_USER@$HOST (SELECT/INSERT schema plus table UPDATE/DELETE) and $MIGRATE_USER@$HOST (migration DDL)"
  note "plan: write root-only runtime.env and migrate.env; web=jaxweb migrator=jaxmigrate artifact=/srv/jax-prod"
  exit 0
fi

[ "$(id -u)" = 0 ] || die "apply must run as root"
if [ -z "$ADMIN_TRANSPORT" ]; then
  command -v docker >/dev/null || die "docker is required for the canonical MariaDB admin transport"
  docker inspect "$ADMIN_CONTAINER" >/dev/null 2>&1 || die "MariaDB admin container is unavailable"
else
  [[ "$ADMIN_TRANSPORT" = /* && -x "$ADMIN_TRANSPORT" ]] || die "test admin transport must be an executable absolute path"
fi

admin_sql() {
  # The root password is read from the secret mounted inside the DB container.
  # It never enters a host argv, output, repository, or runtime env file.
  if [ -n "$ADMIN_TRANSPORT" ]; then "$ADMIN_TRANSPORT"; else
    docker exec -i "$ADMIN_CONTAINER" sh -ec \
      'MYSQL_PWD="$(cat "$1")" exec mariadb -uroot --batch --skip-column-names' sh "$ADMIN_SECRET_FILE"
  fi
}
sql_quote() { local q=$1; q=${q//\'/\'\'}; printf "'%s'" "$q"; }
principal_exists() {
  local user=$1 result
  result=$(printf "SELECT COUNT(*) FROM mysql.user WHERE User=%s AND Host=%s;\n" "$(sql_quote "$user")" "$(sql_quote "$HOST")" | admin_sql) || die "could not inspect database principal"
  [[ "$result" == 0 || "$result" == 1 ]] || die "ambiguous database principal inspection"
  printf '%s' "$result"
}
password_file_for() { printf '%s/%s.password' "$SECRET_DIR" "$1"; }
read_secret() { local path=$1; stat -c '%U:%G:%a' "$path" | grep -qx 'root:root:600' || die "secret file permissions are unsafe"; IFS= read -r REPLY < "$path"; [ -n "$REPLY" ] || die "secret file is empty"; printf '%s' "$REPLY"; }
write_secret() { local path=$1 value=$2 tmp; tmp=$(mktemp "${path}.tmp.XXXXXX"); printf '%s\n' "$value" > "$tmp"; chown root:root "$tmp"; chmod 600 "$tmp"; mv -f "$tmp" "$path"; }
new_secret() { openssl rand -base64 48 | tr -d '\n'; }

if [ ! -d "$SECRET_DIR" ]; then mkdir -p "$SECRET_DIR"; chown root:root "$SECRET_DIR"; chmod 700 "$SECRET_DIR"
else stat -c '%U:%G:%a' "$SECRET_DIR" | grep -qx 'root:root:700' || die "dedicated secret directory permissions are unsafe"
fi
runtime_secret_file=$(password_file_for "$RUNTIME_USER")
migrate_secret_file=$(password_file_for "$MIGRATE_USER")
runtime_exists=$(principal_exists "$RUNTIME_USER")
migrate_exists=$(principal_exists "$MIGRATE_USER")

if { [ "$runtime_exists" = 1 ] && [ ! -f "$runtime_secret_file" ]; } || { [ "$migrate_exists" = 1 ] && [ ! -f "$migrate_secret_file" ]; }; then
  die "an existing database principal lacks its root-only secret file; refusing to alter it"
fi
if { [ "$runtime_exists" = 0 ] && [ -f "$runtime_secret_file" ]; } || { [ "$migrate_exists" = 0 ] && [ -f "$migrate_secret_file" ]; }; then
  note "converging a missing database principal from its existing root-only secret file"
fi

if [ -f "$runtime_secret_file" ]; then runtime_password=$(read_secret "$runtime_secret_file"); else runtime_password=$(new_secret); fi
if [ -f "$migrate_secret_file" ]; then migrate_password=$(read_secret "$migrate_secret_file"); else migrate_password=$(new_secret); fi
[ -f "$runtime_secret_file" ] || write_secret "$runtime_secret_file" "$runtime_password"
[ -f "$migrate_secret_file" ] || write_secret "$migrate_secret_file" "$migrate_password"

verify_password() {
  local user=$1 pass=$2 stored
  stored=$(printf "SELECT JSON_UNQUOTE(JSON_EXTRACT(Priv, '$.authentication_string')) = PASSWORD(%s) FROM mysql.global_priv WHERE User=%s AND Host=%s;\n" "$(sql_quote "$pass")" "$(sql_quote "$user")" "$(sql_quote "$HOST")" | admin_sql) || die "could not verify principal secret"
  [[ "$stored" == 1 ]] || die "existing database principal secret does not match; refusing to alter it"
}
[ "$runtime_exists" = 0 ] || verify_password "$RUNTIME_USER" "$runtime_password"
[ "$migrate_exists" = 0 ] || verify_password "$MIGRATE_USER" "$migrate_password"

create_missing() {
  local user=$1 pass=$2 exists=$3
  [ "$exists" = 0 ] || return 0
  printf 'CREATE USER %s@%s IDENTIFIED BY %s;\n' "$(sql_quote "$user")" "$(sql_quote "$HOST")" "$(sql_quote "$pass")" | admin_sql >/dev/null || die "could not create database principal"
}
normalize_set() { printf '%s\n' "$1" | tr ',' '\n' | sed 's/^ *//;s/ *$//' | sort -u | paste -sd, -; }
show_grants() { printf 'SHOW GRANTS FOR %s@%s;\n' "$(sql_quote "$1")" "$(sql_quote "$HOST")" | admin_sql | sed -E "s/ IDENTIFIED BY (PASSWORD )?'[^']*'//g" | tr '\140' '\047'; }

verify_migrator_grants() {
  local grants schema_line actual_set expected_set
  grants=$(show_grants "$MIGRATE_USER") || die "could not inspect migration grants"
  printf '%s\n' "$grants" | grep -Fx "GRANT USAGE ON *.* TO '$MIGRATE_USER'@'$HOST'" >/dev/null || die "migration principal lacks exact global USAGE"
  schema_line=$(printf '%s\n' "$grants" | grep -E "^GRANT .* ON '$DB'\\.\\* TO '$MIGRATE_USER'@'$HOST'$") || die "migration principal lacks exact schema grants"
  [ "$(printf '%s\n' "$schema_line" | wc -l)" -eq 1 ] || die "migration principal has ambiguous schema grants"
  actual_set=${schema_line#GRANT }; actual_set=${actual_set%% ON *}; actual_set=$(normalize_set "$actual_set")
  expected_set=$(normalize_set 'SELECT, INSERT, UPDATE, DELETE, CREATE, DROP, ALTER, INDEX, TRIGGER, REFERENCES')
  [ "$actual_set" = "$expected_set" ] || die "migration principal schema privilege set is not exact"
  [ "$(printf '%s\n' "$grants" | grep -c '^GRANT ')" -eq 2 ] || die "migration principal has extra grants, roles, routines, columns, or grant option"
}

runtime_grant_state() {
  local grants schema_line actual_set legacy_set line table privileges expected_count actual_count prefix_count index
  grants=$(show_grants "$RUNTIME_USER") || die "could not inspect runtime grants"
  printf '%s\n' "$grants" | grep -Fx "GRANT USAGE ON *.* TO '$RUNTIME_USER'@'$HOST'" >/dev/null || { printf invalid; return; }
  schema_line=$(printf '%s\n' "$grants" | grep -E "^GRANT .* ON '$DB'\\.\\* TO '$RUNTIME_USER'@'$HOST'$"); [ "$(printf '%s\n' "$schema_line" | wc -l)" -eq 1 ] || { printf invalid; return; }
  actual_set=${schema_line#GRANT }; actual_set=${actual_set%% ON *}; actual_set=$(normalize_set "$actual_set")
  legacy_set=$(normalize_set 'SELECT, INSERT, UPDATE, DELETE')
  expected_count=$((2 + ${#RUNTIME_TABLES[@]})); actual_count=$(printf '%s\n' "$grants" | grep -c '^GRANT ')
  # A prior invocation can be interrupted between table GRANT statements and
  # the final schema REVOKE.  Recognize only an exact ordered prefix of this
  # manifest while the old broad DML schema grant is still present.  This is
  # the sole resumable transitional state; it never repairs arbitrary grants
  # and never broadens privileges again.
  if [ "$actual_set" = "$legacy_set" ]; then
    prefix_count=$((actual_count - 2))
    [ "$prefix_count" -ge 0 ] && [ "$prefix_count" -le "${#RUNTIME_TABLES[@]}" ] || { printf invalid; return; }
    for ((index=0; index<prefix_count; index++)); do
      table=${RUNTIME_TABLES[$index]}; privileges=${RUNTIME_TABLE_PRIVILEGES[$table]}
      line=$(printf '%s\n' "$grants" | grep -E "^GRANT .* ON '$DB'\\.'$table' TO '$RUNTIME_USER'@'$HOST'$" || true)
      [ "$(printf '%s\n' "$line" | wc -l)" -eq 1 ] || { printf invalid; return; }
      actual_set=${line#GRANT }; actual_set=${actual_set%% ON *}
      [ "$(normalize_set "$actual_set")" = "$(normalize_set "$privileges")" ] || { printf invalid; return; }
    done
    printf 'legacy-prefix:%s' "$prefix_count"; return
  fi
  [ "$actual_set" = "$(normalize_set 'SELECT, INSERT')" ] && [ "$actual_count" -eq "$expected_count" ] || { printf invalid; return; }
  for table in "${RUNTIME_TABLES[@]}"; do
    privileges=${RUNTIME_TABLE_PRIVILEGES[$table]}
    line=$(printf '%s\n' "$grants" | grep -E "^GRANT .* ON '$DB'\\.'$table' TO '$RUNTIME_USER'@'$HOST'$" || true)
    [ "$(printf '%s\n' "$line" | wc -l)" -eq 1 ] || { printf invalid; return; }
    actual_set=${line#GRANT }; actual_set=${actual_set%% ON *}
    [ "$(normalize_set "$actual_set")" = "$(normalize_set "$privileges")" ] || { printf invalid; return; }
  done
  printf new
}

create_missing "$RUNTIME_USER" "$runtime_password" "$runtime_exists"
create_missing "$MIGRATE_USER" "$migrate_password" "$migrate_exists"
if [ "$runtime_exists" = 0 ]; then
  printf 'GRANT SELECT, INSERT ON `%s`.* TO %s@%s;\n' "$DB" "$(sql_quote "$RUNTIME_USER")" "$(sql_quote "$HOST")" | admin_sql >/dev/null || die "could not grant runtime schema privileges"
  for table in "${RUNTIME_TABLES[@]}"; do
    printf 'GRANT %s ON `%s`.`%s` TO %s@%s;\n' "${RUNTIME_TABLE_PRIVILEGES[$table]}" "$DB" "$table" "$(sql_quote "$RUNTIME_USER")" "$(sql_quote "$HOST")" | admin_sql >/dev/null || die "could not grant runtime table privileges"
  done
else
  runtime_state=$(runtime_grant_state)
  case "$runtime_state" in
    new) ;;
    legacy-prefix:*)
      # Add the narrow table grants before revoking broad schema DML.
      prefix_count=${runtime_state#legacy-prefix:}
      [[ "$prefix_count" =~ ^[0-9]+$ ]] || die "runtime principal transitional state is malformed"
      for ((index=prefix_count; index<${#RUNTIME_TABLES[@]}; index++)); do
        table=${RUNTIME_TABLES[$index]}
        printf 'GRANT %s ON `%s`.`%s` TO %s@%s;\n' "${RUNTIME_TABLE_PRIVILEGES[$table]}" "$DB" "$table" "$(sql_quote "$RUNTIME_USER")" "$(sql_quote "$HOST")" | admin_sql >/dev/null || die "could not converge runtime table privileges"
      done
      printf 'REVOKE UPDATE, DELETE ON `%s`.* FROM %s@%s;\n' "$DB" "$(sql_quote "$RUNTIME_USER")" "$(sql_quote "$HOST")" | admin_sql >/dev/null || die "could not revoke legacy runtime schema privileges"
      ;;
    *) die "runtime principal is neither the exact legacy nor new grant state" ;;
  esac
fi
if [ "$migrate_exists" = 0 ]; then
  printf 'GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, DROP, ALTER, INDEX, TRIGGER, REFERENCES ON `%s`.* TO %s@%s;\n' "$DB" "$(sql_quote "$MIGRATE_USER")" "$(sql_quote "$HOST")" | admin_sql >/dev/null || die "could not grant migration principal"
fi
verify_migrator_grants
[ "$(runtime_grant_state)" = new ] || die "runtime principal table privilege set is not exact"

verify_login() { local user=$1 pass=$2 temp current; temp=$(mktemp "${SECRET_DIR}/login.XXXXXX"); printf '[client]\nuser=%s\npassword=%s\nhost=%s\nport=%s\n' "$user" "$pass" "${CFG[JAX_DB_HOST]}" "${CFG[JAX_DB_PORT]}" > "$temp"; chmod 600 "$temp"; current=$(mariadb "--defaults-extra-file=$temp" --batch --skip-column-names "$DB" -e 'SELECT CURRENT_USER()') || die "principal login failed"; rm -f "$temp"; [[ "$current" == "$user@$HOST" ]] || die "principal login resolved to an unexpected grantee"; }
verify_login "$RUNTIME_USER" "$runtime_password"
verify_login "$MIGRATE_USER" "$migrate_password"

write_atomic() { local destination=$1 content=$2 temp parent; parent=$(dirname "$destination"); [ -d "$parent" ] || mkdir -p "$parent"; temp=$(mktemp "${destination}.tmp.XXXXXX"); printf '%s' "$content" > "$temp"; chown root:root "$temp"; chmod 600 "$temp"; mv -f "$temp" "$destination"; }
runtime_content=
for key in "${!CFG[@]}"; do
  case "$key" in JAX_DB_USER|JAX_DB_PASSWORD|JAX_MIGRATE_*|JAX_BOOTSTRAP_*|JAX_DB_GRANTEE_HOST|JAX_DB_ADMIN_*) continue ;; esac
  runtime_content+="$key=${CFG[$key]}"$'\n'
done
runtime_content+="JAX_DB_USER=$RUNTIME_USER"$'\n'
runtime_content+="JAX_DB_PASSWORD=$runtime_password"$'\n'
migrate_content="JAX_DB_HOST=${CFG[JAX_DB_HOST]}"$'\n'
migrate_content+="JAX_DB_PORT=${CFG[JAX_DB_PORT]}"$'\n'
migrate_content+="JAX_DB_NAME=$DB"$'\n'
migrate_content+="JAX_DB_USER=$MIGRATE_USER"$'\n'
migrate_content+="JAX_DB_PASSWORD=$migrate_password"$'\n'
for key in JAX_REPO_PATH JAX_EJECUTOR_INVENTARIO; do [[ -v "CFG[$key]" ]] && migrate_content+="$key=${CFG[$key]}"$'\n'; done
migrate_content+="JAX_MIGRATE_DB_READINESS_ATTEMPTS=${CFG[JAX_MIGRATE_DB_READINESS_ATTEMPTS]:-12}"$'\n'
migrate_content+="JAX_MIGRATE_DB_READINESS_TIMEOUT_SECONDS=${CFG[JAX_MIGRATE_DB_READINESS_TIMEOUT_SECONDS]:-10}"$'\n'
write_atomic "$RUNTIME_ENV" "$runtime_content"
write_atomic "$MIGRATE_ENV" "$migrate_content"

if [ "$PREPARE_OS" = 1 ]; then
  for os_user in jaxweb jaxmigrate; do
    if ! getent passwd "$os_user" >/dev/null; then useradd --system --user-group --home-dir /nonexistent --shell /usr/sbin/nologin "$os_user"; fi
  done
  install -d -m 0755 -o root -g root /srv/jax-prod
fi

if [ "$PREPARE_OS" = 1 ]; then for unit in jax-platform.service jax-platform-migrate.service; do
  dropin_dir="/etc/systemd/system/$unit.d"
  [ -d "$dropin_dir" ] || continue
  expected_user=jaxweb; [ "$unit" = jax-platform-migrate.service ] && expected_user=jaxmigrate
  if grep -RHE '^[[:space:]]*User=' "$dropin_dir" | grep -vE "User=${expected_user}[[:space:]]*$" >/dev/null; then
    die "a $unit drop-in overrides its dedicated OS identity"
  fi
done
fi
if [ "$PREPARE_OS" = 1 ]; then
  note "provisioned dedicated DB and OS identities; install units then verify effective systemd drop-ins before activation"
else
  note "provisioned dedicated DB identities; OS identity preparation was skipped by explicit fixture mode"
fi
