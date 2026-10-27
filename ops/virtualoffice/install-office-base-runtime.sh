#!/usr/bin/env bash
# Install only the dedicated Office database or API units from an exact SHA.
set -euo pipefail
umask 077
source_repo=
revision=
phase=
server_id=
while [[ $# -gt 0 ]]; do
  case "$1" in
    --source) source_repo=${2:?}; shift ;;
    --revision) revision=${2:?}; shift ;;
    --phase) phase=${2:?}; shift ;;
    --server-id) server_id=${2:?}; shift ;;
    -h|--help) echo 'usage: install-office-base-runtime.sh --source REPOSITORY --revision FULL_SHA --phase database|migration|services [--server-id DECIMAL]'; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done
[[ $(id -u) == 0 && -d $source_repo && $revision =~ ^[0-9a-f]{40}$ && $phase =~ ^(database|migration|services)$ ]] || {
  echo 'root, repository, full revision, and phase required' >&2; exit 2;
}
[[ $(GIT_NO_REPLACE_OBJECTS=1 git -C "$source_repo" rev-parse "$revision^{commit}") == "$revision" ]] || {
  echo 'reviewed revision is unavailable' >&2; exit 1;
}
stage=$(mktemp -d /var/tmp/axioma-office-base.XXXXXXXX)
trap 'rm -rf "$stage"' EXIT
GIT_NO_REPLACE_OBJECTS=1 git -C "$source_repo" archive --format=tar "$revision" \
  ops/migration/provision-db-runtime.sh \
  ops/migration/runtime-table-privileges-v1.txt \
  ops/migration/runtime-table-privileges-v2.txt \
  ops/migration/pair_artifact_readiness.py \
  ops/virtualoffice/mariadb/docker-compose.yml \
  ops/virtualoffice/office-pair-exec.py \
  ops/virtualoffice/install-office-pair-release.py \
  ops/virtualoffice/office-dedicated-mariadb-admin-transport.sh \
  ops/virtualoffice/provision-office-os-users.sh \
  ops/virtualoffice/systemd/axioma-office-database.service \
  ops/virtualoffice/systemd/axioma-office-migrate.service \
  ops/virtualoffice/systemd/axioma-office-backend.service \
  ops/virtualoffice/office-base-artifact-readiness.sh | tar -x -C "$stage"
