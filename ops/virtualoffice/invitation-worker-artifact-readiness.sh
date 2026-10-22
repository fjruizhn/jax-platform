#!/usr/bin/env bash
# Check committed worker artifacts, and optionally their loaded systemd state.
set -euo pipefail
root=${1:?usage: invitation-worker-artifact-readiness.sh REPOSITORY_ROOT [--effective]}
effective=${2:-}
service="$root/ops/virtualoffice/invitation-worker.service"
timer="$root/ops/virtualoffice/invitation-worker.timer"
readiness="$root/ops/virtualoffice/invitation-worker-readiness.sh"
provision="$root/ops/virtualoffice/provision-invitation-worker.sh"
launcher="$root/ops/virtualoffice/run-invitation-worker.py"
for path in "$service" "$timer" "$readiness" "$provision" "$launcher"; do [[ -f "$path" ]] || { printf '%s\n' "missing $path" >&2; exit 1; }; done
[[ -x "$readiness" && -x "$provision" && -x "$launcher" ]] || { printf '%s\n' 'worker helper is not executable' >&2; exit 1; }
grep -Fqx 'User=jaxinvite' "$service"
grep -Fqx 'EnvironmentFile=/etc/jax/invitation-worker.env' "$service"
grep -Fqx 'WorkingDirectory=/srv/jax-prod/jax-platform/backend' "$service"
grep -Fqx 'ExecStartPre=+/srv/jax-prod/jax-platform/ops/virtualoffice/invitation-worker-readiness.sh' "$service"
grep -Fqx 'ExecStart=/srv/jax-prod/jax-platform/backend/.venv/bin/python -Es /srv/jax-prod/jax-platform/ops/virtualoffice/run-invitation-worker.py' "$service"
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
if [[ "$effective" == --effective ]]; then
  systemctl is-enabled axioma-office-invitation-worker.timer | grep -qx enabled
  systemctl show axioma-office-invitation-worker.service -p User | grep -Fqx 'User=jaxinvite'
  systemctl show axioma-office-invitation-worker.service -p NoNewPrivileges | grep -Fqx 'NoNewPrivileges=yes'
  systemctl show axioma-office-invitation-worker.service -p PrivateDevices | grep -Fqx 'PrivateDevices=yes'
  systemctl show axioma-office-invitation-worker.service -p CapabilityBoundingSet | grep -Fqx 'CapabilityBoundingSet='
  systemctl show axioma-office-invitation-worker.service -p LimitCORE | grep -Fqx 'LimitCORE=0'
  systemctl show axioma-office-invitation-worker.service -p ProtectSystem | grep -Fqx 'ProtectSystem=strict'
  systemctl is-active axioma-office-invitation-worker.timer | grep -qx active
  systemctl cat aviso-fallo@.service >/dev/null
fi
