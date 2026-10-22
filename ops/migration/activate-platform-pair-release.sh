#!/usr/bin/env bash
# Root-only atomic activation. current is observational: units receive literals.
set -euo pipefail
ROOT=/opt/jax-platform-pair RELEASE= EXPECTED= PREVIOUS=
while [[ $# -gt 0 ]]; do
 case "$1" in
  --release) RELEASE=${2:?}; shift;; --expected-manifest-sha256) EXPECTED=${2:?}; shift;; --previous-manifest-sha256) PREVIOUS=${2:?}; shift;;
  -h|--help) echo 'usage: activate-platform-pair-release.sh --release /opt/jax-platform-pair/releases/JXP-JAX --expected-manifest-sha256 SHA [--previous-manifest-sha256 SHA]'; exit 0;; *) exit 2;; esac; shift
done
[[ $(id -u) == 0 && $RELEASE == "$ROOT/releases/"* && $EXPECTED =~ ^[0-9a-f]{64}$ ]] || exit 2
exec 9>"$ROOT/.activation.lock"; flock -n 9 || { echo 'pair activation already in progress' >&2; exit 1; }
[[ ! -L $RELEASE && -d $RELEASE ]] || { echo 'unsafe release' >&2; exit 1; }
actual=$(sha256sum "$RELEASE/pair-manifest.json" | cut -d' ' -f1); [[ $actual == "$EXPECTED" ]] || { echo 'unexpected manifest digest' >&2; exit 1; }
"$(dirname "${BASH_SOURCE[0]}")/pair_artifact_readiness.py" "$RELEASE" || exit 1
if [[ -n $PREVIOUS ]]; then
 current=$(readlink -f "$ROOT/current" 2>/dev/null || true)
 [[ -n $current && $(sha256sum "$current/pair-manifest.json" | cut -d' ' -f1) == "$PREVIOUS" ]] || { echo 'predecessor digest mismatch' >&2; exit 1; }
fi
systemctl stop jax-platform.service || true
systemctl stop jax-platform-migrate.service || true
for unit in jax-platform.service jax-platform-migrate.service; do
 dir="/etc/systemd/system/$unit.d"; install -d -o root -g root -m 0755 "$dir"
 backend="$RELEASE/jax-platform/backend"; python="$backend/.venv/bin/python"
 if [[ $unit == jax-platform.service ]]; then command="$python -Es -m uvicorn main:app --host 0.0.0.0 --port 8080 --proxy-headers --forwarded-allow-ips=\${JAX_TRUSTED_PROXIES} --no-access-log --log-level warning"; else command="$python -Es -m db.migrate"; fi
 printf '[Service]\nWorkingDirectory=%s\nExecStartPre=\nExecStartPre=/usr/bin/python3 -Es /usr/local/libexec/pair_artifact_readiness.py %s\nExecStart=\nExecStart=/usr/bin/env JAX_REPO_PATH=%s %s\n' "$backend" "$RELEASE" "$RELEASE/jax" "$command" | install -o root -g root -m 0644 /dev/stdin "$dir/20-pair-release.conf"
done
systemctl daemon-reload
systemctl start jax-platform-migrate.service
systemctl start jax-platform.service
ln -sfn "releases/$(basename "$RELEASE")" "$ROOT/.current.new"; mv -T "$ROOT/.current.new" "$ROOT/current"
