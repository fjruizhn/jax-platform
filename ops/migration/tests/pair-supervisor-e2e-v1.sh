#!/usr/bin/env bash
# Disposable privileged integration proof for the paired artifact boundary.
# It never mounts or changes a host release, account, /etc, or /opt: all of
# those paths exist only inside the throw-away Python 3.14 Bookworm container.
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
docker run --rm --pull=missing -i \
  -v "$repo_root:/workspace:ro" \
  python:3.14-bookworm /bin/bash -se <<'CONTAINER'
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y --no-install-recommends git util-linux >/dev/null

# The production supervisor pins these absolute paths.  The official Python
# image provides CPython under /usr/local, so mirror the production pathname
# only inside this disposable container.
ln -sfn /usr/local/bin/python3.14 /usr/bin/python3
useradd --system --home-dir /opt/pair-e2e/jaxbuild --create-home --shell /usr/sbin/nologin jaxbuild
install -d -o root -g root -m 0755 /usr/local/libexec /opt/pair-e2e/root /opt/pair-e2e/evidence /opt/pair-e2e/inputs/wheelhouse
install -m 0755 -o root -g root /workspace/ops/migration/build-pair-jaxbuild.py /usr/local/libexec/build-pair-jaxbuild.py
install -m 0755 -o root -g root /workspace/ops/migration/pair-build-supervisor.py /usr/local/libexec/pair-build-supervisor.py
install -m 0755 -o root -g root /workspace/ops/migration/adopt-pair-root.sh /usr/local/libexec/adopt-pair-root.py
install -m 0755 -o root -g root /workspace/ops/migration/bootstrap-pair-predecessor.py /usr/local/libexec/bootstrap-pair-predecessor.py
# The builder imports readiness beside its own code; keeping both files
# root-owned makes the fixture exercise the real trusted-code condition.
install -m 0644 -o root -g root /workspace/ops/migration/pair_artifact_readiness.py /usr/local/libexec/pair_artifact_readiness.py
printf 'placeholder wheelhouse member\n' >/opt/pair-e2e/inputs/wheelhouse/placeholder.whl
printf '# empty lock; its hash marker is deliberately present for the builder contract\n# --hash=sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n' >/opt/pair-e2e/inputs/lock
chmod 0644 /opt/pair-e2e/inputs/lock /opt/pair-e2e/inputs/wheelhouse/placeholder.whl
chown -R root:root /opt/pair-e2e/inputs

make_repo() {
  local path=$1 name=$2
  install -d -o jaxbuild -g jaxbuild -m 0755 "$path"
  runuser -u jaxbuild -- git init -q "$path"
  runuser -u jaxbuild -- git -C "$path" config user.email pair-e2e@example.invalid
  runuser -u jaxbuild -- git -C "$path" config user.name pair-e2e
  install -d -o jaxbuild -g jaxbuild -m 0755 "$path/backend"
  printf '%s\n' "$name" >"$path/backend/main.py"
  chown jaxbuild:jaxbuild "$path/backend/main.py"
  runuser -u jaxbuild -- git -C "$path" add .
  runuser -u jaxbuild -- git -C "$path" commit -qm fixture
}
make_repo /opt/pair-e2e/platform platform
make_repo /opt/pair-e2e/jax jax
platform_commit=$(runuser -u jaxbuild -- git -C /opt/pair-e2e/platform rev-parse HEAD)
jax_commit=$(runuser -u jaxbuild -- git -C /opt/pair-e2e/jax rev-parse HEAD)
wheelhouse_sha=$(python3 - <<'PY'
import hashlib
print(hashlib.sha256(b'placeholder.whl\0' + hashlib.sha256(b'placeholder wheelhouse member\n').hexdigest().encode() + b'\n').hexdigest())
PY
)
lock_sha=$(sha256sum /opt/pair-e2e/inputs/lock | awk '{print $1}')
builder_sha=$(sha256sum /usr/local/libexec/build-pair-jaxbuild.py | awk '{print $1}')
python_sha=/usr/local/bin/python3.14
python3 - <<PY
import json
request = {
  'schema': 'axioma.pair-build-request/v1',
  'nonce': '1' * 64,
  'platform': '/opt/pair-e2e/platform', 'platform_commit': '$platform_commit',
  'jax': '/opt/pair-e2e/jax', 'jax_commit': '$jax_commit',
  'platform_predecessor': None, 'jax_predecessor': None,
  'inputs': '/opt/pair-e2e/inputs', 'wheelhouse_sha256': '$wheelhouse_sha',
  'lock_sha256': '$lock_sha', 'out': '/opt/pair-e2e/jaxbuild/candidate',
  'bootstrap_predecessor': True, 'builder_sha256': '$builder_sha',
  'python': '$python_sha',
}
open('/opt/pair-e2e/root/request.json', 'w', encoding='utf-8').write(json.dumps(request, sort_keys=True, separators=(',', ':')) + '\n')
PY
chmod 0600 /opt/pair-e2e/root/request.json
chown root:root /opt/pair-e2e/root/request.json
install -d -o jaxbuild -g jaxbuild -m 0700 /opt/pair-e2e/jaxbuild

