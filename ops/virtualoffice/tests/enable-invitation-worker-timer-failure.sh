#!/usr/bin/env bash
# Isolated rollback proof: it never invokes systemctl or an Office service.
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
HELPER="$ROOT_DIR/ops/virtualoffice/enable-invitation-worker-timer.sh"
TEMP=$(mktemp -d /tmp/invitation-worker-enable.XXXXXX)
cleanup() { rm -rf "$TEMP"; }
trap cleanup EXIT

SYSTEMCTL="$TEMP/systemctl"
GATE="$TEMP/gate"
STATE="$TEMP/state"
LOG="$TEMP/log"
install -d -m 0755 -o root -g root "$TEMP/source"
cat > "$SYSTEMCTL" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "$INVITATION_WORKER_TEST_LOG"
case "$*" in
  'enable --now axioma-office-invitation-worker.timer') printf '%s\n' enabled > "$INVITATION_WORKER_TEST_STATE" ;;
  'disable --now axioma-office-invitation-worker.timer') printf '%s\n' disabled > "$INVITATION_WORKER_TEST_STATE" ;;
  *) exit 64 ;;
esac
EOF
cat > "$GATE" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
[[ $2 == --effective ]]
exit 1
EOF
chown root:root "$SYSTEMCTL" "$GATE"
chmod 0700 "$SYSTEMCTL" "$GATE"

if INVITATION_WORKER_TEST_LOG="$LOG" INVITATION_WORKER_TEST_STATE="$STATE" \
  "$HELPER" --source "$TEMP/source" --systemctl "$SYSTEMCTL" --gate "$GATE"; then
  echo 'activation accepted a failing effective gate' >&2; exit 1
fi
[[ $(cat "$STATE") == disabled ]] || { echo 'failing gate left timer enabled' >&2; exit 1; }
[[ $(cat "$LOG") == $'enable --now axioma-office-invitation-worker.timer\ndisable --now axioma-office-invitation-worker.timer' ]] || {
  echo 'activation did not enable then disable the exact timer' >&2; exit 1;
}
echo 'invitation worker activation rollback fixture passed'
