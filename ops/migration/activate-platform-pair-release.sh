#!/usr/bin/env bash
# Root-only, recoverable selection of an already adopted pair.  The release
# builder proves Git ancestry; this program never invokes Git or release code.
set -euo pipefail
PATH=/usr/sbin:/usr/bin:/sbin:/bin
ROOT=/opt/jax-platform-pair RELEASE= EXPECTED= PREVIOUS= SELECTION=/etc/jax/platform-pair-selection.json SYSTEMCTL=/usr/bin/systemctl HELPER=/usr/local/libexec/pair_artifact_readiness.py HEALTH=/usr/bin/curl
while [[ $# -gt 0 ]]; do
 case "$1" in
  --release) RELEASE=${2:?}; shift;; --expected-manifest-sha256) EXPECTED=${2:?}; shift;; --previous-manifest-sha256) PREVIOUS=${2:?}; shift;; --root) ROOT=${2:?}; shift;;
  -h|--help) echo 'usage: activate-platform-pair-release.sh --release RELEASE --expected-manifest-sha256 SHA --previous-manifest-sha256 SHA'; exit 0;; *) exit 2;; esac; shift
done
if [[ $ROOT != /opt/jax-platform-pair ]]; then
  [[ ${JAX_PAIR_TEST_MODE:-} == 1 && $(/usr/bin/id -u) == 0 && $ROOT == /tmp/* ]] || { echo 'test root rejected' >&2; exit 2; }
  SELECTION="$ROOT/platform-pair-selection.json"; HELPER="$ROOT/libexec/pair_artifact_readiness.py"
  [[ -n ${JAX_PAIR_SYSTEMCTL_BIN:-} && -x ${JAX_PAIR_SYSTEMCTL_BIN} && -n ${JAX_PAIR_HEALTH_BIN:-} && -x ${JAX_PAIR_HEALTH_BIN} ]] || { echo 'test tools rejected' >&2; exit 2; }
  SYSTEMCTL=$JAX_PAIR_SYSTEMCTL_BIN; HEALTH=$JAX_PAIR_HEALTH_BIN
fi
[[ $(/usr/bin/id -u) == 0 && $EXPECTED =~ ^[0-9a-f]{64}$ && $PREVIOUS =~ ^[0-9a-f]{64}$ ]] || { echo 'external target and predecessor manifest digests required' >&2; exit 2; }
JOURNAL="$ROOT/activation-journal.json"

# All writes are atomic and fsync their file and parent directory. PRE_DDL can
# be restored after a crash; once DDL_STARTED is durable no automatic rollback
# is possible, because a migration may have committed.
transaction() { /usr/bin/python3 -Es - "$@" <<'PY'
import base64, json, os, sys
from pathlib import Path

action, root_s, selection_s, journal_s = sys.argv[1:5]
root, selection, journal = Path(root_s), Path(selection_s), Path(journal_s)
def atomic(path, value):
    temp = path.with_name('.'+path.name+'.tmp')
    fd=os.open(temp, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o644)
    try:
      os.write(fd,value); os.fsync(fd)
    finally: os.close(fd)
    os.chown(temp,0,0); os.chmod(temp,0o644); os.replace(temp,path)
    d=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY); os.fsync(d); os.close(d)
def link(name, target):
    path=root/name; temp=root/('.'+name+'.tmp')
    try: temp.unlink()
    except FileNotFoundError: pass
    os.symlink(target,temp); os.replace(temp,path)
    d=os.open(root,os.O_RDONLY|os.O_DIRECTORY); os.fsync(d); os.close(d)
if action == 'recover':
    if not journal.exists(): raise SystemExit(0)
    data=json.loads(journal.read_text())
    if data.get('schema')!='axioma.pair-activation/v1': raise SystemExit('invalid activation journal')
    if data.get('state') == 'DDL_STARTED': raise SystemExit('RECOVERY_REQUIRED: migration may have modified the database')
    if data.get('state') != 'PRE_DDL': raise SystemExit('invalid activation journal state')
    atomic(selection,base64.b64decode(data['previous_selection_b64']))
    link('current',data['previous_current'])
    journal.unlink(); d=os.open(root,os.O_RDONLY|os.O_DIRECTORY); os.fsync(d); os.close(d)
elif action == 'prepare':
    previous = selection.read_bytes()
    current = os.readlink(root/'current')
    data={'schema':'axioma.pair-activation/v1','state':'PRE_DDL','previous_selection_b64':base64.b64encode(previous).decode(),
          'previous_current':current,'target_release':sys.argv[5],'target_manifest_sha256':sys.argv[6]}
    atomic(journal,(json.dumps(data,sort_keys=True,separators=(',',':'))+'\n').encode())
elif action == 'publish':
    release, digest=sys.argv[5:7]
    manifest=json.loads((Path(release)/'pair-manifest.json').read_text())
    atomic(selection,(json.dumps({'release':release,'jax_platform_revision':manifest['jax_platform_revision'],'jax_revision':manifest['jax_revision'],'manifest_sha256':digest},separators=(',',':'))+'\n').encode())
    link('current','releases/'+Path(release).name)
elif action == 'ddl':
    data=json.loads(journal.read_text()); data['state']='DDL_STARTED'
    atomic(journal,(json.dumps(data,sort_keys=True,separators=(',',':'))+'\n').encode())
elif action == 'complete':
    journal.unlink(); d=os.open(root,os.O_RDONLY|os.O_DIRECTORY); os.fsync(d); os.close(d)
else: raise SystemExit(2)
PY
}

exec 9>"$ROOT/.activation.lock"; /usr/bin/flock -w 30 9 || { echo 'activation lock timeout' >&2; exit 1; }
transaction recover "$ROOT" "$SELECTION" "$JOURNAL"
[[ ! -e $ROOT/.runtime.lock ]] && /usr/bin/install -o root -g root -m 0644 /dev/null "$ROOT/.runtime.lock"
[[ ! -L $ROOT/.runtime.lock && $(/usr/bin/stat -c '%U:%G:%a' "$ROOT/.runtime.lock") == root:root:644 ]] || { echo 'unsafe runtime lock' >&2; exit 1; }
releases_real=$(/usr/bin/realpath -e -- "$ROOT/releases") || { echo 'release root unavailable' >&2; exit 1; }
release_real=$(/usr/bin/realpath -e -- "$RELEASE") || { echo 'unsafe release' >&2; exit 1; }
[[ $release_real == "$releases_real/"* && -d $release_real && ! -L $release_real ]] || { echo 'unsafe release' >&2; exit 1; }
RELEASE=$release_real
actual=$(/usr/bin/sha256sum "$RELEASE/pair-manifest.json" | /usr/bin/cut -d' ' -f1); [[ $actual == "$EXPECTED" ]] || { echo 'unexpected manifest digest' >&2; exit 1; }
/usr/bin/python3 -Es "$HELPER" "$RELEASE" || exit 1
current=$(/usr/bin/realpath -e "$ROOT/current" 2>/dev/null || true)
[[ -n $current && $(/usr/bin/sha256sum "$current/pair-manifest.json" | /usr/bin/cut -d' ' -f1) == "$PREVIOUS" ]] || { echo 'predecessor digest mismatch' >&2; exit 1; }
[[ $current != "$RELEASE" ]] || exit 0
/usr/bin/python3 -Es - "$RELEASE/release-lineage.json" "$current/pair-manifest.json" <<'PY'
import json,sys
lineage=json.load(open(sys.argv[1])); previous=json.load(open(sys.argv[2]))
if (lineage.get('schema')!='axioma.pair-lineage/v1' or set(lineage)!={'schema','platform_predecessor','jax_predecessor'}
    or lineage['platform_predecessor']!=previous.get('jax_platform_revision') or lineage['jax_predecessor']!=previous.get('jax_revision')): raise SystemExit(1)
PY
[[ $? == 0 ]] || { echo 'release lineage does not descend from current pair' >&2; exit 1; }

$SYSTEMCTL stop jax-platform.service
$SYSTEMCTL stop jax-platform-migrate.service
for unit in jax-platform.service jax-platform-migrate.service; do
  state=$($SYSTEMCTL show --value --property=ActiveState "$unit")
  pid=$($SYSTEMCTL show --value --property=MainPID "$unit")
  [[ $state == inactive && $pid == 0 ]] || { echo "service did not stop: $unit" >&2; exit 1; }
done
exec 8>"$ROOT/.runtime.lock"; /usr/bin/flock -w 30 -x 8 || { echo 'runtime lock timeout' >&2; exit 1; }
transaction prepare "$ROOT" "$SELECTION" "$JOURNAL" "$RELEASE" "$EXPECTED"
transaction publish "$ROOT" "$SELECTION" "$JOURNAL" "$RELEASE" "$EXPECTED"
if [[ ${JAX_PAIR_TEST_KILL_AT:-} == after_publish ]]; then kill -KILL $$; fi
transaction ddl "$ROOT" "$SELECTION" "$JOURNAL"
/usr/bin/flock -u 8; exec 8>&-
# Starting web triggers its Requires=jax-platform-migrate.service exactly once.
$SYSTEMCTL start jax-platform.service
$HEALTH --fail --silent --show-error --max-time 10 http://127.0.0.1:8080/api/health >/dev/null
transaction complete "$ROOT" "$SELECTION" "$JOURNAL"