# Root only supervises.  Git, venv and pip execute in the jaxbuild child; its
# receipt crosses the privilege boundary solely through the inherited pipe FD.
python3 -I -S /usr/local/libexec/pair-build-supervisor.py \
  --request /opt/pair-e2e/root/request.json --builder /usr/local/libexec/build-pair-jaxbuild.py \
  --receipt /opt/pair-e2e/evidence/receipt.json
test "$(stat -c '%u:%g:%a' /opt/pair-e2e/evidence/receipt.json)" = '0:0:600'
test "$(stat -c '%u:%g' /opt/pair-e2e/jaxbuild/candidate)" = "$(id -u jaxbuild):$(id -g jaxbuild)"
manifest_sha=$(sha256sum /opt/pair-e2e/jaxbuild/candidate/pair-manifest.json | awk '{print $1}')

# Three negative gates use copies so the successful receipt and output remain
# intact: request binding, receipt binding, and output inode binding.
cp /opt/pair-e2e/root/request.json /opt/pair-e2e/root/request-forged.json
python3 - <<'PY'
import json
p='/opt/pair-e2e/root/request-forged.json'; value=json.load(open(p)); value['nonce']='2'*64
open(p,'w').write(json.dumps(value, sort_keys=True,separators=(',',':'))+'\n')
PY
chmod 0600 /opt/pair-e2e/root/request-forged.json
if python3 -I -S /usr/local/libexec/adopt-pair-root.py --source /opt/pair-e2e/jaxbuild/candidate --destination /opt/pair-e2e/forged-request --expected-manifest-sha256 "$manifest_sha" --request /opt/pair-e2e/root/request-forged.json --receipt /opt/pair-e2e/evidence/receipt.json; then
  echo 'forged request was accepted' >&2; exit 1
fi
cp /opt/pair-e2e/evidence/receipt.json /opt/pair-e2e/evidence/receipt-forged.json
python3 - <<'PY'
import json
p='/opt/pair-e2e/evidence/receipt-forged.json'; value=json.load(open(p)); value['manifest_sha256']='0'*64
open(p,'w').write(json.dumps(value, sort_keys=True,separators=(',',':'))+'\n')
PY
chmod 0600 /opt/pair-e2e/evidence/receipt-forged.json
if python3 -I -S /usr/local/libexec/adopt-pair-root.py --source /opt/pair-e2e/jaxbuild/candidate --destination /opt/pair-e2e/forged-receipt --expected-manifest-sha256 "$manifest_sha" --request /opt/pair-e2e/root/request.json --receipt /opt/pair-e2e/evidence/receipt-forged.json; then
  echo 'forged receipt was accepted' >&2; exit 1
fi
cp -a /opt/pair-e2e/jaxbuild/candidate /opt/pair-e2e/jaxbuild/candidate-other-inode
if python3 -I -S /usr/local/libexec/adopt-pair-root.py --source /opt/pair-e2e/jaxbuild/candidate-other-inode --destination /opt/pair-e2e/forged-inode --expected-manifest-sha256 "$manifest_sha" --request /opt/pair-e2e/root/request.json --receipt /opt/pair-e2e/evidence/receipt.json; then
  echo 'receipt accepted a distinct output inode' >&2; exit 1
fi

# Bootstrap performs root adoption from the supervised candidate and publishes
# the first root-owned selection.  These paths are container-local only.
install -d -o root -g root -m 0755 /opt/jax-platform-pair/releases /etc/jax
python3 -I -S /usr/local/libexec/bootstrap-pair-predecessor.py \
  --source /opt/pair-e2e/jaxbuild/candidate \
  --destination /opt/jax-platform-pair/releases/"$platform_commit"-"$jax_commit" \
  --expected-manifest-sha256 "$manifest_sha" \
  --request /opt/pair-e2e/root/request.json --receipt /opt/pair-e2e/evidence/receipt.json
release=/opt/jax-platform-pair/releases/"$platform_commit"-"$jax_commit"
test "$(stat -c '%u:%g' "$release")" = '0:0'
test "$(stat -c '%i' /opt/pair-e2e/jaxbuild/candidate)" != "$(stat -c '%i' "$release")"
test "$(readlink /opt/jax-platform-pair/current)" = "releases/$platform_commit-$jax_commit"
python3 - <<PY
import json
value=json.load(open('/etc/jax/platform-pair-selection.json'))
assert value['release'] == '$release'
assert value['manifest_sha256'] == '$manifest_sha'
assert value['jax_platform_revision'] == '$platform_commit'
assert value['jax_revision'] == '$jax_commit'
PY
test -z "$(pgrep -u jaxbuild || true)"
echo 'PAIR_SUPERVISOR_E2E_V1_OK'
CONTAINER
