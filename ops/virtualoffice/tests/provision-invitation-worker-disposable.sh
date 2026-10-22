#!/usr/bin/env bash
# Root-only, allowlisted integration proof for the invitation worker principal.
set -euo pipefail
umask 077

CONTAINER=cv-office-explain-db-20261022
PORT=33994
DB=jax_memory_test_ac1d2e12
ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
HELPER="$ROOT_DIR/ops/virtualoffice/provision-invitation-worker.sh"
[[ $(id -u) == 0 ]] || { echo 'run this fixture with sudo -n only' >&2; exit 2; }
docker inspect "$CONTAINER" >/dev/null
docker port "$CONTAINER" 3306/tcp | grep -qx "127.0.0.1:$PORT" || { echo 'fixture DB allowlist mismatch' >&2; exit 2; }

TEMP=$(mktemp -d /tmp/invitation-worker-provision.XXXXXX)
USER_NAME=officecvinvite_$(openssl rand -hex 6)
HOST=
OWNED=0
JAX_DIR_BEFORE=$(stat -c '%U:%G:%a' /etc/jax)
admin() { docker exec -i "$CONTAINER" sh -ec 'MYSQL_PWD="$(printenv MARIADB_ROOT_PASSWORD)" exec mariadb -uroot --batch --skip-column-names'; }
cleanup() {
  if [[ $OWNED == 1 && -n $HOST ]]; then
    printf "DROP USER IF EXISTS '%s'@'%s';\n" "$USER_NAME" "$HOST" | admin >/dev/null 2>&1 || true
  fi
  rm -rf "$TEMP"
}
trap cleanup EXIT

ROOT_CNF="$TEMP/root.cnf"
root_password=$(docker inspect "$CONTAINER" --format '{{range .Config.Env}}{{println .}}{{end}}' | sed -n 's/^MARIADB_ROOT_PASSWORD=//p')
printf '[client]\nuser=root\npassword=%s\nhost=127.0.0.1\nport=%s\n' "$root_password" "$PORT" > "$ROOT_CNF"
HOST=$(mariadb "--defaults-extra-file=$ROOT_CNF" --batch --skip-column-names -e 'SELECT SUBSTRING_INDEX(USER(), "@", -1)')
case "$HOST" in ''|%|*%*|*_* ) echo 'unsafe measured fixture host' >&2; exit 2;; esac
[[ $(printf "SELECT COUNT(*) FROM mysql.user WHERE User='%s' AND Host='%s';\n" "$USER_NAME" "$HOST" | admin) == 0 ]] || { echo 'fixture principal already exists' >&2; exit 2; }
OWNED=1

ENV_FILE="$TEMP/runtime.env"
cat > "$ENV_FILE" <<EOF
JAX_DB_HOST=127.0.0.1
JAX_DB_PORT=$PORT
JAX_DB_NAME=$DB
JAX_DB_GRANTEE_HOST=$HOST
FERNET_KEY=fixture_fernet_key
OFFICE_INVITATION_LIFETIME_SECONDS=3600
OFFICE_INVITATION_CLAIM_TIMEOUT_SECONDS=60
OFFICE_INVITATION_STALE_CLAIM_BATCH_LIMIT=10
OFFICE_INVITATION_EXPIRY_SWEEP_BATCH_LIMIT=10
OFFICE_INVITATION_DELIVERY_BATCH_LIMIT=10
OFFICE_INVITATION_PUBLIC_BASE_URL=https://virtualoffice.example.test
EOF
TRANSPORT="$TEMP/admin-transport"
printf '#!/bin/sh\nexec docker exec -i %s sh -ec '\''MYSQL_PWD="$(printenv MARIADB_ROOT_PASSWORD)" exec mariadb -uroot --batch --skip-column-names'\''\n' "$CONTAINER" > "$TRANSPORT"
chmod 700 "$TRANSPORT"

run_helper() { "$HELPER" --env "$ENV_FILE" --state-root "$TEMP/state" --admin-transport "$TRANSPORT" --skip-os-identities --worker-db-user "$USER_NAME"; }
run_helper
run_helper
[[ $(stat -c '%U:%G:%a' /etc/jax) == "$JAX_DIR_BEFORE" ]] || { echo '/etc/jax ownership or mode was changed' >&2; exit 1; }
test "$(stat -c '%U:%G:%a' "$TEMP/state/invitation-worker.env")" = root:root:600
test "$(stat -c '%U:%G:%a' "$TEMP/state/secrets/$USER_NAME.password")" = root:root:600
grep -qx "JAX_DB_USER=$USER_NAME" "$TEMP/state/invitation-worker.env"

password=$(<"$TEMP/state/secrets/$USER_NAME.password")
CLIENT="$TEMP/worker.cnf"
printf '[client]\nuser=%s\npassword=%s\nhost=127.0.0.1\nport=%s\n' "$USER_NAME" "$password" "$PORT" > "$CLIENT"
chmod 600 "$CLIENT"
mariadb "--defaults-extra-file=$CLIENT" "$DB" -e 'SELECT 1' | tail -n1 | grep -qx 1
grants=$(printf "SHOW GRANTS FOR '%s'@'%s';\n" "$USER_NAME" "$HOST" | admin | sed -E "s/ IDENTIFIED BY (PASSWORD )?'[^']*'//g" | tr '\140' '\047')
for grant in \
  "GRANT USAGE ON *.* TO '$USER_NAME'@'$HOST'" \
  "GRANT SELECT ON '$DB'.'axioma_config' TO '$USER_NAME'@'$HOST'" \
  "GRANT SELECT, UPDATE ON '$DB'.'office_invitation' TO '$USER_NAME'@'$HOST'" \
  "GRANT SELECT ON '$DB'.'office_invitation_issuance' TO '$USER_NAME'@'$HOST'" \
  "GRANT SELECT, INSERT ON '$DB'.'office_invitation_event' TO '$USER_NAME'@'$HOST'" \
  "GRANT SELECT, UPDATE ON '$DB'.'office_invitation_outbox' TO '$USER_NAME'@'$HOST'"; do
  grep -Fqx "$grant" <<< "$grants"
done
[[ $(grep -c '^GRANT ' <<< "$grants") == 6 ]]
if mariadb "--defaults-extra-file=$CLIENT" "$DB" -e 'INSERT INTO office_invitation (id) VALUES (0)' >/dev/null 2>&1; then
  echo 'worker was able to issue an invitation' >&2; exit 1
fi
echo 'disposable invitation worker provisioning fixture passed'
