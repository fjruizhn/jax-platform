#!/usr/bin/env bash
# Activate dedicated Office mail only after its own database/SMTP preflight.
set -euo pipefail
source_repo=
revision=
while [[ $# -gt 0 ]]; do
  case "$1" in
    --source) source_repo=${2:?}; shift ;;
    --revision) revision=${2:?}; shift ;;
    -h|--help) echo 'usage: enable-office-delivery-timer.sh --source REPOSITORY --revision FULL_SHA'; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done
[[ $(id -u) == 0 ]] || { echo 'root required' >&2; exit 1; }
gate=/usr/local/libexec/axioma-office-delivery-artifact-readiness
"$gate" "$source_repo" "$revision"
/usr/bin/systemd-run --wait --pipe --collect --property=User=officeinvite --property=Group=officeinvite \
  --property=WorkingDirectory=/opt/axioma-office-dedicated-delivery/backend \
  --property=EnvironmentFile=/etc/axioma-office/invitation-worker.env \
  /usr/local/libexec/axioma-office-dedicated-worker-readiness /etc/axioma-office/invitation-worker.env
/usr/bin/systemd-run --wait --pipe --collect --property=User=officeinvite --property=Group=officeinvite \
  --property=WorkingDirectory=/opt/axioma-office-dedicated-delivery/backend \
  --property=EnvironmentFile=/etc/axioma-office/invitation-worker.env \
  /opt/axioma-office-dedicated-delivery/backend/.venv/bin/python -Es -m virtual_office.invitation_smtp_preflight
systemctl enable --now axioma-office-delivery.timer
if ! "$gate" "$source_repo" "$revision" --effective; then
  systemctl disable --now axioma-office-delivery.timer
  exit 1
fi
