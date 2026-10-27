#!/usr/bin/env bash
# Provision the two untrusted JAX Platform database principals.
#
# This deliberately does not read an env file as shell code.  It is invoked by
# restore-hall9000.sh after the encrypted legacy env has been installed.
set -euo pipefail
umask 077

MODE=apply
MIGRATOR_ONLY=0
ENV_FILE=/etc/jax/.env
RUNTIME_ENV=/etc/jax/runtime.env
MIGRATE_ENV=/etc/jax/migrate.env
SECRET_DIR=/etc/jax/secrets
MEASURED_HOST=
PREPARE_OS=1
ADMIN_CONTAINER="${JAX_DB_ADMIN_CONTAINER:-mariadb-12-3-jax}"
ADMIN_SECRET_FILE="${JAX_DB_ADMIN_SECRET_FILE:-/run/secrets/mariadb_root_password}"
ADMIN_TRANSPORT=
MANIFEST_FILE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/runtime-table-privileges-v2.txt"
V1_MANIFEST_FILE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/runtime-table-privileges-v1.txt"

usage() {
  printf '%s\n' 'usage: provision-db-runtime.sh [--plan] [--migrator-only] [--state-root DIR] [--admin-transport PATH] [--env FILE] --measured-grantee-host HOST [--skip-os-identities]'
}
while [ "$#" -gt 0 ]; do
  case "$1" in
    --plan) MODE=plan ;;
    --migrator-only) MIGRATOR_ONLY=1 ;;
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
declare -A RUNTIME_ALLOWED=()
declare -A OFFICE_INVITATION_CFG=()
OFFICE_INVITATION_KEYS=(OFFICE_INVITATION_LIFETIME_SECONDS OFFICE_INVITATION_CLAIM_TIMEOUT_SECONDS OFFICE_INVITATION_STALE_CLAIM_BATCH_LIMIT OFFICE_INVITATION_EXPIRY_SWEEP_BATCH_LIMIT OFFICE_INVITATION_DELIVERY_BATCH_LIMIT OFFICE_INVITATION_PUBLIC_BASE_URL OFFICE_PUBLIC_ORIGIN)
load_runtime_allowlist() {
  local pair_exec allowed_keys allowed_key required_runtime_key
  # The service executes this installed helper, so provisioning derives the
  # allowlist from those exact bytes.  A source checkout must never silently
  # create a credential the installed launcher rejects.  The override exists
  # solely for the root-owned disposable fixture, which deliberately has no
  # installed runtime package.
  pair_exec=/usr/local/libexec/jax-platform-pair-exec.py
  if [[ -n ${JAX_PAIR_EXEC_FIXTURE:-} ]]; then
    [[ -n $ADMIN_TRANSPORT ]] || die "pair-exec fixture override requires test transport"
    pair_exec=$JAX_PAIR_EXEC_FIXTURE
  fi
  [[ -f $pair_exec && ! -L $pair_exec ]] || die "installed runtime credential launcher is unavailable"
  allowed_keys=$(python3 -I -S "$pair_exec" --list-allowed-keys) || die "runtime credential allowlist is unavailable"
  while IFS= read -r allowed_key; do
    [[ -z $allowed_key ]] || RUNTIME_ALLOWED["$allowed_key"]=1
  done <<< "$allowed_keys"
  [[ ${#RUNTIME_ALLOWED[@]} -gt 0 ]] || die "runtime credential allowlist is empty"
  # These are consumed by the platform's Ejecutor subprocess.  Reject a stale
  # installed launcher before it can silently strip them while making runtime.env.
  for required_runtime_key in JAX_GITHUB_TOKEN JAX_EJECUTOR_MISIONES JAX_EJECUTOR_POLITICA JAX_EJECUTOR_TURNO_TOPE_S JAX_EJECUTOR_VIGIA_ESPERA_S JAX_PROXY_CARRIL_MODELO JAX_PROXY_CARRIL_MAX_SALIDA_TOKENS; do
    [[ -n ${RUNTIME_ALLOWED[$required_runtime_key]+yes} ]] || die "installed runtime credential launcher lacks required key: $required_runtime_key"
  done
}
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
load_office_invitation_config() {
  local source=$1 strict_mode=$2 parsed line key value
  if [ "$strict_mode" = runtime ]; then
    [[ -f $source && ! -L $source ]] || die "existing runtime Office invitation configuration is unavailable"
    [[ $(stat -c '%U:%G:%a' "$source") == root:root:600 ]] || die "existing runtime Office invitation configuration has unsafe permissions"
  fi
  parsed=$(python3 -I -S - "$source" "$strict_mode" <<'PY'
import os, re, stat, sys
from pathlib import Path
from urllib.parse import urlsplit

path = Path(sys.argv[1])
strict_mode = sys.argv[2]
keys = (
    "OFFICE_INVITATION_LIFETIME_SECONDS", "OFFICE_INVITATION_CLAIM_TIMEOUT_SECONDS",
    "OFFICE_INVITATION_STALE_CLAIM_BATCH_LIMIT", "OFFICE_INVITATION_EXPIRY_SWEEP_BATCH_LIMIT",
    "OFFICE_INVITATION_DELIVERY_BATCH_LIMIT", "OFFICE_INVITATION_PUBLIC_BASE_URL",
    "OFFICE_PUBLIC_ORIGIN",
)
values = {}
try:
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        if key in keys:
            if key in values:
                raise ValueError("duplicate")
            values[key] = value
except (OSError, UnicodeDecodeError, ValueError):
    raise SystemExit(1)
if set(values) != set(keys):
    raise SystemExit(1)
limits = {
    "OFFICE_INVITATION_LIFETIME_SECONDS": (1, 604800),
    "OFFICE_INVITATION_CLAIM_TIMEOUT_SECONDS": (1, 3600),
    "OFFICE_INVITATION_STALE_CLAIM_BATCH_LIMIT": (1, 1000),
    "OFFICE_INVITATION_EXPIRY_SWEEP_BATCH_LIMIT": (1, 100),
    "OFFICE_INVITATION_DELIVERY_BATCH_LIMIT": (1, 25),
}
for key, (low, high) in limits.items():
    raw = values[key]
    if not re.fullmatch(r"[1-9][0-9]*", raw) or not low <= int(raw) <= high:
        raise SystemExit(1)
def origin(raw):
    if not raw or raw != raw.strip() or len(raw) > 2048:
        raise ValueError
    parsed = urlsplit(raw)
    if (parsed.scheme != "https" or not parsed.netloc or parsed.hostname is None
            or not parsed.hostname.isascii() or parsed.username is not None
            or parsed.password is not None or parsed.path or parsed.query or parsed.fragment
            or (parsed.port is not None and not 1 <= parsed.port <= 65535)):
        raise ValueError
    return raw
try:
    public_base = origin(values["OFFICE_INVITATION_PUBLIC_BASE_URL"])
    public_origin = origin(values["OFFICE_PUBLIC_ORIGIN"])
except ValueError:
    raise SystemExit(1)
if public_base != public_origin:
    raise SystemExit(1)
for key in keys:
    print(f"{key}={values[key]}")
PY
) || die "Office invitation configuration is incomplete or invalid"
  while IFS= read -r line; do
    key=${line%%=*}; value=${line#*=}
    OFFICE_INVITATION_CFG["$key"]=$value
  done <<< "$parsed"
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
if [[ ${CFG[OFFICE_DEDICATED_DB]:-} == 1 ]]; then
  [[ ${CFG[JAX_DB_NAME]} != jax_memory && ${CFG[JAX_DB_PORT]} != 3308 ]] || die "Office must use its own MariaDB target"
  for key in OFFICE_DB_SERVER_ID OFFICE_DB_HOSTNAME OFFICE_MFA_POLICY; do need "$key"; done
  [[ ${CFG[OFFICE_MFA_POLICY]} == optional ]] || die "Office MFA policy must match the product decision"
  [[ ${CFG[OFFICE_DB_SERVER_ID]} =~ ^[1-9][0-9]*$ ]] || die "invalid Office MariaDB server id"
  [[ ${CFG[OFFICE_DB_HOSTNAME]} =~ ^[A-Za-z0-9][A-Za-z0-9.-]*$ ]] || die "invalid Office MariaDB hostname"
fi

RUNTIME_USER=${CFG[JAX_RUNTIME_DB_USER]}
MIGRATE_USER=${CFG[JAX_MIGRATE_DB_USER]}
HOST=${CFG[JAX_DB_GRANTEE_HOST]}
DB=${CFG[JAX_DB_NAME]}
[[ "$RUNTIME_USER" != "$MIGRATE_USER" ]] || die "database principal names must differ"
[[ "$RUNTIME_USER" =~ ^[A-Za-z0-9_]{1,32}$ && "$MIGRATE_USER" =~ ^[A-Za-z0-9_]{1,32}$ ]] || die "invalid database principal name"
[[ ${CFG[JAX_DB_USER]:-} != "$RUNTIME_USER" && ${CFG[JAX_DB_USER]:-} != "$MIGRATE_USER" ]] || die "legacy principal must remain distinct"
if [ "$MIGRATOR_ONLY" = 0 ]; then
  load_runtime_allowlist
  if [[ ${CFG[OFFICE_DEDICATED_DB]:-} == 1 ]]; then
    for key in OFFICE_DEDICATED_DB OFFICE_DB_SERVER_ID OFFICE_DB_HOSTNAME OFFICE_MFA_POLICY; do
      [[ -n ${RUNTIME_ALLOWED[$key]+yes} ]] || die "installed runtime credential launcher lacks required key: $key"
    done
  fi
  [[ -n ${RUNTIME_ALLOWED[OFFICE_BROKER_ALLOWED_BASE_URLS]+yes} ]] || die "installed runtime credential launcher lacks required key: OFFICE_BROKER_ALLOWED_BASE_URLS"
  [[ -n ${CFG[OFFICE_BROKER_ALLOWED_BASE_URLS]+yes} && ${CFG[OFFICE_BROKER_ALLOWED_BASE_URLS]} == https://api.z.ai/api/paas/v4 ]] || die "Office broker allowlist must be the approved HTTPS Z.ai base URL"
  office_keys_in_deployment=0
  for key in "${OFFICE_INVITATION_KEYS[@]}"; do [[ -v "CFG[$key]" ]] && office_keys_in_deployment=$((office_keys_in_deployment + 1)); done
  if [ "$office_keys_in_deployment" = 0 ]; then
    load_office_invitation_config "$RUNTIME_ENV" runtime
  elif [ "$office_keys_in_deployment" = "${#OFFICE_INVITATION_KEYS[@]}" ]; then
    load_office_invitation_config "$ENV_FILE" deployment
  else
    die "Office invitation configuration is incomplete in deployment environment"
  fi
fi

declare -a RUNTIME_TABLES=()
declare -A RUNTIME_TABLE_PRIVILEGES=()
declare -a V1_RUNTIME_TABLES=()
declare -A V1_RUNTIME_TABLE_PRIVILEGES=()
load_runtime_manifest() {
  [[ -r "$MANIFEST_FILE" ]] || die "runtime table privilege manifest is not readable"
  local line table privileges extra previous=
  while IFS= read -r line || [ -n "$line" ]; do
    [[ -z "$line" || "$line" == \#* ]] && continue
    read -r table privileges extra <<< "$line"
    [[ -n "$table" && -n "$privileges" && -z ${extra:-} ]] || die "runtime table privilege manifest is malformed"
    [[ "$table" =~ ^[a-z][a-z0-9_]{0,63}$ ]] || die "runtime table privilege manifest has invalid table"
    [[ "$privileges" =~ ^(INSERT|UPDATE|DELETE)(,(INSERT|UPDATE|DELETE)){0,2}$ ]] || die "runtime table privilege manifest has invalid privileges"
    [[ "$table" != office_schema_revision && "$table" != office_module_candidate && "$table" != office_module_candidate_revision && "$table" != office_module_candidate_evidence && "$table" != office_module_creative_review_event && "$table" != office_module_creative_review_head && "$table" != office_module_rights_review_event && "$table" != office_module_rights_review_head && "$table" != office_module_release_event && "$table" != office_module_release ]] || die "runtime table privilege manifest grants a protected table"
    [[ -z "$previous" || "$table" > "$previous" ]] || die "runtime table privilege manifest is not ordered"
    previous=$table
    RUNTIME_TABLES+=("$table")
    RUNTIME_TABLE_PRIVILEGES["$table"]=$privileges
  done < "$MANIFEST_FILE"
  [ "${#RUNTIME_TABLES[@]}" -gt 0 ] || die "runtime table privilege manifest is empty"
}
load_runtime_manifest

# v1 was the predecessor with schema INSERT.  It is accepted only if every
# old table grant is exact, then converged by adding v2's named writes before
# schema INSERT is revoked.  Any other grant shape remains fail-closed.
load_v1_runtime_manifest() {
  [[ -r "$V1_MANIFEST_FILE" ]] || die "v1 runtime table privilege manifest is not readable"
  local line table privileges extra previous=
  while IFS= read -r line || [ -n "$line" ]; do
    [[ -z "$line" || "$line" == \#* ]] && continue
    read -r table privileges extra <<< "$line"
    [[ -n "$table" && -n "$privileges" && -z ${extra:-} ]] || die "v1 runtime table privilege manifest is malformed"
    [[ "$table" =~ ^[a-z][a-z0-9_]{0,63}$ && "$privileges" =~ ^(UPDATE|DELETE)(,(UPDATE|DELETE))?$ ]] || die "v1 runtime table privilege manifest is invalid"
    [[ -z "$previous" || "$table" > "$previous" ]] || die "v1 runtime table privilege manifest is not ordered"
    previous=$table
    V1_RUNTIME_TABLES+=("$table")
    V1_RUNTIME_TABLE_PRIVILEGES["$table"]=$privileges
  done < "$V1_MANIFEST_FILE"
  [ "${#V1_RUNTIME_TABLES[@]}" -gt 0 ] || die "v1 runtime table privilege manifest is empty"
}
load_v1_runtime_manifest

verify_runtime_manifest_tables() {
  # v2 names INSERT explicitly, so a database restored from a pre-v2 backup
  # cannot be provisioned as though it had the new tables.  The migrator is
  # the only principal that may create them; fail before touching web grants.
  local table count
  for table in "${RUNTIME_TABLES[@]}"; do
    count=$(printf "SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s;\n" "$(sql_quote "$DB")" "$(sql_quote "$table")" | admin_sql) || die "could not inspect runtime manifest table $table"
    [[ "$count" == 1 ]] || die "runtime manifest table is absent: $table; run the migrator before provisioning v2"
  done
}

if [ "$MODE" = plan ]; then
  if [ "$MIGRATOR_ONLY" = 1 ]; then
    note "plan: provision only $MIGRATE_USER@$HOST (migration DDL) and root-only migrate.env"
    exit 0
  fi
  note "plan: preserve legacy principal; provision $RUNTIME_USER@$HOST (schema SELECT plus named table DML) and $MIGRATE_USER@$HOST (migration DDL)"
  note "plan: write root-only runtime.env and migrate.env; web=jaxweb migrator=jaxmigrate artifact=/srv/jax-prod"
  exit 0
fi

[ "$(id -u)" = 0 ] || die "apply must run as root"
if [[ ${CFG[OFFICE_DEDICATED_DB]:-} == 1 && -z $ADMIN_TRANSPORT ]]; then
  die "Office provisioning requires its dedicated MariaDB admin transport"
fi
if [[ ${CFG[OFFICE_DEDICATED_DB]:-} == 1 && $PREPARE_OS == 1 ]]; then
  die "Office service identities must be prepared separately; pass --skip-os-identities"
fi
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
if [[ ${CFG[OFFICE_DEDICATED_DB]:-} == 1 ]]; then
  server_identity=$(printf 'SELECT @@server_id,@@hostname;\n' | admin_sql) || die "could not inspect Office MariaDB server"
  [[ $server_identity == "${CFG[OFFICE_DB_SERVER_ID]}"$'\t'"${CFG[OFFICE_DB_HOSTNAME]}" ]] || die "admin transport is not connected to the dedicated Office MariaDB"
fi
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
migrate_secret_file=$(password_file_for "$MIGRATE_USER")
migrate_exists=$(principal_exists "$MIGRATE_USER")
if [ "$MIGRATOR_ONLY" = 0 ]; then
  runtime_secret_file=$(password_file_for "$RUNTIME_USER")
  runtime_exists=$(principal_exists "$RUNTIME_USER")
fi

if { [ "$MIGRATOR_ONLY" = 0 ] && [ "$runtime_exists" = 1 ] && [ ! -f "$runtime_secret_file" ]; } || { [ "$migrate_exists" = 1 ] && [ ! -f "$migrate_secret_file" ]; }; then
  die "an existing database principal lacks its root-only secret file; refusing to alter it"
fi
if { [ "$MIGRATOR_ONLY" = 0 ] && [ "$runtime_exists" = 0 ] && [ -f "$runtime_secret_file" ]; } || { [ "$migrate_exists" = 0 ] && [ -f "$migrate_secret_file" ]; }; then
  note "converging a missing database principal from its existing root-only secret file"
fi

if [ "$MIGRATOR_ONLY" = 0 ]; then
  if [ -f "$runtime_secret_file" ]; then runtime_password=$(read_secret "$runtime_secret_file"); else runtime_password=$(new_secret); fi
  [ -f "$runtime_secret_file" ] || write_secret "$runtime_secret_file" "$runtime_password"
fi
if [ -f "$migrate_secret_file" ]; then migrate_password=$(read_secret "$migrate_secret_file"); else migrate_password=$(new_secret); fi
[ -f "$migrate_secret_file" ] || write_secret "$migrate_secret_file" "$migrate_password"

verify_password() {
  local user=$1 pass=$2 stored
  stored=$(printf "SELECT JSON_UNQUOTE(JSON_EXTRACT(Priv, '$.authentication_string')) = PASSWORD(%s) FROM mysql.global_priv WHERE User=%s AND Host=%s;\n" "$(sql_quote "$pass")" "$(sql_quote "$user")" "$(sql_quote "$HOST")" | admin_sql) || die "could not verify principal secret"
  [[ "$stored" == 1 ]] || die "existing database principal secret does not match; refusing to alter it"
}
[ "$MIGRATOR_ONLY" = 1 ] || [ "$runtime_exists" = 0 ] || verify_password "$RUNTIME_USER" "$runtime_password"
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
  expected_set=$(normalize_set 'SELECT, INSERT, UPDATE, DELETE, CREATE, DROP, ALTER, INDEX, TRIGGER, REFERENCES, CREATE VIEW')
  [ "$actual_set" = "$expected_set" ] || die "migration principal schema privilege set is not exact"
  [ "$(printf '%s\n' "$grants" | grep -c '^GRANT ')" -eq 2 ] || die "migration principal has extra grants, roles, routines, columns, or grant option"
}

runtime_grant_state() {
  local grants schema_line actual_set legacy_set line table privileges expected_count actual_count prefix_count index mixed_prefix expected_mixed_count expected_privileges matches remaining pre_agent_count
  grants=$(show_grants "$RUNTIME_USER") || die "could not inspect runtime grants"
  printf '%s\n' "$grants" | grep -Fx "GRANT USAGE ON *.* TO '$RUNTIME_USER'@'$HOST'" >/dev/null || { printf invalid; return; }
  schema_line=$(printf '%s\n' "$grants" | grep -E "^GRANT .* ON '$DB'\\.\\* TO '$RUNTIME_USER'@'$HOST'$" || true)
  # CREATE USER leaves only the exact USAGE grant.  This is a safe prefix of
  # a new principal; no pre-existing capability is repaired or broadened.
  if [ -z "$schema_line" ]; then
    [ "$(printf '%s\n' "$grants" | grep -c '^GRANT ')" -eq 1 ] || { printf invalid; return; }
    printf 'new-prefix:0'; return
  fi
  [ "$(printf '%s\n' "$schema_line" | wc -l)" -eq 1 ] || { printf invalid; return; }
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
  if [ "$actual_set" = "$(normalize_set 'SELECT, INSERT')" ]; then
    # This can occur only after the v2 table grants have been made and before
    # the final schema INSERT revoke.  Check the whole v2 set here; a real
    # v11 principal has different table rows and is handled below as v1.
    prefix_count=$((actual_count - 2))
    [ "$prefix_count" -ge 0 ] && [ "$prefix_count" -le "${#RUNTIME_TABLES[@]}" ] || { printf invalid; return; }
    index=0
    for ((index=0; index<prefix_count; index++)); do
      table=${RUNTIME_TABLES[$index]}; privileges=${RUNTIME_TABLE_PRIVILEGES[$table]}
      line=$(printf '%s\n' "$grants" | grep -E "^GRANT .* ON '$DB'\\.'$table' TO '$RUNTIME_USER'@'$HOST'$" || true)
      [ "$(printf '%s\n' "$line" | wc -l)" -eq 1 ] || break
      actual_set=${line#GRANT }; actual_set=${actual_set%% ON *}
      [ "$(normalize_set "$actual_set")" = "$(normalize_set "$privileges")" ] || break
    done
    if [ "$index" -eq "$prefix_count" ] && [ "$prefix_count" -eq "${#RUNTIME_TABLES[@]}" ] && [ "$actual_count" -eq "$expected_count" ]; then
      # Every named v2 grant may be durable before the final REVOKE.  This is
      # still a transition: reporting it as `new` would leave broad INSERT.
      printf 'new-schema-insert'; return
    fi
    # A v11/v1 principal begins with all v1 UPDATE/DELETE rows and schema
    # INSERT.  A v2 GRANT may merge an existing row without increasing the
    # SHOW GRANTS count (axioma_config is the first such row).  Recognize
    # every exact durable v2 prefix against the remaining v1 rows instead of
    # inferring progress from row count alone.
    mixed_prefix=0; expected_mixed_count=2; matches=1; remaining=0
    for ((index=0; index<${#RUNTIME_TABLES[@]}; index++)); do
      table=${RUNTIME_TABLES[$index]}
      line=$(printf '%s\n' "$grants" | grep -E "^GRANT .* ON '$DB'\\.'$table' TO '$RUNTIME_USER'@'$HOST'$" || true)
      if [ "$remaining" = 0 ] && [ "$(printf '%s\n' "$line" | wc -l)" -eq 1 ]; then
        actual_set=${line#GRANT }; actual_set=${actual_set%% ON *}
        if [ "$(normalize_set "$actual_set")" = "$(normalize_set "${RUNTIME_TABLE_PRIVILEGES[$table]}")" ]; then
          mixed_prefix=$((index + 1)); expected_mixed_count=$((expected_mixed_count + 1)); continue
        fi
      fi
      remaining=1
      if [[ -v "V1_RUNTIME_TABLE_PRIVILEGES[$table]" ]]; then
        expected_privileges=${V1_RUNTIME_TABLE_PRIVILEGES[$table]}
        [ "$(printf '%s\n' "$line" | wc -l)" -eq 1 ] || { matches=0; break; }
        actual_set=${line#GRANT }; actual_set=${actual_set%% ON *}
        [ "$(normalize_set "$actual_set")" = "$(normalize_set "$expected_privileges")" ] || { matches=0; break; }
        expected_mixed_count=$((expected_mixed_count + 1))
      else
        [ -z "$line" ] || { matches=0; break; }
      fi
    done
    [ "$matches" = 1 ] && [ "$mixed_prefix" -lt "${#RUNTIME_TABLES[@]}" ] && [ "$actual_count" -eq "$expected_mixed_count" ] && { printf 'v1-v2-prefix:%s' "$mixed_prefix"; return; }
  elif [ "$actual_set" = "SELECT" ]; then
    # The Office-agent selector appends these three tables in the middle of
    # the sorted v2 manifest.  A principal provisioned immediately before
    # that release has every other exact v2 row, so it is not an ordered v2
    # prefix.  Accept only that precise predecessor, including an interrupted
    # ordered grant prefix for these three rows; any missing non-agent row or
    # extra capability remains invalid.
    pre_agent_count=0
    for table in office_agent office_agent_binding office_agent_run; do
      line=$(printf '%s\n' "$grants" | grep -E "^GRANT .* ON '$DB'\\.'$table' TO '$RUNTIME_USER'@'$HOST'$" || true)
      if [ -z "$line" ]; then
        break
      fi
      [ "$(printf '%s\n' "$line" | wc -l)" -eq 1 ] || { printf invalid; return; }
      actual_set=${line#GRANT }; actual_set=${actual_set%% ON *}
      [ "$(normalize_set "$actual_set")" = "$(normalize_set "${RUNTIME_TABLE_PRIVILEGES[$table]}")" ] || { printf invalid; return; }
      pre_agent_count=$((pre_agent_count + 1))
    done
    matches=1
    for table in "${RUNTIME_TABLES[@]}"; do
      case "$table" in office_agent|office_agent_binding|office_agent_run) continue;; esac
      privileges=${RUNTIME_TABLE_PRIVILEGES[$table]}
      line=$(printf '%s\n' "$grants" | grep -E "^GRANT .* ON '$DB'\\.'$table' TO '$RUNTIME_USER'@'$HOST'$" || true)
      [ "$(printf '%s\n' "$line" | wc -l)" -eq 1 ] || { matches=0; break; }
      actual_set=${line#GRANT }; actual_set=${actual_set%% ON *}
      [ "$(normalize_set "$actual_set")" = "$(normalize_set "$privileges")" ] || { matches=0; break; }
    done
    if [ "$matches" = 1 ] && [ "$pre_agent_count" -lt 3 ] && [ "$actual_count" -eq "$((expected_count - 3 + pre_agent_count))" ]; then
      printf 'pre-agent-prefix:%s' "$pre_agent_count"; return
    fi
    prefix_count=$((actual_count - 2))
    [ "$prefix_count" -ge 0 ] && [ "$prefix_count" -le "${#RUNTIME_TABLES[@]}" ] || { printf invalid; return; }
    for ((index=0; index<prefix_count; index++)); do
      table=${RUNTIME_TABLES[$index]}; privileges=${RUNTIME_TABLE_PRIVILEGES[$table]}
      line=$(printf '%s\n' "$grants" | grep -E "^GRANT .* ON '$DB'\\.'$table' TO '$RUNTIME_USER'@'$HOST'$" || true)
      [ "$(printf '%s\n' "$line" | wc -l)" -eq 1 ] || { printf invalid; return; }
      actual_set=${line#GRANT }; actual_set=${actual_set%% ON *}
      [ "$(normalize_set "$actual_set")" = "$(normalize_set "$privileges")" ] || { printf invalid; return; }
    done
    [ "$prefix_count" -eq "${#RUNTIME_TABLES[@]}" ] && [ "$actual_count" -eq "$expected_count" ] || { printf 'new-prefix:%s' "$prefix_count"; return; }
    printf new; return
  else
    printf invalid; return
  fi
  # v1 can have been interrupted after any ordered old-table grant.  Verify
  # that exact prefix before adding v2's named INSERT grants; arbitrary table
  # grants still fail closed.
  prefix_count=$((actual_count - 2))
  if [ "$prefix_count" -ge 0 ] && [ "$prefix_count" -le "${#V1_RUNTIME_TABLES[@]}" ]; then
    index=0
    for ((index=0; index<prefix_count; index++)); do
      table=${V1_RUNTIME_TABLES[$index]}
      privileges=${V1_RUNTIME_TABLE_PRIVILEGES[$table]}
      line=$(printf '%s\n' "$grants" | grep -E "^GRANT .* ON '$DB'\\.'$table' TO '$RUNTIME_USER'@'$HOST'$" || true)
      [ "$(printf '%s\n' "$line" | wc -l)" -eq 1 ] || break
      actual_set=${line#GRANT }; actual_set=${actual_set%% ON *}
      [ "$(normalize_set "$actual_set")" = "$(normalize_set "$privileges")" ] || break
    done
    [ "$index" -eq "$prefix_count" ] && { printf 'v1-prefix:%s' "$prefix_count"; return; }
  fi
  # A v2 creation can be interrupted after schema SELECT or any ordered table
  # grant; only that exact prefix is resumable.
  prefix_count=$((actual_count - 2))
  [ "$prefix_count" -ge 0 ] && [ "$prefix_count" -le "${#RUNTIME_TABLES[@]}" ] || { printf invalid; return; }
  for ((index=0; index<prefix_count; index++)); do
    table=${RUNTIME_TABLES[$index]}; privileges=${RUNTIME_TABLE_PRIVILEGES[$table]}
    line=$(printf '%s\n' "$grants" | grep -E "^GRANT .* ON '$DB'\\.'$table' TO '$RUNTIME_USER'@'$HOST'$" || true)
    [ "$(printf '%s\n' "$line" | wc -l)" -eq 1 ] || { printf invalid; return; }
    actual_set=${line#GRANT }; actual_set=${actual_set%% ON *}
    [ "$(normalize_set "$actual_set")" = "$(normalize_set "$privileges")" ] || { printf invalid; return; }
  done
  [ "$prefix_count" -eq "${#RUNTIME_TABLES[@]}" ] || { printf 'new-prefix:%s' "$prefix_count"; return; }
  [ "$actual_count" -eq "$expected_count" ] || { printf invalid; return; }
  for table in "${RUNTIME_TABLES[@]}"; do
    privileges=${RUNTIME_TABLE_PRIVILEGES[$table]}
    line=$(printf '%s\n' "$grants" | grep -E "^GRANT .* ON '$DB'\\.'$table' TO '$RUNTIME_USER'@'$HOST'$" || true)
    [ "$(printf '%s\n' "$line" | wc -l)" -eq 1 ] || { printf invalid; return; }
    actual_set=${line#GRANT }; actual_set=${actual_set%% ON *}
    [ "$(normalize_set "$actual_set")" = "$(normalize_set "$privileges")" ] || { printf invalid; return; }
  done
  printf new
}

migrator_grant_state() {
  local grants schema_line actual_set expected_set previous_exact_set grant_count
  grants=$(show_grants "$MIGRATE_USER") || die "could not inspect migration grants"
  printf '%s\n' "$grants" | grep -Fx "GRANT USAGE ON *.* TO '$MIGRATE_USER'@'$HOST'" >/dev/null || { printf invalid; return; }
  schema_line=$(printf '%s\n' "$grants" | grep -E "^GRANT .* ON '$DB'\\.\\* TO '$MIGRATE_USER'@'$HOST'$" || true)
  grant_count=$(printf '%s\n' "$grants" | grep -c '^GRANT ')
  if [ -z "$schema_line" ]; then
    [ "$grant_count" -eq 1 ] || { printf invalid; return; }
    printf 'new-prefix:0'; return
  fi
  [ "$(printf '%s\n' "$schema_line" | wc -l)" -eq 1 ] && [ "$grant_count" -eq 2 ] || { printf invalid; return; }
  actual_set=${schema_line#GRANT }; actual_set=${actual_set%% ON *}; actual_set=$(normalize_set "$actual_set")
  expected_set=$(normalize_set 'SELECT, INSERT, UPDATE, DELETE, CREATE, DROP, ALTER, INDEX, TRIGGER, REFERENCES, CREATE VIEW')
  previous_exact_set=$(normalize_set 'SELECT, INSERT, UPDATE, DELETE, CREATE, DROP, ALTER, INDEX, TRIGGER, REFERENCES')
  [ "$actual_set" = "$expected_set" ] && { printf new; return; }
  # The only accepted predecessor is the exact schema grant emitted before
  # views became part of the migration contract.  Upgrade it with the one
  # missing capability; every other difference stays fail-closed.
  [ "$actual_set" = "$previous_exact_set" ] && { printf 'upgrade-create-view'; return; }
  printf invalid
}

if [ "$MIGRATOR_ONLY" = 0 ]; then create_missing "$RUNTIME_USER" "$runtime_password" "$runtime_exists"; fi
create_missing "$MIGRATE_USER" "$migrate_password" "$migrate_exists"
if [ "$MIGRATOR_ONLY" = 0 ]; then
  verify_runtime_manifest_tables
  runtime_state=$(runtime_grant_state)
  case "$runtime_state" in
    new) ;;
    new-prefix:*)
      prefix_count=${runtime_state#new-prefix:}
      [[ "$prefix_count" =~ ^[0-9]+$ ]] || die "runtime principal transitional state is malformed"
      if [ "$prefix_count" = 0 ]; then
        printf 'GRANT SELECT ON `%s`.* TO %s@%s;\n' "$DB" "$(sql_quote "$RUNTIME_USER")" "$(sql_quote "$HOST")" | admin_sql >/dev/null || die "could not grant runtime schema privileges"
      fi
      for ((index=prefix_count; index<${#RUNTIME_TABLES[@]}; index++)); do
        table=${RUNTIME_TABLES[$index]}
        printf 'GRANT %s ON `%s`.`%s` TO %s@%s;\n' "${RUNTIME_TABLE_PRIVILEGES[$table]}" "$DB" "$table" "$(sql_quote "$RUNTIME_USER")" "$(sql_quote "$HOST")" | admin_sql >/dev/null || die "could not converge runtime table privileges"
      done
      ;;
    new-schema-insert)
      # The only accepted post-GRANT cut point has every exact v2 grant and
      # no additional capability.  Finish the interrupted convergence.
      printf 'REVOKE INSERT ON `%s`.* FROM %s@%s;\n' "$DB" "$(sql_quote "$RUNTIME_USER")" "$(sql_quote "$HOST")" | admin_sql >/dev/null || die "could not revoke transitional runtime schema INSERT"
      ;;
    legacy-prefix:*)
      # Add the narrow table grants before revoking broad schema DML.
      prefix_count=${runtime_state#legacy-prefix:}
      [[ "$prefix_count" =~ ^[0-9]+$ ]] || die "runtime principal transitional state is malformed"
      for ((index=prefix_count; index<${#RUNTIME_TABLES[@]}; index++)); do
        table=${RUNTIME_TABLES[$index]}
        printf 'GRANT %s ON `%s`.`%s` TO %s@%s;\n' "${RUNTIME_TABLE_PRIVILEGES[$table]}" "$DB" "$table" "$(sql_quote "$RUNTIME_USER")" "$(sql_quote "$HOST")" | admin_sql >/dev/null || die "could not converge runtime table privileges"
      done
      printf 'REVOKE INSERT, UPDATE, DELETE ON `%s`.* FROM %s@%s;\n' "$DB" "$(sql_quote "$RUNTIME_USER")" "$(sql_quote "$HOST")" | admin_sql >/dev/null || die "could not revoke legacy runtime schema privileges"
      ;;
    v1-prefix:*)
      prefix_count=${runtime_state#v1-prefix:}
      [[ "$prefix_count" =~ ^[0-9]+$ ]] || die "v1 runtime principal transitional state is malformed"
      # GRANT merges an existing v1 UPDATE/DELETE row with v2's INSERT
      # without ever removing the old ability before the replacement exists.
      for table in "${RUNTIME_TABLES[@]}"; do
        printf 'GRANT %s ON `%s`.`%s` TO %s@%s;\n' "${RUNTIME_TABLE_PRIVILEGES[$table]}" "$DB" "$table" "$(sql_quote "$RUNTIME_USER")" "$(sql_quote "$HOST")" | admin_sql >/dev/null || die "could not converge v1 runtime table privileges"
      done
      printf 'REVOKE INSERT ON `%s`.* FROM %s@%s;\n' "$DB" "$(sql_quote "$RUNTIME_USER")" "$(sql_quote "$HOST")" | admin_sql >/dev/null || die "could not revoke v1 runtime schema INSERT"
      ;;
    v1-v2-prefix:*)
      prefix_count=${runtime_state#v1-v2-prefix:}
      [[ "$prefix_count" =~ ^[0-9]+$ && "$prefix_count" -le "${#RUNTIME_TABLES[@]}" ]] || die "v1/v2 runtime principal transitional state is malformed"
      for ((index=prefix_count; index<${#RUNTIME_TABLES[@]}; index++)); do
        table=${RUNTIME_TABLES[$index]}
        printf 'GRANT %s ON `%s`.`%s` TO %s@%s;\n' "${RUNTIME_TABLE_PRIVILEGES[$table]}" "$DB" "$table" "$(sql_quote "$RUNTIME_USER")" "$(sql_quote "$HOST")" | admin_sql >/dev/null || die "could not converge mixed v1/v2 runtime table privileges"
      done
      printf 'REVOKE INSERT ON `%s`.* FROM %s@%s;\n' "$DB" "$(sql_quote "$RUNTIME_USER")" "$(sql_quote "$HOST")" | admin_sql >/dev/null || die "could not revoke mixed v1/v2 runtime schema INSERT"
      ;;
    pre-agent-prefix:*)
      prefix_count=${runtime_state#pre-agent-prefix:}
      [[ "$prefix_count" =~ ^[0-2]$ ]] || die "pre-agent runtime principal transitional state is malformed"
      agent_tables=(office_agent office_agent_binding office_agent_run)
      for ((index=prefix_count; index<${#agent_tables[@]}; index++)); do
        table=${agent_tables[$index]}
        printf 'GRANT %s ON `%s`.`%s` TO %s@%s;\n' "${RUNTIME_TABLE_PRIVILEGES[$table]}" "$DB" "$table" "$(sql_quote "$RUNTIME_USER")" "$(sql_quote "$HOST")" | admin_sql >/dev/null || die "could not converge pre-agent runtime table privileges"
      done
      ;;
    *) die "runtime principal is neither the exact legacy nor new grant state" ;;
  esac
fi
migrate_state=$(migrator_grant_state)
case "$migrate_state" in
  new) ;;
  new-prefix:0) printf 'GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, DROP, ALTER, INDEX, TRIGGER, REFERENCES, CREATE VIEW ON `%s`.* TO %s@%s;\n' "$DB" "$(sql_quote "$MIGRATE_USER")" "$(sql_quote "$HOST")" | admin_sql >/dev/null || die "could not grant migration principal" ;;
  upgrade-create-view) printf 'GRANT CREATE VIEW ON `%s`.* TO %s@%s;\n' "$DB" "$(sql_quote "$MIGRATE_USER")" "$(sql_quote "$HOST")" | admin_sql >/dev/null || die "could not upgrade migration principal with CREATE VIEW" ;;
  *) die "migration principal is neither the exact new nor create-only grant state" ;;
esac
verify_migrator_grants
[ "$MIGRATOR_ONLY" = 1 ] || [ "$(runtime_grant_state)" = new ] || die "runtime principal table privilege set is not exact"

verify_login() { local user=$1 pass=$2 temp current; temp=$(mktemp "${SECRET_DIR}/login.XXXXXX"); printf '[client]\nuser=%s\npassword=%s\nhost=%s\nport=%s\n' "$user" "$pass" "${CFG[JAX_DB_HOST]}" "${CFG[JAX_DB_PORT]}" > "$temp"; chmod 600 "$temp"; current=$(mariadb "--defaults-extra-file=$temp" --batch --skip-column-names "$DB" -e 'SELECT CURRENT_USER()') || die "principal login failed"; rm -f "$temp"; [[ "$current" == "$user@$HOST" ]] || die "principal login resolved to an unexpected grantee"; }
if [ "$MIGRATOR_ONLY" = 0 ]; then verify_login "$RUNTIME_USER" "$runtime_password"; fi
verify_login "$MIGRATE_USER" "$migrate_password"

write_atomic() { local destination=$1 content=$2 temp parent; parent=$(dirname "$destination"); [ -d "$parent" ] || mkdir -p "$parent"; temp=$(mktemp "${destination}.tmp.XXXXXX"); printf '%s' "$content" > "$temp"; chown root:root "$temp"; chmod 600 "$temp"; mv -f "$temp" "$destination"; }
if [ "$MIGRATOR_ONLY" = 0 ]; then
  runtime_content=
  for key in "${!CFG[@]}"; do
    case "$key" in JAX_DB_USER|JAX_DB_PASSWORD|JAX_RUNTIME_DB_USER|JAX_MIGRATE_*|JAX_BOOTSTRAP_*|JAX_DB_GRANTEE_HOST|JAX_DB_ADMIN_*|OFFICE_INVITATION_*|OFFICE_PUBLIC_ORIGIN) continue ;; esac
    [[ -v "RUNTIME_ALLOWED[$key]" ]] || continue
    runtime_content+="$key=${CFG[$key]}"$'\n'
  done
  for key in "${OFFICE_INVITATION_KEYS[@]}"; do runtime_content+="$key=${OFFICE_INVITATION_CFG[$key]}"$'\n'; done
  runtime_content+="JAX_DB_USER=$RUNTIME_USER"$'\n'
  runtime_content+="JAX_DB_PASSWORD=$runtime_password"$'\n'
fi
migrate_content="JAX_DB_HOST=${CFG[JAX_DB_HOST]}"$'\n'
migrate_content+="JAX_DB_PORT=${CFG[JAX_DB_PORT]}"$'\n'
migrate_content+="JAX_DB_NAME=$DB"$'\n'
migrate_content+="JAX_DB_USER=$MIGRATE_USER"$'\n'
migrate_content+="JAX_DB_PASSWORD=$migrate_password"$'\n'
if [[ ${CFG[OFFICE_DEDICATED_DB]:-} == 1 ]]; then
  migrate_content+="OFFICE_DEDICATED_DB=1"$'\n'
  migrate_content+="OFFICE_DB_SERVER_ID=${CFG[OFFICE_DB_SERVER_ID]}"$'\n'
  migrate_content+="OFFICE_DB_HOSTNAME=${CFG[OFFICE_DB_HOSTNAME]}"$'\n'
  migrate_content+="OFFICE_MFA_POLICY=${CFG[OFFICE_MFA_POLICY]}"$'\n'
fi
for key in JAX_REPO_PATH JAX_EJECUTOR_INVENTARIO; do [[ -v "CFG[$key]" ]] && migrate_content+="$key=${CFG[$key]}"$'\n'; done
migrate_content+="JAX_MIGRATE_DB_READINESS_ATTEMPTS=${CFG[JAX_MIGRATE_DB_READINESS_ATTEMPTS]:-12}"$'\n'
migrate_content+="JAX_MIGRATE_DB_READINESS_TIMEOUT_SECONDS=${CFG[JAX_MIGRATE_DB_READINESS_TIMEOUT_SECONDS]:-10}"$'\n'
if [ "$MIGRATOR_ONLY" = 0 ]; then write_atomic "$RUNTIME_ENV" "$runtime_content"; fi
write_atomic "$MIGRATE_ENV" "$migrate_content"

if [ "$PREPARE_OS" = 1 ]; then
  os_users=(jaxmigrate)
  [ "$MIGRATOR_ONLY" = 0 ] && os_users+=(jaxweb)
  for os_user in "${os_users[@]}"; do
    if ! getent passwd "$os_user" >/dev/null; then useradd --system --user-group --home-dir /nonexistent --shell /usr/sbin/nologin "$os_user"; fi
  done
  install -d -m 0755 -o root -g root /srv/jax-prod
fi

if [ "$PREPARE_OS" = 1 ] && [ "$MIGRATOR_ONLY" = 0 ]; then for unit in jax-platform.service jax-platform-migrate.service; do
  dropin_dir="/etc/systemd/system/$unit.d"
  [ -d "$dropin_dir" ] || continue
  expected_user=jaxweb; [ "$unit" = jax-platform-migrate.service ] && expected_user=jaxmigrate
  if grep -RHE '^[[:space:]]*User=' "$dropin_dir" | grep -vE "User=${expected_user}[[:space:]]*$" >/dev/null; then
    die "a $unit drop-in overrides its dedicated OS identity"
  fi
done
fi
if [ "$MIGRATOR_ONLY" = 1 ]; then
  note "provisioned migration DB and OS identity only; run the migration before provisioning the web runtime"
elif [ "$PREPARE_OS" = 1 ]; then
  note "provisioned dedicated DB and OS identities; install units then verify effective systemd drop-ins before activation"
else
  note "provisioned dedicated DB identities; OS identity preparation was skipped by explicit fixture mode"
fi
