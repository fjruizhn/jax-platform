#!/usr/bin/env bash
# Root-only, allowlisted integration proof for the invitation worker principal.
set -euo pipefail
umask 077

CONTAINER=cv-office-explain-db-20261022
PORT=33994
DB=jax_memory_test_ac1d2e12
ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
HELPER="$ROOT_DIR/ops/virtualoffice/provision-invitation-worker.sh"
RELEASE_INSTALLER="$ROOT_DIR/ops/virtualoffice/install-invitation-worker-release.sh"
ROUTINE_INSTALLER="$ROOT_DIR/ops/virtualoffice/install-invitation-delivery-routines.sh"
ROUTINE_ATTESTOR="$ROOT_DIR/ops/virtualoffice/attest-invitation-delivery-routines.sh"
[[ $(id -u) == 0 ]] || { echo 'run this fixture with sudo -n only' >&2; exit 2; }
docker inspect "$CONTAINER" >/dev/null
docker port "$CONTAINER" 3306/tcp | grep -qx "127.0.0.1:$PORT" || { echo 'fixture DB allowlist mismatch' >&2; exit 2; }

TEMP=$(mktemp -d /tmp/invitation-worker-provision.XXXXXX)
USER_NAME=officecvinvite_$(openssl rand -hex 6)
NO_SCHEMA_USER=officecvnoschema_$(openssl rand -hex 6)
HOST=
OLD_HOST=old.invalid
EMPTY_DB=jax_invitation_empty_$(openssl rand -hex 6)
EMPTY_DB_CREATED=0
OWNED=0
OLD_PRINCIPAL=0
FIXTURE_PROCEDURE=0
JAX_DIR_BEFORE=$(stat -c '%U:%G:%a' /etc/jax)
RELEASE=/opt/axioma-office-invitation-worker-fixture-$USER_NAME
SUPPLY=/opt/.axioma-office-invitation-worker-supply-$USER_NAME
admin() { docker exec -i "$CONTAINER" sh -ec 'MYSQL_PWD="$(printenv MARIADB_ROOT_PASSWORD)" exec mariadb -uroot --batch --skip-column-names'; }
cleanup() {
  if [[ $EMPTY_DB_CREATED == 1 ]]; then
    printf "DROP DATABASE IF EXISTS \`%s\`;\n" "$EMPTY_DB" | admin >/dev/null 2>&1 || true
  fi
  if [[ $FIXTURE_PROCEDURE == 1 ]]; then
    printf "DROP PROCEDURE IF EXISTS \`%s\`.\`office_invitation_fixture_forbidden_v1\`;\n" "$DB" | admin >/dev/null 2>&1 || true
  fi
  if [[ $OWNED == 1 && -n $HOST ]]; then
    printf "DROP USER IF EXISTS '%s'@'%s';\n" "$USER_NAME" "$HOST" | admin >/dev/null 2>&1 || true
  fi
  if [[ $OLD_PRINCIPAL == 1 ]]; then
    printf "DROP USER IF EXISTS '%s'@'%s';\n" "$USER_NAME" "$OLD_HOST" | admin >/dev/null 2>&1 || true
  fi
  if [[ -n $HOST ]]; then
    printf "DROP USER IF EXISTS '%s'@'%s';\n" "$NO_SCHEMA_USER" "$HOST" | admin >/dev/null 2>&1 || true
  fi
  rm -rf "$RELEASE"
  rm -rf "$SUPPLY"
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

SQL_FILE="$TEMP/office-invitation-delivery-v1.sql"
cp "$ROOT_DIR/ops/virtualoffice/office-invitation-delivery-v1.sql" "$SQL_FILE"
chown root:root "$SQL_FILE"; chmod 644 "$SQL_FILE"
INSTALLED_ATTESTOR="$TEMP/installed/axioma-office-invitation-delivery-attest"
mkdir -p "$TEMP/installed"
cp "$ROUTINE_ATTESTOR" "$INSTALLED_ATTESTOR"
chown root:root "$INSTALLED_ATTESTOR"; chmod 750 "$INSTALLED_ATTESTOR"
ENV_FILE="$TEMP/runtime.env"
cat > "$ENV_FILE" <<EOF
JAX_DB_HOST=127.0.0.1
JAX_DB_PORT=$PORT
JAX_DB_NAME=$DB
FERNET_KEY=fixture_fernet_key
JAX_ENVIRONMENT=production
OFFICE_PUBLIC_ORIGIN=https://virtualoffice.example.test
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

printf "CREATE DATABASE \`%s\`;\n" "$EMPTY_DB" | admin
EMPTY_DB_CREATED=1
NO_SCHEMA_ENV="$TEMP/no-office-schema.env"
cat > "$NO_SCHEMA_ENV" <<EOF
JAX_DB_HOST=127.0.0.1
JAX_DB_PORT=$PORT
JAX_DB_NAME=$EMPTY_DB
FERNET_KEY=fixture_fernet_key
OFFICE_INVITATION_LIFETIME_SECONDS=3600
OFFICE_INVITATION_CLAIM_TIMEOUT_SECONDS=60
OFFICE_INVITATION_STALE_CLAIM_BATCH_LIMIT=10
OFFICE_INVITATION_EXPIRY_SWEEP_BATCH_LIMIT=10
OFFICE_INVITATION_DELIVERY_BATCH_LIMIT=10
OFFICE_INVITATION_PUBLIC_BASE_URL=https://virtualoffice.example.test
EOF
NO_SCHEMA_STATE="$TEMP/no-office-schema-state"
if "$HELPER" --env "$NO_SCHEMA_ENV" --state-root "$NO_SCHEMA_STATE" --admin-transport "$TRANSPORT" --skip-os-identities --worker-db-user "$NO_SCHEMA_USER" --measured-grantee-host "$HOST" >/dev/null 2>&1; then
  echo 'provisioner accepted a database without the Office invitation schema' >&2; exit 1
fi
[[ ! -e "$NO_SCHEMA_STATE" ]] || { echo 'missing Office schema created worker state or secret' >&2; exit 1; }
[[ $(printf "SELECT COUNT(*) FROM mysql.user WHERE User='%s' AND Host='%s';\n" "$NO_SCHEMA_USER" "$HOST" | admin) == 0 ]] || { echo 'missing Office schema created a worker principal' >&2; exit 1; }
printf "DROP DATABASE \`%s\`;\n" "$EMPTY_DB" | admin
EMPTY_DB_CREATED=0

# Start the installation proof at the actual fresh boundary: no delivery
# routines and no procedure grants survive the DROP. The fixture database is
# a disposable allowlisted container and the component is rebuilt below.
printf "USE \`%s\`; DROP PROCEDURE IF EXISTS office_invitation_claim_next_v1; DROP PROCEDURE IF EXISTS office_invitation_expire_due_v1; DROP PROCEDURE IF EXISTS office_invitation_settle_delivery_v1;\n" "$DB" | admin

run_helper() { "$HELPER" --env "$ENV_FILE" --state-root "$TEMP/state" --admin-transport "$TRANSPORT" --skip-os-identities --worker-db-user "$USER_NAME" --measured-grantee-host "$HOST"; }
if "$HELPER" --env "$ENV_FILE" --state-root "$TEMP/state" --admin-transport "$TRANSPORT" --skip-os-identities --worker-db-user "$USER_NAME" >/dev/null 2>&1; then
  echo 'provisioner accepted a missing measured grantee host' >&2; exit 1
fi
if "$HELPER" --env "$ENV_FILE" --state-root "$TEMP/state" --admin-transport "$TRANSPORT" --skip-os-identities --worker-db-user "$USER_NAME" --measured-grantee-host '%' >/dev/null 2>&1; then
  echo 'provisioner accepted a wildcard measured grantee host' >&2; exit 1
fi
if "$HELPER" --env "$ENV_FILE" --state-root "$TEMP/state" --admin-transport "$TRANSPORT" --skip-os-identities --worker-db-user "$USER_NAME" --measured-grantee-host 'host$' >/dev/null 2>&1; then
  echo 'provisioner accepted an invalid measured grantee host' >&2; exit 1
fi
[[ $(printf "SELECT COUNT(*) FROM mysql.user WHERE User='%s' AND Host='%s';\n" "$USER_NAME" "$OLD_HOST" | admin) == 0 ]] || { echo 'fixture stale worker principal already exists' >&2; exit 2; }
printf "CREATE USER '%s'@'%s' IDENTIFIED BY 'fixture-stale-secret'; GRANT SELECT ON \`%s\`.\`axioma_config\` TO '%s'@'%s';\n" "$USER_NAME" "$OLD_HOST" "$DB" "$USER_NAME" "$OLD_HOST" | admin
OLD_PRINCIPAL=1
old_grants_before=$(printf "SHOW GRANTS FOR '%s'@'%s';\n" "$USER_NAME" "$OLD_HOST" | admin)
if run_helper >/dev/null 2>&1; then
  echo 'provisioner accepted a stale worker principal at a different host' >&2; exit 1
fi
old_grants_after=$(printf "SHOW GRANTS FOR '%s'@'%s';\n" "$USER_NAME" "$OLD_HOST" | admin)
[[ "$old_grants_after" == "$old_grants_before" ]] || { echo 'provisioner altered the stale worker principal' >&2; exit 1; }
[[ $(printf "SELECT COUNT(*) FROM mysql.user WHERE User='%s' AND Host='%s';\n" "$USER_NAME" "$HOST" | admin) == 0 ]] || { echo 'provisioner created a worker principal despite stale host' >&2; exit 1; }
printf "DROP USER '%s'@'%s';\n" "$USER_NAME" "$OLD_HOST" | admin
OLD_PRINCIPAL=0
ln -s "$ENV_FILE" "$TEMP/runtime-link.env"
if "$HELPER" --env "$TEMP/runtime-link.env" --state-root "$TEMP/state" --admin-transport "$TRANSPORT" --skip-os-identities --worker-db-user "$USER_NAME" --measured-grantee-host "$HOST" >/dev/null 2>&1; then
  echo 'symlinked runtime env was accepted' >&2; exit 1
fi
chmod 666 "$ENV_FILE"
if run_helper >/dev/null 2>&1; then echo 'writable runtime env was accepted' >&2; exit 1; fi
chmod 600 "$ENV_FILE"
run_helper
ROUTINE_COUNT_BEFORE=$(printf "SELECT COUNT(*) FROM information_schema.ROUTINES WHERE ROUTINE_SCHEMA='%s' AND ROUTINE_TYPE='PROCEDURE' AND ROUTINE_NAME IN ('office_invitation_claim_next_v1','office_invitation_expire_due_v1','office_invitation_settle_delivery_v1');\n" "$DB" | admin)
[[ $ROUTINE_COUNT_BEFORE == 0 ]] || { echo 'fresh routine component proof did not start empty' >&2; exit 1; }
# Three same-named but structurally altered procedures must fail before either
# locked owner or worker gains durable EXECUTE authority.
printf "USE \`%s\`; CREATE PROCEDURE office_invitation_claim_next_v1() SELECT 1; CREATE PROCEDURE office_invitation_expire_due_v1() SELECT 1; CREATE PROCEDURE office_invitation_settle_delivery_v1() SELECT 1;\n" "$DB" | admin
if "$ROUTINE_INSTALLER" --db "$DB" --admin-transport "$TRANSPORT" --worker-db-user "$USER_NAME" --worker-host "$HOST" --sql-file "$SQL_FILE" --attestor "$INSTALLED_ATTESTOR" >/dev/null 2>&1; then
  echo 'altered complete routine component was accepted' >&2; exit 1
fi
for principal in "'jaxoffice_invitation_owner'@'localhost'" "'$USER_NAME'@'$HOST'"; do
  if printf "SHOW GRANTS FOR %s;\n" "$principal" | admin | grep -q 'EXECUTE ON PROCEDURE'; then
    echo 'rejected altered component received execute authority' >&2; exit 1
  fi
done
printf "USE \`%s\`; DROP PROCEDURE office_invitation_claim_next_v1; DROP PROCEDURE office_invitation_expire_due_v1; DROP PROCEDURE office_invitation_settle_delivery_v1;\n" "$DB" | admin
BAD_SQL="$TEMP/office-invitation-delivery-v1-tampered.sql"
cp "$SQL_FILE" "$BAD_SQL"
printf '\n' >> "$BAD_SQL"
chown root:root "$BAD_SQL"; chmod 644 "$BAD_SQL"
if "$ROUTINE_INSTALLER" --db "$DB" --admin-transport "$TRANSPORT" --worker-db-user "$USER_NAME" --worker-host "$HOST" --sql-file "$BAD_SQL" --attestor "$INSTALLED_ATTESTOR" >/dev/null 2>&1; then
  echo 'tampered root-owned routine SQL was accepted' >&2; exit 1
fi
[[ $(printf "SELECT COUNT(*) FROM information_schema.ROUTINES WHERE ROUTINE_SCHEMA='%s' AND ROUTINE_TYPE='PROCEDURE' AND ROUTINE_NAME IN ('office_invitation_claim_next_v1','office_invitation_expire_due_v1','office_invitation_settle_delivery_v1');\n" "$DB" | admin) == "$ROUTINE_COUNT_BEFORE" ]] || {
  echo 'rejected SQL changed the routine component' >&2; exit 1;
}
"$ROUTINE_INSTALLER" --db "$DB" --admin-transport "$TRANSPORT" --worker-db-user "$USER_NAME" --worker-host "$HOST" --sql-file "$SQL_FILE" --attestor "$INSTALLED_ATTESTOR"
run_helper
[[ $(stat -c '%U:%G:%a' /etc/jax) == "$JAX_DIR_BEFORE" ]] || { echo '/etc/jax ownership or mode was changed' >&2; exit 1; }
test "$(stat -c '%U:%G:%a' "$TEMP/state/invitation-worker.env")" = root:root:600
ln -s "$TEMP/state/invitation-worker.env" "$TEMP/invitation-worker-link.env"
if "$ROOT_DIR/ops/virtualoffice/invitation-worker-readiness.sh" "$TEMP/invitation-worker-link.env" >/dev/null 2>&1; then
  echo 'symlinked worker env was accepted' >&2; exit 1
fi
test "$(stat -c '%U:%G:%a' "$TEMP/state/secrets/$USER_NAME.password")" = root:root:600
grep -qx "JAX_DB_USER=$USER_NAME" "$TEMP/state/invitation-worker.env"
grep -qx 'JAX_ENVIRONMENT=production' "$TEMP/state/invitation-worker.env"
grep -qx 'OFFICE_PUBLIC_ORIGIN=https://virtualoffice.example.test' "$TEMP/state/invitation-worker.env"
set -a
. "$TEMP/state/invitation-worker.env"
set +a
"$ROOT_DIR/ops/virtualoffice/invitation-worker-readiness.sh" "$TEMP/state/invitation-worker.env"
if OFFICE_PUBLIC_ORIGIN=https://evil.example.test "$ROOT_DIR/ops/virtualoffice/invitation-worker-readiness.sh" "$TEMP/state/invitation-worker.env" >/dev/null 2>&1; then
  echo 'worker readiness accepted a mismatched public origin' >&2; exit 1
fi
# The post-install effective gate must supply its own root-owned environment;
# an operator's clean shell has none of the worker values to inherit.
env -i PATH=/usr/bin:/bin /usr/bin/systemd-run --wait --pipe --collect --property=User=root \
  --property=EnvironmentFile="$TEMP/state/invitation-worker.env" \
  "$ROOT_DIR/ops/virtualoffice/invitation-worker-readiness.sh" "$TEMP/state/invitation-worker.env"

password=$(<"$TEMP/state/secrets/$USER_NAME.password")
CLIENT="$TEMP/worker.cnf"
printf '[client]\nuser=%s\npassword=%s\nhost=127.0.0.1\nport=%s\n' "$USER_NAME" "$password" "$PORT" > "$CLIENT"
chmod 600 "$CLIENT"
mariadb "--defaults-extra-file=$CLIENT" "$DB" -e 'SELECT 1' | tail -n1 | grep -qx 1
grants=$(printf "SHOW GRANTS FOR '%s'@'%s';\n" "$USER_NAME" "$HOST" | admin | sed -E "s/ IDENTIFIED BY (PASSWORD )?'[^']*'//g" | tr '\140' '\047')
for grant in \
  "GRANT USAGE ON *.* TO '$USER_NAME'@'$HOST'" \
  "GRANT SELECT ON '$DB'.'axioma_config' TO '$USER_NAME'@'$HOST'" \
  "GRANT SELECT ('attempt_id', 'outbox_id', 'invitation_id', 'state', 'created_at', 'claimed_at') ON '$DB'.'office_invitation_outbox' TO '$USER_NAME'@'$HOST'" \
  "GRANT EXECUTE ON PROCEDURE '$DB'.'office_invitation_claim_next_v1' TO '$USER_NAME'@'$HOST'" \
  "GRANT EXECUTE ON PROCEDURE '$DB'.'office_invitation_expire_due_v1' TO '$USER_NAME'@'$HOST'" \
  "GRANT EXECUTE ON PROCEDURE '$DB'.'office_invitation_settle_delivery_v1' TO '$USER_NAME'@'$HOST'"; do
  grep -Fqx "$grant" <<< "$grants" || { printf "missing expected grant: %s\n" "$grant" >&2; printf "%s\n" "$grants" >&2; exit 1; }
done
[[ $(grep -c '^GRANT ' <<< "$grants") == 6 ]]
# An extra definer procedure grant is an authority widening. Attestation must
# reject it, and idempotent provisioning must restore exactly the contract.
printf "USE \`%s\`; CREATE PROCEDURE \`office_invitation_fixture_forbidden_v1\`() SELECT 1; GRANT EXECUTE ON PROCEDURE \`%s\`.\`office_invitation_fixture_forbidden_v1\` TO 'jaxoffice_invitation_owner'@'localhost';\n" "$DB" "$DB" | admin
FIXTURE_PROCEDURE=1
if JAX_INVITATION_WORKER_DB_USER="$USER_NAME" "$INSTALLED_ATTESTOR" --db "$DB" --admin-transport "$TRANSPORT" --worker-host "$HOST" >/dev/null 2>&1; then
  echo 'attestation accepted widened definer routine authority' >&2; exit 1
fi
run_helper
JAX_INVITATION_WORKER_DB_USER="$USER_NAME" "$INSTALLED_ATTESTOR" --db "$DB" --admin-transport "$TRANSPORT" --worker-host "$HOST"
printf "DROP PROCEDURE \`%s\`.\`office_invitation_fixture_forbidden_v1\`;\n" "$DB" | admin
FIXTURE_PROCEDURE=0
"$ROUTINE_INSTALLER" --db "$DB" --admin-transport "$TRANSPORT" --worker-db-user "$USER_NAME" --worker-host "$HOST" --sql-file "$SQL_FILE" --attestor "$INSTALLED_ATTESTOR"
# Valid lifecycle SQL must fail at privilege resolution (1142), not merely
# because a made-up column was used.
if mariadb "--defaults-extra-file=$CLIENT" "$DB" -e "UPDATE office_invitation SET status='EXPIRED' WHERE invitation_id='00000000-0000-4000-8000-000000000000'" 2>"$TEMP/denied"; then
  echo 'worker was able to mutate invitation lifecycle' >&2; exit 1
fi
grep -q '1142' "$TEMP/denied" || { cat "$TEMP/denied" >&2; exit 1; }


# Exercise the installer without downloading packages: the fake interpreter
# reproduces CPython's one permitted lib64 -> lib venv link.
REPO="$TEMP/repo"
mkdir -p "$REPO/backend/virtual_office"
mkdir -p "$REPO/ops/virtualoffice"
printf 'fastapi>=0\n' > "$REPO/backend/requirements.txt"
printf 'fastapi==1.0\n' > "$REPO/backend/constraints.txt"
printf '__all__ = ()\n' > "$REPO/backend/virtual_office/__init__.py"
cp "$ROOT_DIR/ops/virtualoffice/worker-requirements.lock" "$REPO/ops/virtualoffice/worker-requirements.lock"
git -C "$REPO" init -q
git -C "$REPO" add .
git -C "$REPO" -c user.name=fixture -c user.email=fixture@example.test commit -qm fixture
REVISION=$(git -C "$REPO" rev-parse HEAD)
FAKE_PYTHON="$TEMP/fake-python"
WHEELHOUSE="$SUPPLY/wheelhouse"
LOCK="$SUPPLY/worker-requirements.lock"
mkdir -p "$SUPPLY"
chmod 755 "$SUPPLY"
mkdir -p "$WHEELHOUSE"
chmod 755 "$WHEELHOUSE"
cp "$ROOT_DIR/ops/virtualoffice/worker-requirements.lock" "$LOCK"
chmod 644 "$LOCK"
cat > "$FAKE_PYTHON" <<'EOF'
#!/bin/sh
set -eu
if [ "$1" = -m ] && [ "$2" = venv ]; then
  dest=$4
  mkdir -p "$dest/bin" "$dest/lib"
  ln -s lib "$dest/lib64"
  printf '#!/bin/sh\nexit 0\n' > "$dest/bin/pip"
  cp "$dest/bin/pip" "$dest/bin/python"
  chmod 755 "$dest/bin/pip" "$dest/bin/python"
  exit 0
fi
exit 0
EOF
chmod 755 "$FAKE_PYTHON"
ln -s "$LOCK" "$WHEELHOUSE/unsafe-link"
if "$RELEASE_INSTALLER" --origin "$REPO" --revision "$REVISION" --release "$RELEASE" --python "$FAKE_PYTHON" --wheelhouse "$WHEELHOUSE" --lock "$LOCK" --build-user nobody >/dev/null 2>&1; then
  echo 'symlinked wheelhouse content was accepted' >&2; exit 1
fi
rm "$WHEELHOUSE/unsafe-link"
printf 'unsafe\n' > "$WHEELHOUSE/writable.whl"
chmod 666 "$WHEELHOUSE/writable.whl"
if "$RELEASE_INSTALLER" --origin "$REPO" --revision "$REVISION" --release "$RELEASE" --python "$FAKE_PYTHON" --wheelhouse "$WHEELHOUSE" --lock "$LOCK" --build-user nobody >/dev/null 2>&1; then
  echo 'writable wheelhouse content was accepted' >&2; exit 1
fi
rm "$WHEELHOUSE/writable.whl"
"$RELEASE_INSTALLER" --origin "$REPO" --revision "$REVISION" --release "$RELEASE" --python "$FAKE_PYTHON" --wheelhouse "$WHEELHOUSE" --lock "$LOCK" --build-user nobody
test "$(stat -c '%U:%G:%a' "$RELEASE")" = root:root:755
grep -Eq "^\{\"schema\":\"axioma-office-invitation-worker-release/v1\",\"source_revision\":\"$REVISION\",\"lock_sha256\":\"[0-9a-f]{64}\",\"wheelhouse_manifest_sha256\":\"[0-9a-f]{64}\"\}$" "$RELEASE/release-manifest.json"
test "$(readlink "$RELEASE/backend/.venv/lib64")" = lib
echo 'disposable invitation worker provisioning fixture passed'
