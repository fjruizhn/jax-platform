#!/usr/bin/env bash
# Verify installed dedicated Office files against the reviewed commit.
set -euo pipefail
source_repo=${1:?repository required}
revision=${2:?full revision required}
[[ ${3:-} == --phase && ${4:-} =~ ^(database|migration|services)$ && $revision =~ ^[0-9a-f]{40}$ ]] || exit 2
phase=$4
for path in /etc/axioma-office /etc/axioma-office/mariadb /var/lib/axioma-office-mariadb /var/lib/axioma-office-mariadb/secrets; do
  [[ -d $path && ! -L $path && $(stat -c '%U:%G' "$path") == root:root ]]
done
check_file() {
  local tracked=$1 installed=$2 owner_mode=$3
  [[ -f $installed && ! -L $installed && $(stat -c '%U:%G:%a' "$installed") == "$owner_mode" ]] || exit 1
  GIT_NO_REPLACE_OBJECTS=1 git -C "$source_repo" show "$revision:$tracked" | cmp -s - "$installed"
}
check_file ops/virtualoffice/mariadb/docker-compose.yml /etc/axioma-office/mariadb/compose.yml root:root:644
check_file ops/virtualoffice/office-dedicated-mariadb-admin-transport.sh /usr/local/libexec/axioma-office-mariadb-admin root:root:750
check_file ops/virtualoffice/systemd/axioma-office-database.service /etc/systemd/system/axioma-office-database.service root:root:644
check_file ops/virtualoffice/office-base-artifact-readiness.sh /usr/local/libexec/axioma-office-base-artifact-readiness root:root:750
[[ -f /etc/axioma-office/mariadb/compose.env && ! -L /etc/axioma-office/mariadb/compose.env &&
   $(stat -c '%U:%G:%a' /etc/axioma-office/mariadb/compose.env) == root:root:600 ]]
[[ -f /var/lib/axioma-office-mariadb/secrets/root_password.txt &&
   ! -L /var/lib/axioma-office-mariadb/secrets/root_password.txt &&
   $(stat -c '%U:%G:%a' /var/lib/axioma-office-mariadb/secrets/root_password.txt) == root:root:600 ]]
if [[ $phase == migration || $phase == services ]]; then
  check_file ops/virtualoffice/provision-office-os-users.sh /usr/local/libexec/axioma-office-provision-os-users root:root:750
  check_file ops/virtualoffice/systemd/axioma-office-migrate.service /etc/systemd/system/axioma-office-migrate.service root:root:644
  env_names=(migrate)
  if [[ $phase == services ]]; then
    check_file ops/virtualoffice/systemd/axioma-office-backend.service /etc/systemd/system/axioma-office-backend.service root:root:644
    env_names+=(runtime)
  fi
  for name in "${env_names[@]}"; do
    env_file=/etc/axioma-office/$name.env
    [[ -f $env_file && ! -L $env_file && $(stat -c '%U:%G:%a' "$env_file") == root:root:600 ]]
  done
  [[ $(id -nG officeweb) == officeweb && $(id -nG officemigrate) == officemigrate ]]
fi
