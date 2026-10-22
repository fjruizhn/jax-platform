#!/usr/bin/env bash
# Root-only integration fixture.  Its allowlist is the disposable Office DB.
set -euo pipefail
umask 077

CONTAINER=cv-op343-db-20261020
PORT=33991
DB=jax_memory_test
ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
HELPER="$ROOT_DIR/ops/migration/provision-db-runtime.sh"
[ "$(id -u)" = 0 ] || { echo 'run this fixture with sudo -n only' >&2; exit 2; }
docker inspect "$CONTAINER" >/dev/null
docker port "$CONTAINER" 3306/tcp | grep -qx "127.0.0.1:$PORT" || { echo 'fixture DB allowlist mismatch' >&2; exit 2; }

TEMP=$(mktemp -d /tmp/office-db-provision.XXXXXX)
WEB=officecvweb_$(openssl rand -hex 8)
MIG=officecvmig_$(openssl rand -hex 8)
HOST=
OWNED=0
cleanup() {
  if [ "$OWNED" = 1 ] && [ -n "$HOST" ]; then printf "DROP USER IF EXISTS '%s'@'%s'; DROP USER IF EXISTS '%s'@'%s';\n" "$WEB" "$HOST" "$MIG" "$HOST" | admin >/dev/null 2>&1 || true; fi
  rm -rf "$TEMP"
}
trap cleanup EXIT
admin() { docker exec -i "$CONTAINER" sh -ec 'MYSQL_PWD="$(printenv MARIADB_ROOT_PASSWORD)" exec mariadb -uroot --batch --skip-column-names'; }

ROOT_CNF="$TEMP/root.cnf"
docker inspect "$CONTAINER" --format '{{range .Config.Env}}{{println .}}{{end}}' | sed -n 's/^MARIADB_ROOT_PASSWORD=//p' | {
  IFS= read -r root_password
  printf '[client]\nuser=root\npassword=%s\nhost=127.0.0.1\nport=%s\n' "$root_password" "$PORT" > "$ROOT_CNF"
}
HOST=$(mariadb "--defaults-extra-file=$ROOT_CNF" --batch --skip-column-names -e 'SELECT SUBSTRING_INDEX(USER(), "@", -1)')
case "$HOST" in ''|%|*'_'*|*'%'*) echo 'unsafe measured fixture host' >&2; exit 2;; esac

ENV_FILE="$TEMP/deployment.env"
printf 'JAX_DB_HOST=127.0.0.1\nJAX_DB_PORT=%s\nJAX_DB_NAME=jax_memory_test\nJAX_DB_USER=legacy_untouched\nJAX_DB_PASSWORD=legacy-do-not-copy\nJAX_DB_GRANTEE_HOST=%s\nJAX_RUNTIME_DB_USER=%s\nJAX_MIGRATE_DB_USER=%s\nJAX_JWT_SECRET=fixture-runtime-value\n' "$PORT" "$HOST" "$WEB" "$MIG" > "$ENV_FILE"
TRANSPORT="$TEMP/admin-transport"
printf '#!/bin/sh\nexec docker exec -i %s sh -ec '\''MYSQL_PWD="$(printenv MARIADB_ROOT_PASSWORD)" exec mariadb -uroot --batch --skip-column-names'\''\n' "$CONTAINER" > "$TRANSPORT"
chmod 700 "$TRANSPORT"

absent=$(printf "SELECT COUNT(*) FROM mysql.user WHERE (User='%s' OR User='%s') AND Host='%s';\n" "$WEB" "$MIG" "$HOST" | admin)
[ "$absent" = 0 ] || { echo 'random fixture principal already exists; refusing ownership' >&2; exit 2; }
OWNED=1

run_helper() { "$HELPER" --env "$ENV_FILE" --state-root "$TEMP/state" --admin-transport "$TRANSPORT" --measured-grantee-host "$HOST" --skip-os-identities; }
run_helper
run_helper
test "$(stat -c '%U:%G:%a' "$TEMP/state/secrets/$WEB.password")" = root:root:600
test "$(stat -c '%U:%G:%a' "$TEMP/state/runtime.env")" = root:root:600
grep -qx "JAX_DB_USER=$WEB" "$TEMP/state/runtime.env"
! grep -q 'legacy-do-not-copy\|JAX_MIGRATE_DB_USER' "$TEMP/state/runtime.env"
test "$(wc -l < "$TEMP/state/migrate.env")" -eq 7
grep -qx 'JAX_MIGRATE_DB_READINESS_ATTEMPTS=12' "$TEMP/state/migrate.env"
grep -qx 'JAX_MIGRATE_DB_READINESS_TIMEOUT_SECONDS=10' "$TEMP/state/migrate.env"

