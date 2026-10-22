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
[[ $(id -u) == 0 ]] || { echo 'run this fixture with sudo -n only' >&2; exit 2; }
docker inspect "$CONTAINER" >/dev/null
docker port "$CONTAINER" 3306/tcp | grep -qx "127.0.0.1:$PORT" || { echo 'fixture DB allowlist mismatch' >&2; exit 2; }

TEMP=$(mktemp -d /tmp/invitation-worker-provision.XXXXXX)
USER_NAME=officecvinvite_$(openssl rand -hex 6)
HOST=
OWNED=0
JAX_DIR_BEFORE=$(stat -c '%U:%G:%a' /etc/jax)
RELEASE=/opt/axioma-office-invitation-worker-fixture-$USER_NAME
SUPPLY=/opt/.axioma-office-invitation-worker-supply-$USER_NAME
admin() { docker exec -i "$CONTAINER" sh -ec 'MYSQL_PWD="$(printenv MARIADB_ROOT_PASSWORD)" exec mariadb -uroot --batch --skip-column-names'; }
cleanup() {
  if [[ $OWNED == 1 && -n $HOST ]]; then
    printf "DROP USER IF EXISTS '%s'@'%s';\n" "$USER_NAME" "$HOST" | admin >/dev/null 2>&1 || true
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
"$RELEASE_INSTALLER" --origin "$REPO" --revision "$REVISION" --release "$RELEASE" --python "$FAKE_PYTHON" --wheelhouse "$WHEELHOUSE" --lock "$LOCK" --build-user nobody
test "$(stat -c '%U:%G:%a' "$RELEASE")" = root:root:755
grep -Eq "^\{\"schema\":\"axioma-office-invitation-worker-release/v1\",\"source_revision\":\"$REVISION\",\"lock_sha256\":\"[0-9a-f]{64}\",\"wheelhouse_manifest_sha256\":\"[0-9a-f]{64}\"\}$" "$RELEASE/release-manifest.json"
test "$(readlink "$RELEASE/backend/.venv/lib64")" = lib
echo 'disposable invitation worker provisioning fixture passed'