base=$stage/ops/virtualoffice
install -d -o root -g root -m 0755 /usr/local/libexec /etc/systemd/system
if [[ $phase == database ]]; then
  [[ $server_id =~ ^[1-9][0-9]*$ ]] || { echo 'unique decimal Office server ID required' >&2; exit 2; }
  for path in /etc/axioma-office /etc/axioma-office/mariadb /var/lib/axioma-office-mariadb /var/lib/axioma-office-mariadb/secrets; do
    [[ ! -L $path ]] || { echo 'Office storage symlink is forbidden' >&2; exit 1; }
  done
  if systemctl is-active --quiet axioma-office-database.service; then
    echo 'stop the dedicated database unit before replacing its files' >&2; exit 1
  fi
  if [[ $(docker inspect --format='{{.State.Running}}' axioma-office-mariadb 2>/dev/null || true) == true ]]; then
    echo 'stop the dedicated MariaDB container before replacing its files' >&2; exit 1
  fi
  # The unprivileged Office launcher reads the root-owned 0644 pair selector
  # in this directory.  Credentials remain individual 0600 files; the DB
  # configuration and data directories stay private.
  install -d -o root -g root -m 0755 /etc/axioma-office
  install -d -o root -g root -m 0700 /etc/axioma-office/mariadb
  install -d -o root -g root -m 0700 /var/lib/axioma-office-mariadb /var/lib/axioma-office-mariadb/secrets
  for path in /etc/axioma-office /etc/axioma-office/mariadb /var/lib/axioma-office-mariadb /var/lib/axioma-office-mariadb/secrets; do
    [[ $(stat -c '%U:%G' "$path") == root:root ]] || { echo 'Office storage owner is unsafe' >&2; exit 1; }
  done
  if [[ ! -e /var/lib/axioma-office-mariadb/secrets/root_password.txt ]]; then
    openssl rand -base64 48 > /var/lib/axioma-office-mariadb/secrets/root_password.txt
    chmod 0600 /var/lib/axioma-office-mariadb/secrets/root_password.txt
  fi
  [[ -f /var/lib/axioma-office-mariadb/secrets/root_password.txt &&
     ! -L /var/lib/axioma-office-mariadb/secrets/root_password.txt &&
     $(stat -c '%U:%G:%a' /var/lib/axioma-office-mariadb/secrets/root_password.txt) == root:root:600 ]] || {
    echo 'database root secret is unsafe' >&2; exit 1;
  }
  if [[ -e /etc/axioma-office/mariadb/compose.env ]]; then
    [[ -f /etc/axioma-office/mariadb/compose.env && ! -L /etc/axioma-office/mariadb/compose.env &&
       $(stat -c '%U:%G:%a' /etc/axioma-office/mariadb/compose.env) == root:root:600 &&
       $(cat /etc/axioma-office/mariadb/compose.env) == "OFFICE_DB_SERVER_ID=$server_id" ]] || {
      echo 'existing Office database identity differs or is unsafe' >&2; exit 1;
    }
  else
    printf 'OFFICE_DB_SERVER_ID=%s\n' "$server_id" > /etc/axioma-office/mariadb/compose.env
    chmod 0600 /etc/axioma-office/mariadb/compose.env
  fi
  install -o root -g root -m 0644 "$base/mariadb/docker-compose.yml" /etc/axioma-office/mariadb/compose.yml
  install -o root -g root -m 0750 "$base/office-dedicated-mariadb-admin-transport.sh" /usr/local/libexec/axioma-office-mariadb-admin
  install -d -o root -g root -m 0755 /opt/axioma-office-pair /opt/axioma-office-pair/releases /opt/axioma-office-pair/libexec
  if [[ ! -e /opt/axioma-office-pair/.runtime.lock ]]; then
    install -o root -g root -m 0644 /dev/null /opt/axioma-office-pair/.runtime.lock
  fi
  [[ -f /opt/axioma-office-pair/.runtime.lock && ! -L /opt/axioma-office-pair/.runtime.lock &&
     $(stat -c '%U:%G:%a' /opt/axioma-office-pair/.runtime.lock) == root:root:644 ]] || {
    echo 'Office pair runtime lock is unsafe' >&2; exit 1;
  }
  install -o root -g root -m 0644 "$stage/ops/migration/pair_artifact_readiness.py" /opt/axioma-office-pair/libexec/pair_artifact_readiness.py
  install -o root -g root -m 0644 "$base/office-pair-exec.py" /usr/local/libexec/axioma-office-pair-exec.py
  install -o root -g root -m 0750 "$base/install-office-pair-release.py" /usr/local/libexec/axioma-office-pair-install
  install -d -o root -g root -m 0755 /usr/local/libexec/axioma-office-db-runtime
  install -o root -g root -m 0750 "$stage/ops/migration/provision-db-runtime.sh" /usr/local/libexec/axioma-office-db-runtime/provision-db-runtime.sh
  install -o root -g root -m 0644 "$stage/ops/migration/runtime-table-privileges-v1.txt" /usr/local/libexec/axioma-office-db-runtime/runtime-table-privileges-v1.txt
  install -o root -g root -m 0644 "$stage/ops/migration/runtime-table-privileges-v2.txt" /usr/local/libexec/axioma-office-db-runtime/runtime-table-privileges-v2.txt
  install -o root -g root -m 0644 "$base/systemd/axioma-office-database.service" /etc/systemd/system/axioma-office-database.service
else
  env_names=(migrate)
  [[ $phase == migration ]] || env_names+=(runtime)
  for name in "${env_names[@]}"; do
    env_file=/etc/axioma-office/$name.env
    [[ -f $env_file && ! -L $env_file && $(stat -c '%U:%G:%a' "$env_file") == root:root:600 ]] || {
      echo "Office $name environment is absent or unsafe" >&2; exit 1;
    }
  done
  if [[ $phase == migration ]]; then
    if systemctl is-active --quiet axioma-office-migrate.service || systemctl is-active --quiet axioma-office-backend.service; then
      echo 'stop the Office API and migrator before replacing the migration unit' >&2; exit 1
    fi
    install -o root -g root -m 0750 "$base/provision-office-os-users.sh" /usr/local/libexec/axioma-office-provision-os-users
    /usr/local/libexec/axioma-office-provision-os-users
    install -o root -g root -m 0644 "$base/systemd/axioma-office-migrate.service" /etc/systemd/system/axioma-office-migrate.service
  else
    if systemctl is-active --quiet axioma-office-backend.service; then
      echo 'stop the Office API before replacing its unit' >&2; exit 1
    fi
    install -o root -g root -m 0644 "$base/systemd/axioma-office-backend.service" /etc/systemd/system/axioma-office-backend.service
  fi
fi
install -o root -g root -m 0750 "$base/office-base-artifact-readiness.sh" /usr/local/libexec/axioma-office-base-artifact-readiness
systemctl daemon-reload
/usr/local/libexec/axioma-office-base-artifact-readiness "$source_repo" "$revision" --phase "$phase"
