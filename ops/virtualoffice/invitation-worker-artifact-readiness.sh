#!/usr/bin/env bash
# Check committed worker artifacts, and optionally their loaded systemd state.
set -euo pipefail
root=${1:?usage: invitation-worker-artifact-readiness.sh REPOSITORY_ROOT [--effective]}
effective=${2:-}
service="$root/ops/virtualoffice/invitation-worker.service"
timer="$root/ops/virtualoffice/invitation-worker.timer"
readiness="$root/ops/virtualoffice/invitation-worker-runtime-readiness.sh"
provision="$root/ops/virtualoffice/provision-invitation-worker.sh"
installer="$root/ops/virtualoffice/install-invitation-worker-release.sh"
for path in "$service" "$timer" "$readiness" "$provision" "$installer"; do [[ -f "$path" ]] || { printf '%s\n' "missing $path" >&2; exit 1; }; done
[[ -x "$readiness" && -x "$provision" && -x "$installer" ]] || { printf '%s\n' 'worker helper is not executable' >&2; exit 1; }
grep -Fqx 'User=jaxinvite' "$service"
grep -Fqx 'EnvironmentFile=/etc/jax/invitation-worker.env' "$service"
grep -Fqx 'WorkingDirectory=/opt/axioma-office-invitation-worker/backend' "$service"
grep -Fqx 'ExecStartPre=/usr/local/libexec/axioma-office-invitation-worker-readiness' "$service"
grep -Fqx 'ExecStart=/opt/axioma-office-invitation-worker/backend/.venv/bin/python -Es -m virtual_office.invitation_delivery_worker' "$service"
grep -Fqx 'NoNewPrivileges=true' "$service"
grep -Fqx 'PrivateDevices=true' "$service"
grep -Fqx 'CapabilityBoundingSet=' "$service"
grep -Fqx 'LimitCORE=0' "$service"
grep -Fqx 'ProtectSystem=strict' "$service"
grep -Fqx 'OnFailure=aviso-fallo@%n.service' "$service"
grep -Fqx 'OnBootSec=30s' "$timer"
grep -Fqx 'OnUnitInactiveSec=30s' "$timer"
grep -Fqx 'Persistent=true' "$timer"
grep -Fq 'GRANT UPDATE ON' "$provision" && grep -Fq 'office_invitation_outbox' "$provision"
grep -Fq 'GRANT INSERT ON' "$provision" && grep -Fq 'office_invitation_event' "$provision"
grep -Fq 'git -C "$REPO" archive' "$installer"
grep -Fq 'chown -R root:root' "$installer"
if [[ "$effective" == --effective ]]; then
  systemctl is-enabled axioma-office-invitation-worker.timer | grep -qx enabled
  systemctl show axioma-office-invitation-worker.service -p User | grep -Fqx 'User=jaxinvite'
  systemctl show axioma-office-invitation-worker.service -p NoNewPrivileges | grep -Fqx 'NoNewPrivileges=yes'
  systemctl show axioma-office-invitation-worker.service -p PrivateDevices | grep -Fqx 'PrivateDevices=yes'
  systemctl show axioma-office-invitation-worker.service -p CapabilityBoundingSet | grep -Fqx 'CapabilityBoundingSet='
  systemctl show axioma-office-invitation-worker.service -p LimitCORE | grep -Fqx 'LimitCORE=0'
  systemctl show axioma-office-invitation-worker.service -p ProtectSystem | grep -Fqx 'ProtectSystem=strict'
  test -x /usr/local/libexec/axioma-office-invitation-worker-readiness
  test "$(stat -c '%U:%G:%a' /usr/local/libexec/axioma-office-invitation-worker-readiness)" = root:root:755
  /usr/local/libexec/axioma-office-invitation-worker-readiness --deep
  systemctl is-active axioma-office-invitation-worker.timer | grep -qx active
  systemctl cat aviso-fallo@.service >/dev/null
fi
