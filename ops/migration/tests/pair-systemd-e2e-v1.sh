#!/usr/bin/env bash
# Real-PID1 acceptance proof for pair activation.  It runs only in a
# disposable privileged Docker systemd container; it never addresses host /etc
# or /opt.  Exit 77 means the host cannot provide that isolation.
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
command -v docker >/dev/null || { echo 'SKIP: docker is unavailable' >&2; exit 77; }
docker info >/dev/null 2>&1 || { echo 'SKIP: docker daemon/privileged access is unavailable' >&2; exit 77; }

image=$(docker build -q -f - /tmp <<'DOCKERFILE'
FROM debian:bookworm
RUN apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    systemd systemd-sysv dbus python3 python3-fastapi python3-uvicorn curl util-linux >/dev/null \
    && rm -rf /var/lib/apt/lists/*
CMD ["/sbin/init"]
DOCKERFILE
)
container=$(docker run -d --rm --privileged --cgroupns=private --tmpfs /run --tmpfs /run/lock \
  -v "$repo_root:/workspace:ro" "$image")
cleanup() { docker rm -f "$container" >/dev/null 2>&1 || true; docker image rm "$image" >/dev/null 2>&1 || true; }
trap cleanup EXIT

if ! docker exec -i "$container" /bin/bash -se <<'CONTAINER'
set -euo pipefail
for n in {1..40}; do systemctl show --property=Version >/dev/null 2>&1 && break; sleep .25; done
systemctl show --property=Version >/dev/null
test "$(cat /proc/1/comm)" = systemd

root=/tmp/pair-systemd-e2e
install -d -o root -g root -m 0755 "$root/releases" "$root/libexec" /etc/jax /usr/local/libexec
# The production validator's temporary-root escape hatch still attests every
# ancestor and therefore rejects sticky /tmp itself.  This PID1 fixture owns a
# root-only, manifest-shape probe instead; artifact byte sealing is exercised
# by pair-supervisor-e2e-v1 and artifact_readiness unit tests.
cat >"$root/libexec/pair_artifact_readiness.py" <<'PY'
import json, sys
from pathlib import Path
class PairReadinessError(Exception): pass
def verify(release, **_expected):
    try:
        value=json.loads((Path(release) / 'pair-manifest.json').read_text())
        if value['schema'] != 'axioma.jax-platform-pair/v1': raise ValueError
    except Exception as exc:
        raise PairReadinessError from exc
PY
chmod 0644 "$root/libexec/pair_artifact_readiness.py"
install -m 0755 -o root -g root /workspace/ops/migration/activate-platform-pair-release.sh /usr/local/libexec/activate-platform-pair-release.sh
install -m 0644 -o root -g root /workspace/ops/migration/jax-platform-pair-exec.py /usr/local/libexec/jax-platform-pair-exec.py
install -m 0644 -o root -g root /workspace/ops/migration/systemd-units/jax-platform.service /etc/systemd/system/jax-platform.service
install -m 0644 -o root -g root /workspace/ops/migration/systemd-units/jax-platform-migrate.service /etc/systemd/system/jax-platform-migrate.service
cat >/etc/systemd/system/docker.service <<'EOF'
[Service]
Type=simple
ExecStart=/bin/sleep infinity
EOF
useradd --system --home-dir /nonexistent --shell /usr/sbin/nologin jaxweb
useradd --system --home-dir /nonexistent --shell /usr/sbin/nologin jaxmigrate
printf 'JAX_TRUSTED_PROXIES=127.0.0.1\n' >/etc/jax/runtime.env
printf 'JAX_DB_HOST=127.0.0.1\n' >/etc/jax/migrate.env
chmod 0600 /etc/jax/runtime.env /etc/jax/migrate.env
install -d -o root -g root -m 0755 /etc/systemd/system/jax-platform.service.d /etc/systemd/system/jax-platform-migrate.service.d
cat >/etc/systemd/system/jax-platform.service.d/e2e.conf <<EOF
[Service]
User=root
Environment=JAX_PAIR_TEST_MODE=1 JAX_PAIR_TEST_ROOT=$root
ExecStart=
ExecStart=/usr/bin/python3 -I -S /usr/local/libexec/jax-platform-pair-exec.py web /etc/jax/runtime.env
EOF
cat >/etc/systemd/system/jax-platform-migrate.service.d/e2e.conf <<EOF
[Service]
User=root
Environment=JAX_PAIR_TEST_MODE=1 JAX_PAIR_TEST_ROOT=$root
ExecStart=
ExecStart=/usr/bin/python3 -I -S /usr/local/libexec/jax-platform-pair-exec.py migrate /etc/jax/migrate.env
EOF

build_release() {
  local name=$1 platform=$2 jax=$3 previous_platform=$4 previous_jax=$5
  local release="$root/releases/$name"
  install -d -o root -g root -m 0755 "$release/jax-platform/backend/.venv/bin" "$release/jax/jax" "$release/jax-platform/backend/db"
  cat >"$release/jax-platform/backend/.venv/bin/python" <<'SH'
#!/bin/sh
exec /usr/bin/python3 "$@"
SH
  chmod 0755 "$release/jax-platform/backend/.venv/bin/python"
  cat >"$release/jax-platform/backend/main.py" <<'PY'
from fastapi import FastAPI
app = FastAPI()
@app.get('/api/health')
def health(): return {'ok': True}
PY
  : >"$release/jax-platform/backend/db/__init__.py"
  cat >"$release/jax-platform/backend/db/migrate.py" <<'PY'
if __name__ == '__main__': pass
PY
  : >"$release/jax/jax/__init__.py"
  python3 - "$release" "$platform" "$jax" "$previous_platform" "$previous_jax" <<'PY'
import hashlib,json,os,stat,sys
from pathlib import Path
r,platform,jax,pp,pj=map(Path,sys.argv[1:])
def digest(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1048576),b''): h.update(b)
 return h.hexdigest()
att={'schema':'axioma.pair-build/v2','platform_commit':str(platform),'platform_tree':'a'*40,'platform_links':[],'jax_commit':str(jax),'jax_tree':'b'*40,'jax_links':[],'wheelhouse_sha256':'c'*64,'lock_sha256':'d'*64,'python':'/usr/bin/python3','python_sha256':'e'*64,'python_version':'fixture','freeze_sha256':'f'*64}
(r/'build-attestation.json').write_text(json.dumps(att,sort_keys=True,separators=(',',':'))+'\n')
(r/'release-lineage.json').write_text(json.dumps({'schema':'axioma.pair-lineage/v1','platform_predecessor':None if str(pp)=='-' else str(pp),'jax_predecessor':None if str(pj)=='-' else str(pj)},sort_keys=True,separators=(',',':'))+'\n')
files=[]
for base,dirs,names in os.walk(r):
 dirs.sort(); names.sort()
 for name in names:
  p=Path(base)/name
  if p.name=='pair-manifest.json': continue
  s=p.stat(); files.append({'path':p.relative_to(r).as_posix(),'size':s.st_size,'mode':f'{stat.S_IMODE(s.st_mode):04o}','sha256':digest(p)})
manifest={'schema':'axioma.jax-platform-pair/v1','jax_platform_revision':str(platform),'jax_revision':str(jax),'dependency_lock_sha256':'d'*64,'wheelhouse_manifest_sha256':'c'*64,'files':files,'jax_links':[]}
(r/'pair-manifest.json').write_text(json.dumps(manifest,sort_keys=True,separators=(',',':'))+'\n')
PY
  sha256sum "$release/pair-manifest.json" | awk '{print $1}'
}

p1=1111111111111111111111111111111111111111
j1=2222222222222222222222222222222222222222
p2=3333333333333333333333333333333333333333
j2=4444444444444444444444444444444444444444
d1=$(build_release predecessor "$p1" "$j1" - -)
d2=$(build_release successor "$p2" "$j2" "$p1" "$j1")
ln -s releases/predecessor "$root/current"
python3 - "$root/platform-pair-selection.json" "$root/releases/predecessor" "$p1" "$j1" "$d1" <<'PY'
import json,sys
open(sys.argv[1],'w').write(json.dumps(dict(zip(('release','jax_platform_revision','jax_revision','manifest_sha256'),sys.argv[2:])),separators=(',',':'))+'\n')
PY
touch "$root/.runtime.lock"; chown root:root "$root/.runtime.lock"; chmod 0644 "$root/.runtime.lock"
systemctl daemon-reload

# SIGKILL after publish leaves the durable PRE_DDL journal and the candidate
# selected.  A second real invocation must stop the units, recover predecessor,
# then start the successor through systemd/PID1.
if JAX_PAIR_TEST_MODE=1 JAX_PAIR_SYSTEMCTL_BIN=/usr/bin/systemctl JAX_PAIR_HEALTH_BIN=/usr/bin/curl JAX_PAIR_TEST_KILL_AT=after_publish /usr/local/libexec/activate-platform-pair-release.sh \
  --root "$root" --release "$root/releases/successor" --expected-manifest-sha256 "$d2" --previous-manifest-sha256 "$d1"; then
  echo 'activation cutpoint was not reached' >&2; exit 1
fi
test "$(python3 -c "import json; print(json.load(open('$root/activation-journal.json'))['state'])")" = PRE_DDL
# A stale PRE_DDL selector must never become a bootable web process.  This is
# intentionally checked against PID1 before recovery; if it starts, the test
# fails with evidence that the runtime gate is missing.
if systemctl start jax-platform.service; then
  echo 'PRE_DDL start was accepted by real systemd' >&2
  exit 1
fi
test "$(systemctl show --value --property=ActiveState jax-platform.service)" != active
# The rejected boot can leave the dependency unit in failed state.  Reset that
# transient state so recovery exercises the activation script's own orderly
# stop/start path rather than systemd's remembered failure result.
systemctl reset-failed jax-platform.service jax-platform-migrate.service
JAX_PAIR_TEST_MODE=1 JAX_PAIR_SYSTEMCTL_BIN=/usr/bin/systemctl JAX_PAIR_HEALTH_BIN=/usr/bin/curl /usr/local/libexec/activate-platform-pair-release.sh \
  --root "$root" --release "$root/releases/successor" --expected-manifest-sha256 "$d2" --previous-manifest-sha256 "$d1"
test ! -e "$root/activation-journal.json"
test "$(readlink "$root/current")" = releases/successor
test "$(systemctl show --value --property=ActiveState jax-platform.service)" = active
pid=$(systemctl show --value --property=MainPID jax-platform.service)
test "$pid" -gt 1
tr '\0' '\n' <"/proc/$pid/environ" | grep -Fx "JAX_REPO_PATH=$root/releases/successor/jax"
curl --fail --silent http://127.0.0.1:8080/api/health >/dev/null
echo PAIR_SYSTEMD_E2E_V1_OK
CONTAINER
then
  docker exec "$container" journalctl --no-pager -u jax-platform-migrate.service -u jax-platform.service >&2 || true
  exit 1
fi
