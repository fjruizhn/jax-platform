#!/usr/bin/env bash
# Root-only, recoverable selection of an already adopted pair.  The release
# builder proves Git ancestry; this program never invokes Git or release code.
set -euo pipefail
PATH=/usr/sbin:/usr/bin:/sbin:/bin
ROOT=/opt/jax-platform-pair RELEASE= EXPECTED= PREVIOUS= MEASURED_HOST= SELECTION=/etc/jax/platform-pair-selection.json SYSTEMCTL=/usr/bin/systemctl HELPER=/usr/local/libexec/pair_artifact_readiness.py HEALTH=/usr/bin/curl PROVISIONER=/usr/local/libexec/provision-db-runtime.sh ENV_FILE=/etc/jax/.env
LOCK_WAIT=30
while [[ $# -gt 0 ]]; do
 case "$1" in
  --release) RELEASE=${2:?}; shift;; --expected-manifest-sha256) EXPECTED=${2:?}; shift;; --previous-manifest-sha256) PREVIOUS=${2:?}; shift;; --measured-grantee-host) MEASURED_HOST=${2:?}; shift;; --root) ROOT=${2:?}; shift;;
  -h|--help) echo 'usage: activate-platform-pair-release.sh --release RELEASE --expected-manifest-sha256 SHA --previous-manifest-sha256 SHA --measured-grantee-host HOST'; exit 0;; *) exit 2;; esac; shift
