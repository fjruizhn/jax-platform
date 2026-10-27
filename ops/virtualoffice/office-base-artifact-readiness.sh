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
check_file ops/migration/provision-db-runtime.sh /usr/local/libexec/axioma-office-db-runtime/provision-db-runtime.sh root:root:750
check_file ops/migration/runtime-table-privileges-v1.txt /usr/local/libexec/axioma-office-db-runtime/runtime-table-privileges-v1.txt root:root:644
check_file ops/migration/runtime-table-privileges-v2.txt /usr/local/libexec/axioma-office-db-runtime/runtime-table-privileges-v2.txt root:root:644
check_file ops/migration/pair_artifact_readiness.py /opt/axioma-office-pair/libexec/pair_artifact_readiness.py root:root:644
check_file ops/virtualoffice/office-pair-exec.py /usr/local/libexec/axioma-office-pair-exec.py root:root:644
check_file ops/virtualoffice/install-office-pair-release.py /usr/local/libexec/axioma-office-pair-install root:root:750
for path in /opt/axioma-office-pair /opt/axioma-office-pair/releases /opt/axioma-office-pair/libexec; do
  [[ -d $path && ! -L $path && $(stat -c '%U:%G:%a' "$path") == root:root:755 ]]
done
[[ -f /opt/axioma-office-pair/.runtime.lock && ! -L /opt/axioma-office-pair/.runtime.lock &&
   $(stat -c '%U:%G:%a' /opt/axioma-office-pair/.runtime.lock) == root:root:644 ]]
check_file ops/virtualoffice/systemd/axioma-office-database.service /etc/systemd/system/axioma-office-database.service root:root:644
check_file ops/virtualoffice/office-base-artifact-readiness.sh /usr/local/libexec/axioma-office-base-artifact-readiness root:root:750
systemctl show axioma-office-database.service -p WorkingDirectory | grep -Fqx WorkingDirectory=/etc/axioma-office/mariadb
systemctl show axioma-office-database.service -p EnvironmentFiles | grep -Fq /etc/axioma-office/mariadb/compose.env
systemctl show axioma-office-database.service -p ExecStart | grep -Fq '/etc/axioma-office/mariadb/compose.yml up -d --wait'
systemctl show axioma-office-database.service -p ExecStop | grep -Fq '/etc/axioma-office/mariadb/compose.yml stop'
systemctl show axioma-office-database.service -p DropInPaths | grep -Fqx DropInPaths=
[[ -f /etc/axioma-office/mariadb/compose.env && ! -L /etc/axioma-office/mariadb/compose.env &&
   $(stat -c '%U:%G:%a' /etc/axioma-office/mariadb/compose.env) == root:root:600 ]]
[[ -f /var/lib/axioma-office-mariadb/secrets/root_password.txt &&
   ! -L /var/lib/axioma-office-mariadb/secrets/root_password.txt &&
   $(stat -c '%U:%G:%a' /var/lib/axioma-office-mariadb/secrets/root_password.txt) == root:root:600 ]]
if [[ $phase == migration || $phase == services ]]; then
  python3 -I -S - "$revision" <<'PY'
import hashlib, json, os, stat, sys
from pathlib import Path
import importlib.util

expected = sys.argv[1]
root = Path('/opt/axioma-office-pair')
selection = Path('/etc/axioma-office/platform-pair-selection.json')
node = selection.lstat()
if not stat.S_ISREG(node.st_mode) or node.st_uid != 0 or node.st_gid != 0 or stat.S_IMODE(node.st_mode) != 0o644:
    raise SystemExit('Office pair selection is unsafe')
data = json.loads(selection.read_text(encoding='utf-8'))
if set(data) != {'release', 'jax_platform_revision', 'jax_revision', 'manifest_sha256'} or data['jax_platform_revision'] != expected:
    raise SystemExit('Office pair selection names a different platform SHA')
release = Path(data['release']).resolve(strict=True)
if release.parent != (root / 'releases').resolve(strict=True) or (root / 'current').resolve(strict=True) != release:
    raise SystemExit('Office pair release is not active')
manifest = release / 'pair-manifest.json'
if hashlib.sha256(manifest.read_bytes()).hexdigest() != data['manifest_sha256']:
    raise SystemExit('Office pair manifest digest differs')
spec = importlib.util.spec_from_file_location('pair_readiness', root / 'libexec/pair_artifact_readiness.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.verify(release, expected_jxp=expected, expected_jax=data['jax_revision'])
PY
  check_file ops/virtualoffice/provision-office-os-users.sh /usr/local/libexec/axioma-office-provision-os-users root:root:750
  check_file ops/virtualoffice/systemd/axioma-office-migrate.service /etc/systemd/system/axioma-office-migrate.service root:root:644
  systemctl show axioma-office-migrate.service -p User | grep -Fqx User=officemigrate
  systemctl show axioma-office-migrate.service -p Group | grep -Fqx Group=officemigrate
  systemctl show axioma-office-migrate.service -p DropInPaths | grep -Fqx DropInPaths=
  systemctl show axioma-office-migrate.service -p ExecStart | grep -Fq 'axioma-office-pair-exec.py office-migrate'
  env_names=(migrate)
  if [[ $phase == services ]]; then
    check_file ops/virtualoffice/systemd/axioma-office-backend.service /etc/systemd/system/axioma-office-backend.service root:root:644
    systemctl show axioma-office-backend.service -p User | grep -Fqx User=officeweb
    systemctl show axioma-office-backend.service -p Group | grep -Fqx Group=officeweb
    systemctl show axioma-office-backend.service -p DropInPaths | grep -Fqx DropInPaths=
    systemctl show axioma-office-backend.service -p ExecStart | grep -Fq 'axioma-office-pair-exec.py office-web'
    env_names+=(runtime)
  fi
  for name in "${env_names[@]}"; do
    env_file=/etc/axioma-office/$name.env
    [[ -f $env_file && ! -L $env_file && $(stat -c '%U:%G:%a' "$env_file") == root:root:600 ]]
  done
  [[ $(id -nG officeweb) == officeweb && $(id -nG officemigrate) == officemigrate ]]
fi
