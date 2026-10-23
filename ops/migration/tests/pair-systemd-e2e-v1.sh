#!/usr/bin/env bash
# Real-PID1 acceptance proof for pair activation.  It runs only in a
# disposable privileged Docker systemd container; it never addresses host /etc
# or /opt.  Exit 77 means the host cannot provide that isolation.
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
command -v docker >/dev/null || { echo 'SKIP: docker is unavailable' >&2; exit 77; }
docker info >/dev/null 2>&1 || { echo 'SKIP: docker daemon/privileged access is unavailable' >&2; exit 77; }

image=$(docker build -q -f - /tmp <<'DOCKERFILE'
FROM ubuntu:26.04
RUN apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    systemd systemd-sysv dbus python3 python3-fastapi python3-uvicorn curl util-linux >/dev/null \
    && useradd --system --home-dir /nonexistent --shell /usr/sbin/nologin jaxweb \
    && useradd --system --home-dir /nonexistent --shell /usr/sbin/nologin jaxmigrate \
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
for n in {1..40}; do systemctl show-environment >/dev/null 2>&1 && break; sleep .25; done
systemctl show-environment >/dev/null
sleep 2
test "$(cat /proc/1/comm)" = systemd
test "$(systemctl --version | awk 'NR==1 {print $2}')" -ge 259

root=/opt/jax-platform-pair
install -d -o root -g root -m 0755 "$root/releases" /etc/jax /usr/local/libexec
install -m 0755 -o root -g root /workspace/ops/migration/activate-platform-pair-release.sh /usr/local/libexec/activate-platform-pair-release.sh
install -m 0755 -o root -g root /workspace/ops/migration/provision-db-runtime.sh /usr/local/libexec/provision-db-runtime.sh
install -m 0644 -o root -g root /workspace/ops/migration/runtime-table-privileges-v1.txt /usr/local/libexec/runtime-table-privileges-v1.txt
install -m 0644 -o root -g root /workspace/ops/migration/runtime-table-privileges-v2.txt /usr/local/libexec/runtime-table-privileges-v2.txt
install -m 0644 -o root -g root /workspace/ops/migration/jax-platform-pair-exec.py /usr/local/libexec/jax-platform-pair-exec.py
install -m 0644 -o root -g root /workspace/ops/migration/pair_artifact_readiness.py /usr/local/libexec/pair_artifact_readiness.py
install -m 0644 -o root -g root /workspace/ops/migration/systemd-units/jax-platform.service /etc/systemd/system/jax-platform.service
install -m 0644 -o root -g root /workspace/ops/migration/systemd-units/jax-platform-migrate.service /etc/systemd/system/jax-platform-migrate.service
cat >/etc/systemd/system/docker.service <<'EOF'
[Service]
Type=simple
ExecStart=/bin/sleep infinity
EOF
printf 'JAX_TRUSTED_PROXIES=127.0.0.1\n' >/etc/jax/runtime.env
printf 'JAX_DB_HOST=127.0.0.1\n' >/etc/jax/migrate.env
printf 'JAX_DB_GRANTEE_HOST=127.0.0.1\n' >/etc/jax/.env
chown root:root /etc/jax/runtime.env /etc/jax/migrate.env
chmod 0600 /etc/jax/runtime.env /etc/jax/migrate.env
chown root:root /etc/jax/.env
chmod 0600 /etc/jax/.env
cat >/usr/local/libexec/provision-fixture <<'SH'
#!/bin/sh
set -eu
printf '%s\n' "$*" >/var/lib/jax-pair-e2e/provision-args
SH
chmod 0755 /usr/local/libexec/provision-fixture
install -d -o jaxmigrate -g jaxmigrate -m 0700 /var/lib/jax-pair-e2e

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
if __name__ == '__main__':
    import os, pwd
    if os.geteuid() != pwd.getpwnam('jaxmigrate').pw_uid: raise SystemExit(1)
    marker='/var/lib/jax-pair-e2e/migrate-count'
    fd=os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as out: out.write('1\n')
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
python3 - /etc/jax/platform-pair-selection.json "$root/releases/predecessor" "$p1" "$j1" "$d1" <<'PY'
import json,sys
open(sys.argv[1],'w').write(json.dumps(dict(zip(('release','jax_platform_revision','jax_revision','manifest_sha256'),sys.argv[2:])),separators=(',',':'))+'\n')
PY
touch "$root/.runtime.lock"; chown root:root "$root/.runtime.lock"; chmod 0644 "$root/.runtime.lock"
systemctl daemon-reload
# Exercise the validator against PID1's D-Bus view before activation.  The
# source units are the exact files just installed, while `busctl` reports the
# effective argv that systemd retained (including credential syntax).
python3 /workspace/ops/migration/artifact_readiness.py "$root/releases/predecessor" \
  --units-dir /workspace/ops/migration/systemd-units busctl

# SIGKILL after publish leaves the durable PRE_DDL journal and the candidate
# selected.  A second real invocation must stop the units, recover predecessor,
# then start the successor through systemd/PID1.
if JAX_PAIR_TEST_MODE=1 JAX_PAIR_PROVISIONER_BIN=/usr/local/libexec/provision-fixture JAX_PAIR_TEST_KILL_AT=after_publish /usr/local/libexec/activate-platform-pair-release.sh \
  --release "$root/releases/successor" --expected-manifest-sha256 "$d2" --previous-manifest-sha256 "$d1" --measured-grantee-host 127.0.0.1; then
  echo 'activation cutpoint was not reached' >&2; exit 1
fi
test "$(python3 -c "import json; print(json.load(open('$root/activation-journal.json'))['state'])")" = PRE_DDL
test ! -e /var/lib/jax-pair-e2e/migrate-count
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
JAX_PAIR_TEST_MODE=1 JAX_PAIR_PROVISIONER_BIN=/usr/local/libexec/provision-fixture /usr/local/libexec/activate-platform-pair-release.sh \
  --release "$root/releases/successor" --expected-manifest-sha256 "$d2" --previous-manifest-sha256 "$d1" --measured-grantee-host 127.0.0.1
test ! -e "$root/activation-journal.json"
test "$(cat /var/lib/jax-pair-e2e/migrate-count)" = 1
test "$(cat /var/lib/jax-pair-e2e/provision-args)" = "--env /etc/jax/.env --measured-grantee-host 127.0.0.1"
test "$(readlink "$root/current")" = releases/successor
test "$(systemctl show --value --property=ActiveState jax-platform.service)" = active
pid=$(systemctl show --value --property=MainPID jax-platform.service)
test "$pid" -gt 1
test "$(awk '/^Uid:/{print $2}' "/proc/$pid/status")" = "$(id -u jaxweb)"
tr '\0' '\n' <"/proc/$pid/environ" | grep -Fx "JAX_REPO_PATH=$root/releases/successor/jax"
for _ in {1..40}; do
  curl --fail --silent http://127.0.0.1:8080/api/health >/dev/null && break
  sleep .25
done
curl --fail --silent http://127.0.0.1:8080/api/health >/dev/null
echo PAIR_SYSTEMD_E2E_V1_OK
CONTAINER
then
  docker exec "$container" journalctl --no-pager -u jax-platform-migrate.service -u jax-platform.service >&2 || true
  exit 1
fi
