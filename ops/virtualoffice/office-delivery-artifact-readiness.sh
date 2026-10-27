#!/usr/bin/env bash
# Attest the exact dedicated Office timer and its effective identity.
set -euo pipefail
source_repo=${1:?usage: office-delivery-artifact-readiness.sh REPOSITORY FULL_SHA [--effective]}
revision=${2:?full revision required}
effective=${3:-}
[[ $revision =~ ^[0-9a-f]{40}$ ]] || exit 2
check_file() {
  local tracked=$1 installed=$2 owner_mode=$3
  [[ -f $installed && ! -L $installed && $(stat -c '%U:%G:%a' "$installed") == "$owner_mode" ]] || exit 1
  GIT_NO_REPLACE_OBJECTS=1 git -C "$source_repo" show "$revision:$tracked" | cmp -s - "$installed"
}
check_file ops/virtualoffice/systemd/axioma-office-delivery.service /etc/systemd/system/axioma-office-delivery.service root:root:644
check_file ops/virtualoffice/systemd/axioma-office-delivery.timer /etc/systemd/system/axioma-office-delivery.timer root:root:644
check_file ops/virtualoffice/invitation-worker-readiness.sh /usr/local/libexec/axioma-office-invitation-worker-readiness root:root:755
check_file ops/virtualoffice/office-delivery-artifact-readiness.sh /usr/local/libexec/axioma-office-delivery-artifact-readiness root:root:750
check_file ops/virtualoffice/enable-office-delivery-timer.sh /usr/local/libexec/axioma-office-delivery-enable root:root:750
grep -Fqx 'User=officeinvite' /etc/systemd/system/axioma-office-delivery.service
grep -Fqx 'Group=officeinvite' /etc/systemd/system/axioma-office-delivery.service
grep -Fqx 'EnvironmentFile=/etc/axioma-office/invitation-worker.env' /etc/systemd/system/axioma-office-delivery.service
grep -Fqx 'Unit=axioma-office-delivery.service' /etc/systemd/system/axioma-office-delivery.timer
[[ $(id -nG officeinvite) == officeinvite ]]
[[ -f /etc/axioma-office/invitation-worker.env && ! -L /etc/axioma-office/invitation-worker.env &&
   $(stat -c '%U:%G:%a' /etc/axioma-office/invitation-worker.env) == root:root:600 ]]
[[ -f /opt/axioma-office-invitation-worker/release-manifest.json ]]
[[ $(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["source_revision"])' \
  /opt/axioma-office-invitation-worker/release-manifest.json) == "$revision" ]]
if [[ $effective == --effective ]]; then
  systemctl is-enabled axioma-office-delivery.timer | grep -qx enabled
  systemctl is-active axioma-office-delivery.timer | grep -qx active
  systemctl show axioma-office-delivery.service -p User | grep -Fqx User=officeinvite
  systemctl show axioma-office-delivery.service -p Group | grep -Fqx Group=officeinvite
  systemctl show axioma-office-delivery.service -p EnvironmentFiles | grep -Fq /etc/axioma-office/invitation-worker.env
  systemctl show axioma-office-delivery.service -p ExecStart | grep -Fq virtual_office.invitation_delivery_worker
fi
