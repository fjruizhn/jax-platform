#!/usr/bin/env bash
# Install dedicated Office delivery units from an exact reviewed Git commit.
set -euo pipefail
umask 022

source_repo=
revision=
while [[ $# -gt 0 ]]; do
  case "$1" in
    --source) source_repo=${2:?}; shift ;;
    --revision) revision=${2:?}; shift ;;
    -h|--help) echo 'usage: install-office-delivery-runtime.sh --source REPOSITORY --revision FULL_SHA'; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done
[[ $(id -u) == 0 && -d $source_repo && $revision =~ ^[0-9a-f]{40}$ ]] || {
  echo 'root, repository, and full revision are required' >&2; exit 2;
}
[[ $(GIT_NO_REPLACE_OBJECTS=1 git -C "$source_repo" rev-parse "$revision^{commit}") == "$revision" ]] || {
  echo 'reviewed revision is unavailable' >&2; exit 1;
}
stage=$(mktemp -d /var/tmp/axioma-office-delivery.XXXXXXXX)
trap 'rm -rf "$stage"' EXIT
GIT_NO_REPLACE_OBJECTS=1 git -C "$source_repo" archive --format=tar "$revision" \
  ops/virtualoffice/systemd/axioma-office-delivery.service \
  ops/virtualoffice/systemd/axioma-office-delivery.timer \
  ops/virtualoffice/invitation-worker-readiness.sh \
  ops/virtualoffice/office-delivery-artifact-readiness.sh \
  ops/virtualoffice/enable-office-delivery-timer.sh | tar -x -C "$stage"
base=$stage/ops/virtualoffice
[[ -f $base/systemd/axioma-office-delivery.service &&
   -f $base/systemd/axioma-office-delivery.timer &&
   -f $base/invitation-worker-readiness.sh &&
   -f $base/office-delivery-artifact-readiness.sh &&
   -f $base/enable-office-delivery-timer.sh ]] || { echo 'incomplete delivery artifact' >&2; exit 1; }
if systemctl is-active --quiet axioma-office-delivery.timer || systemctl is-active --quiet axioma-office-delivery.service; then
  echo 'stop the dedicated delivery timer before replacing its units' >&2; exit 1;
fi
install -d -o root -g root -m 0755 /usr/local/libexec /etc/systemd/system
install -o root -g root -m 0755 "$base/invitation-worker-readiness.sh" /usr/local/libexec/axioma-office-dedicated-worker-readiness
install -o root -g root -m 0750 "$base/office-delivery-artifact-readiness.sh" /usr/local/libexec/axioma-office-delivery-artifact-readiness
install -o root -g root -m 0750 "$base/enable-office-delivery-timer.sh" /usr/local/libexec/axioma-office-delivery-enable
install -o root -g root -m 0644 "$base/systemd/axioma-office-delivery.service" /etc/systemd/system/axioma-office-delivery.service
install -o root -g root -m 0644 "$base/systemd/axioma-office-delivery.timer" /etc/systemd/system/axioma-office-delivery.timer
systemctl daemon-reload
/usr/local/libexec/axioma-office-delivery-artifact-readiness "$source_repo" "$revision"
