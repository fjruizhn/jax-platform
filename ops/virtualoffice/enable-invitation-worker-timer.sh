#!/usr/bin/env bash
# Enable delivery only when the post-enable effective gate can be rolled back.
set -euo pipefail

SOURCE=
SYSTEMCTL=/usr/bin/systemctl
GATE=/usr/local/libexec/axioma-office-invitation-worker-artifact-readiness
while [[ $# -gt 0 ]]; do
  case "$1" in
    --source) SOURCE=${2:?}; shift ;;
    --systemctl) SYSTEMCTL=${2:?}; shift ;;
    --gate) GATE=${2:?}; shift ;;
    -h|--help) echo 'usage: enable-invitation-worker-timer.sh --source REPOSITORY_ROOT [--systemctl PATH] [--gate PATH]'; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done
[[ $(id -u) == 0 && -d $SOURCE ]] || { echo 'root and a repository source are required' >&2; exit 2; }
for executable in "$SYSTEMCTL" "$GATE"; do
  [[ $executable == /* && -f $executable && ! -L $executable && -x $executable ]] || {
    echo 'activation executable is unsafe' >&2; exit 2;
  }
  [[ $(stat -c '%U:%G' "$executable") == root:root ]] || { echo 'activation executable is not root-owned' >&2; exit 2; }
  mode=$(stat -c '%a' "$executable")
  (( (8#$mode & 0022) == 0 )) || { echo 'activation executable is writable' >&2; exit 2; }
done

"$SYSTEMCTL" enable --now axioma-office-invitation-worker.timer
if ! "$GATE" "$SOURCE" --effective; then
  "$SYSTEMCTL" disable --now axioma-office-invitation-worker.timer
  exit 1
fi