done
if [[ $ROOT != /opt/jax-platform-pair ]]; then
  [[ ${JAX_PAIR_TEST_MODE:-} == 1 && $(/usr/bin/id -u) == 0 && $ROOT == /tmp/* ]] || { echo 'test root rejected' >&2; exit 2; }
  SELECTION="$ROOT/platform-pair-selection.json"; HELPER="$ROOT/libexec/pair_artifact_readiness.py"
  [[ -n ${JAX_PAIR_SYSTEMCTL_BIN:-} && -x ${JAX_PAIR_SYSTEMCTL_BIN} && -n ${JAX_PAIR_HEALTH_BIN:-} && -x ${JAX_PAIR_HEALTH_BIN} ]] || { echo 'test tools rejected' >&2; exit 2; }
  SYSTEMCTL=$JAX_PAIR_SYSTEMCTL_BIN; HEALTH=$JAX_PAIR_HEALTH_BIN
  [[ -z ${JAX_PAIR_PROVISIONER_BIN:-} || -x ${JAX_PAIR_PROVISIONER_BIN} ]] || { echo 'test provisioner rejected' >&2; exit 2; }
  [[ -z ${JAX_PAIR_PROVISIONER_BIN:-} ]] || PROVISIONER=$JAX_PAIR_PROVISIONER_BIN
  [[ -z ${JAX_PAIR_PROVISION_ENV:-} ]] || ENV_FILE=$JAX_PAIR_PROVISION_ENV
  [[ -z ${JAX_PAIR_TEST_LOCK_SECONDS:-} || ${JAX_PAIR_TEST_LOCK_SECONDS} =~ ^[0-9]+([.][0-9]+)?$ ]] || { echo 'test lock timeout rejected' >&2; exit 2; }
  [[ -z ${JAX_PAIR_TEST_LOCK_SECONDS:-} ]] || LOCK_WAIT=$JAX_PAIR_TEST_LOCK_SECONDS
fi
if [[ ${JAX_PAIR_TEST_MODE:-} == 1 ]]; then
  [[ -z ${JAX_PAIR_PROVISIONER_BIN:-} || -x ${JAX_PAIR_PROVISIONER_BIN} ]] || { echo 'test provisioner rejected' >&2; exit 2; }
  [[ -z ${JAX_PAIR_PROVISIONER_BIN:-} ]] || PROVISIONER=$JAX_PAIR_PROVISIONER_BIN
  [[ -z ${JAX_PAIR_PROVISION_ENV:-} ]] || ENV_FILE=$JAX_PAIR_PROVISION_ENV
fi
[[ $(/usr/bin/id -u) == 0 && $EXPECTED =~ ^[0-9a-f]{64}$ && $PREVIOUS =~ ^[0-9a-f]{64}$ && -n $MEASURED_HOST ]] || { echo 'external target and predecessor manifest digests required; measured DB grantee host required' >&2; exit 2; }
[[ -f $PROVISIONER && ! -L $PROVISIONER ]] || { echo 'runtime DB provisioner unavailable' >&2; exit 1; }
if [[ $ROOT == /opt/jax-platform-pair ]]; then
  env_stat=$(/usr/bin/stat -c '%U:%G:%a' "$ENV_FILE" 2>/dev/null || true)
  [[ $(/usr/bin/stat -c '%U:%G:%a' "$PROVISIONER") == root:root:755 && -f $ENV_FILE && ! -L $ENV_FILE && ( $env_stat == root:root:600 || $env_stat == root:jaxsvc:640 ) ]] || { echo 'runtime DB provisioner or environment is unsafe' >&2; exit 1; }
fi
JOURNAL="$ROOT/activation-journal.json"

# All writes are atomic and fsync their file and parent directory. PRE_DDL can
# be restored after a crash; once DDL_STARTED is durable no automatic rollback
# is possible, because a migration may have committed.
transaction() { /usr/bin/python3 -Es - "$@" <<'PY'
import base64, hashlib, json, os, sys, tempfile
from pathlib import Path

action, root_s, selection_s, journal_s = sys.argv[1:5]
root, selection, journal = Path(root_s), Path(selection_s), Path(journal_s)
def atomic(path, value):
    fd, temp_s = tempfile.mkstemp(prefix='.'+path.name+'.', dir=path.parent)
    temp=Path(temp_s)
    try:
      os.write(fd,value); os.fsync(fd)
      os.close(fd); fd=-1
      os.chown(temp,0,0); os.chmod(temp,0o644); os.replace(temp,path)
    finally:
      if fd >= 0: os.close(fd)
      try: temp.unlink()
      except FileNotFoundError: pass
    d=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY); os.fsync(d); os.close(d)
def link(name, target):
    path=root/name; slot=Path(tempfile.mkdtemp(prefix='.'+name+'.',dir=root)); temp=slot/'value'
    try:
      os.symlink(target,temp); os.replace(temp,path)
    finally:
      try: temp.unlink()
      except FileNotFoundError: pass
      try: slot.rmdir()
      except OSError: pass
    d=os.open(root,os.O_RDONLY|os.O_DIRECTORY); os.fsync(d); os.close(d)
def pair():
    x=json.loads(selection.read_text())
    if set(x) != {'release','jax_platform_revision','jax_revision','manifest_sha256'}: raise SystemExit('invalid selection')
    release=Path(x['release']).resolve(strict=True); releases=(root/'releases').resolve(strict=True)
    current=(root/'current').resolve(strict=True)
    if release.parent != releases or current != release: raise SystemExit('selection/current mismatch')
    if hashlib.sha256((release/'pair-manifest.json').read_bytes()).hexdigest()!=x['manifest_sha256']: raise SystemExit('selection digest mismatch')
    return x, release
def checked_journal(release, digest):
    data=json.loads(journal.read_text())
    required={'schema','state','previous_selection_b64','previous_current','target_release','target_manifest_sha256','target_selection_sha256'}
    if set(data)!=required or data.get('schema')!='axioma.pair-activation/v1': raise SystemExit('invalid activation journal')
    if data['target_release'] != release or data['target_manifest_sha256'] != digest: raise SystemExit('journal target mismatch')
    return data
if action == 'inspect':
    if journal.exists():
      data=checked_journal(sys.argv[5],sys.argv[6])
      if data['state'] not in ('PRE_DDL','DDL_STARTED'): raise SystemExit('invalid activation journal state')
      print(data['state'])
elif action == 'recover':
    if not journal.exists(): raise SystemExit(0)
    data=checked_journal(sys.argv[5],sys.argv[6])
    if data.get('state') == 'DDL_STARTED': raise SystemExit('RECOVERY_REQUIRED: migration may have modified the database')
    if data.get('state') != 'PRE_DDL': raise SystemExit('invalid activation journal state')
    previous_selection=base64.b64decode(data['previous_selection_b64'])
    atomic(selection,previous_selection)
    # Selection is the authoritative runtime input. Derive current from those
    # restored bytes instead of replaying a potentially stale relative link.
    prior=json.loads(previous_selection)
    release=Path(prior['release']).resolve(strict=True)
    releases=(root/'releases').resolve(strict=True)
    if release.parent != releases or data['previous_current'] != 'releases/'+release.name: raise SystemExit('invalid prior selection in activation journal')
    link('current','releases/'+release.name)
    pair()
    journal.unlink(); d=os.open(root,os.O_RDONLY|os.O_DIRECTORY); os.fsync(d); os.close(d)
elif action == 'prepare':
    previous = selection.read_bytes(); x, current = pair()
    current = os.readlink(root/'current')
    target=sys.argv[5]; digest=sys.argv[6]
    manifest=json.loads((Path(target)/'pair-manifest.json').read_text())
    target_selection=json.dumps({'release':target,'jax_platform_revision':manifest['jax_platform_revision'],'jax_revision':manifest['jax_revision'],'manifest_sha256':digest},separators=(',',':')).encode()+b'\n'
    data={'schema':'axioma.pair-activation/v1','state':'PRE_DDL','previous_selection_b64':base64.b64encode(previous).decode(),
          'previous_current':current,'target_release':target,'target_manifest_sha256':digest,'target_selection_sha256':hashlib.sha256(target_selection).hexdigest()}
    atomic(journal,(json.dumps(data,sort_keys=True,separators=(',',':'))+'\n').encode())
elif action == 'publish':
    release, digest=sys.argv[5:7]
    manifest=json.loads((Path(release)/'pair-manifest.json').read_text())
    atomic(selection,(json.dumps({'release':release,'jax_platform_revision':manifest['jax_platform_revision'],'jax_revision':manifest['jax_revision'],'manifest_sha256':digest},separators=(',',':'))+'\n').encode())
    if os.environ.get('JAX_PAIR_TEST_KILL_AT') == 'after_selection': os.kill(os.getpid(),9)
    link('current','releases/'+Path(release).name)
    pair()
    if os.environ.get('JAX_PAIR_TEST_KILL_AT') == 'after_current': os.kill(os.getpid(),9)
elif action == 'ddl':
    data=checked_journal(sys.argv[5],sys.argv[6]); x, current=pair()
    if hashlib.sha256(selection.read_bytes()).hexdigest()!=data['target_selection_sha256']: raise SystemExit('target selection binding mismatch')
    data['state']='DDL_STARTED'
    atomic(journal,(json.dumps(data,sort_keys=True,separators=(',',':'))+'\n').encode())
elif action == 'complete':
    journal.unlink(); d=os.open(root,os.O_RDONLY|os.O_DIRECTORY); os.fsync(d); os.close(d)
else: raise SystemExit(2)
PY
}

exec 9>"$ROOT/.activation.lock"; /usr/bin/flock -w "$LOCK_WAIT" 9 || { echo 'activation lock timeout' >&2; exit 1; }
[[ ! -e $ROOT/.runtime.lock ]] && /usr/bin/install -o root -g root -m 0644 /dev/null "$ROOT/.runtime.lock"
[[ ! -L $ROOT/.runtime.lock && $(/usr/bin/stat -c '%U:%G:%a' "$ROOT/.runtime.lock") == root:root:644 ]] || { echo 'unsafe runtime lock' >&2; exit 1; }
releases_real=$(/usr/bin/realpath -e -- "$ROOT/releases") || { echo 'release root unavailable' >&2; exit 1; }
release_real=$(/usr/bin/realpath -e -- "$RELEASE") || { echo 'unsafe release' >&2; exit 1; }
[[ $release_real == "$releases_real/"* && -d $release_real && ! -L $release_real ]] || { echo 'unsafe release' >&2; exit 1; }
RELEASE=$release_real
actual=$(/usr/bin/sha256sum "$RELEASE/pair-manifest.json" | /usr/bin/cut -d' ' -f1); [[ $actual == "$EXPECTED" ]] || { echo 'unexpected manifest digest' >&2; exit 1; }
/usr/bin/python3 -Es "$HELPER" "$RELEASE" || exit 1
pending=$(transaction inspect "$ROOT" "$SELECTION" "$JOURNAL" "$RELEASE" "$EXPECTED")
[[ $pending != DDL_STARTED ]] || { echo 'RECOVERY_REQUIRED: migration may have modified the database' >&2; exit 1; }
$SYSTEMCTL stop jax-platform.service
$SYSTEMCTL stop jax-platform-migrate.service
for unit in jax-platform.service jax-platform-migrate.service; do
  state=$($SYSTEMCTL show --value --property=ActiveState "$unit")
  substate=$($SYSTEMCTL show --value --property=SubState "$unit")
  pid=$($SYSTEMCTL show --value --property=MainPID "$unit")
  result=$($SYSTEMCTL show --value --property=Result "$unit")
  [[ $state == inactive && $substate == dead && $pid == 0 && $result == success ]] || { echo "service did not stop cleanly: $unit" >&2; exit 1; }
done
exec 8>"$ROOT/.runtime.lock"; /usr/bin/flock -x -w "$LOCK_WAIT" 8 || { echo 'runtime lock timeout' >&2; exit 1; }
if [[ $pending == PRE_DDL ]]; then
  transaction recover "$ROOT" "$SELECTION" "$JOURNAL" "$RELEASE" "$EXPECTED"
  current=$(/usr/bin/realpath -e "$ROOT/current")
  [[ $(/usr/bin/sha256sum "$current/pair-manifest.json" | /usr/bin/cut -d' ' -f1) == "$PREVIOUS" ]] || { echo 'recovered predecessor digest mismatch' >&2; exit 1; }
fi
current=$(/usr/bin/realpath -e "$ROOT/current" 2>/dev/null || true)
[[ -n $current && $(/usr/bin/sha256sum "$current/pair-manifest.json" | /usr/bin/cut -d' ' -f1) == "$PREVIOUS" ]] || { echo 'predecessor digest mismatch' >&2; exit 1; }
[[ $current != "$RELEASE" ]] || { /usr/bin/flock -u 8; exit 0; }
/usr/bin/python3 -Es - "$RELEASE/release-lineage.json" "$current/pair-manifest.json" <<'PY'
import json,sys
lineage=json.load(open(sys.argv[1])); previous=json.load(open(sys.argv[2]))
if (lineage.get('schema')!='axioma.pair-lineage/v1' or set(lineage)!={'schema','platform_predecessor','jax_predecessor'}
    or lineage['platform_predecessor']!=previous.get('jax_platform_revision') or lineage['jax_predecessor']!=previous.get('jax_revision')): raise SystemExit(1)
PY
[[ $? == 0 ]] || { echo 'release lineage does not descend from current pair' >&2; exit 1; }
transaction prepare "$ROOT" "$SELECTION" "$JOURNAL" "$RELEASE" "$EXPECTED"
transaction publish "$ROOT" "$SELECTION" "$JOURNAL" "$RELEASE" "$EXPECTED"
if [[ ${JAX_PAIR_TEST_KILL_AT:-} == after_publish ]]; then kill -KILL $$; fi
transaction ddl "$ROOT" "$SELECTION" "$JOURNAL" "$RELEASE" "$EXPECTED"
/usr/bin/flock -u 8; exec 8>&-
# The runtime principal cannot receive its exact table grants until this
# release's migration created its Office tables.  Run that migration first,
# then converge its grants before the web service has any opportunity to use
# them.  The migrator stays active after a successful one-shot, so the normal
# web dependency observes it as satisfied and preserves all other unit
# requirements without running this migration twice.
$SYSTEMCTL start jax-platform-migrate.service
state=$($SYSTEMCTL show --value --property=ActiveState jax-platform-migrate.service)
substate=$($SYSTEMCTL show --value --property=SubState jax-platform-migrate.service)
pid=$($SYSTEMCTL show --value --property=MainPID jax-platform-migrate.service)
result=$($SYSTEMCTL show --value --property=Result jax-platform-migrate.service)
[[ $state == active && $substate == exited && $pid == 0 && $result == success ]] || { echo 'migration service did not complete cleanly' >&2; exit 1; }
"$PROVISIONER" --env "$ENV_FILE" --measured-grantee-host "$MEASURED_HOST"
$SYSTEMCTL start jax-platform.service
# Type=simple returns from `start` when the process has been forked, before
# uvicorn has necessarily bound its listener.  Never accept a health response
# from a process whose effective systemd state is no longer the selected web
# service; check it before every bounded probe.
web_service_effective() {
  state=$($SYSTEMCTL show --value --property=ActiveState jax-platform.service)
  substate=$($SYSTEMCTL show --value --property=SubState jax-platform.service)
  pid=$($SYSTEMCTL show --value --property=MainPID jax-platform.service)
  result=$($SYSTEMCTL show --value --property=Result jax-platform.service)
  [[ $state == active && $substate == running && $pid =~ ^[1-9][0-9]*$ && $result == success ]]
}
health_deadline=$((SECONDS + 10))
while :; do
  web_service_effective || { echo 'web service did not become healthy' >&2; exit 1; }
  probe_pid=$pid
  if $HEALTH --fail --silent --show-error --max-time 1 http://127.0.0.1:8080/api/health >/dev/null; then
    # The endpoint may have been served by a process which replaced the unit's
    # MainPID while curl was in flight.  Only commit the activation if both
    # observations identify the same effective process.
    web_service_effective || { echo 'web service did not become healthy' >&2; exit 1; }
    [[ $pid == "$probe_pid" ]] || { echo 'web service changed during health check' >&2; exit 1; }
    break
  fi
  (( SECONDS < health_deadline )) || { echo 'web health check timed out' >&2; exit 1; }
  /usr/bin/sleep 1
done
/usr/bin/python3 -Es - "$ROOT" "$SELECTION" "$RELEASE" "$EXPECTED" "$pid" <<'PY'
import hashlib,json,sys
from pathlib import Path
root, selection, release, expected, pid=map(Path,sys.argv[1:])
x=json.loads(selection.read_text())
if (root/'current').resolve(strict=True) != release or Path(x['release']).resolve(strict=True) != release: raise SystemExit('selection/current mismatch after start')
if hashlib.sha256((release/'pair-manifest.json').read_bytes()).hexdigest()!=expected.name or x.get('manifest_sha256')!=expected.name: raise SystemExit('unexpected post-start manifest')
env=(Path('/proc')/pid.name/'environ').read_bytes().split(b'\0')
if ('JAX_REPO_PATH='+str(release/'jax')).encode() not in env: raise SystemExit('web process does not belong to selected release')
PY
transaction complete "$ROOT" "$SELECTION" "$JOURNAL"