# The web credential is allowed to make the classic mutation it actually
# needs, but the append-only bootstrap table must deny UPDATE/DELETE before a
# trigger could decide anything.  Both predicates are empty on purpose: this
# proves the privilege boundary without changing the disposable fixture.
runtime_sql() { mariadb "--defaults-extra-file=$TEMP/state/secrets/$WEB.client.cnf" --batch --skip-column-names "$DB" "$@"; }
printf '[client]\nuser=%s\npassword=%s\nhost=127.0.0.1\nport=%s\n' "$WEB" "$(head -n1 "$TEMP/state/secrets/$WEB.password")" "$PORT" > "$TEMP/state/secrets/$WEB.client.cnf"
chmod 600 "$TEMP/state/secrets/$WEB.client.cnf"
runtime_sql -e 'SELECT 1' | grep -qx 1
runtime_sql -e "UPDATE axioma_config SET config_value=config_value WHERE 1=0"
if runtime_sql -e "UPDATE office_bootstrap_state SET completed_at=completed_at WHERE ceremony_key='__acl_disposable_probe__'"; then
  echo 'runtime UPDATE on protected bootstrap state was accepted' >&2; exit 1
fi
if runtime_sql -e "DELETE FROM office_bootstrap_state WHERE ceremony_key='__acl_disposable_probe__'"; then
  echo 'runtime DELETE on protected bootstrap state was accepted' >&2; exit 1
fi

# Simulate an interruption after the first narrow table GRANT but before the
# legacy schema UPDATE/DELETE revoke.  The next invocation may resume that
# exact manifest prefix; an arbitrary extra grant remains fail-closed below.
printf "REVOKE ALL PRIVILEGES, GRANT OPTION FROM '%s'@'%s'; GRANT SELECT, INSERT, UPDATE, DELETE ON \`%s\`.* TO '%s'@'%s'; GRANT UPDATE ON \`%s\`.\`axioma_config\` TO '%s'@'%s';\n" "$WEB" "$HOST" "$DB" "$WEB" "$HOST" "$DB" "$WEB" "$HOST" | admin >/dev/null
run_helper
runtime_sql -e "UPDATE axioma_config SET config_value=config_value WHERE 1=0"

# Every durable cut-point for a newly created principal is resumable and
# bounded: CREATE USER (USAGE), schema grant, and the first table grant.  The
# migration principal has only CREATE USER and its one schema grant cut-point.
reset_new_runtime() {
  printf "REVOKE ALL PRIVILEGES, GRANT OPTION FROM '%s'@'%s';\n" "$WEB" "$HOST" | admin >/dev/null
}
reset_new_runtime
run_helper
reset_new_runtime
printf "GRANT SELECT ON \`%s\`.* TO '%s'@'%s';\n" "$DB" "$WEB" "$HOST" | admin >/dev/null
run_helper
reset_new_runtime
printf "GRANT SELECT ON \`%s\`.* TO '%s'@'%s'; GRANT INSERT, UPDATE ON \`%s\`.\`axioma_config\` TO '%s'@'%s';\n" "$DB" "$WEB" "$HOST" "$DB" "$WEB" "$HOST" | admin >/dev/null
run_helper
printf "REVOKE ALL PRIVILEGES, GRANT OPTION FROM '%s'@'%s';\n" "$MIG" "$HOST" | admin >/dev/null
run_helper

chmod 644 "$TEMP/state/secrets/$WEB.password"
if run_helper; then echo 'unsafe secret mode was accepted' >&2; exit 1; fi
chmod 600 "$TEMP/state/secrets/$WEB.password"
cp "$TEMP/state/secrets/$MIG.password" "$TEMP/migrate-password.saved"
rm "$TEMP/state/secrets/$MIG.password"
if run_helper; then echo 'existing principal without secret was accepted' >&2; exit 1; fi
mv "$TEMP/migrate-password.saved" "$TEMP/state/secrets/$MIG.password"
chmod 600 "$TEMP/state/secrets/$MIG.password"

printf "GRANT SELECT ON mysql.* TO '%s'@'%s';\n" "$WEB" "$HOST" | admin >/dev/null
if run_helper; then echo 'extra grant was accepted' >&2; exit 1; fi
echo 'disposable provisioning fixture passed'
