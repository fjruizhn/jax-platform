#!/usr/bin/python3
"""Credential-only paired runtime launcher; never inherits service environment."""
import fcntl, json, os, re, stat, sys
from pathlib import Path

ALLOWED = frozenset('FERNET_KEY FRONTEND_ORIGIN GUION_EJECUTOR JACOBS_URL JAX_ADJUNTOS_DIR JAX_AUDIT_LOG_PATH JAX_DB_CONNECT_TIMEOUT_SECONDS JAX_DB_HOST JAX_DB_NAME JAX_DB_PASSWORD JAX_DB_PORT JAX_DB_USER JAX_ENVIRONMENT JAX_JWT_SECRET JAX_OLLAMA_URL JAX_OPERATIONAL_TRUSTED_HOSTS JAX_PUBLIC_HOST_ORIGIN_BINDINGS JAX_REFRESH_COOKIE_SECURE JAX_TRUSTED_PROXIES LAS_MANOS_URL OFFICE_INVITATION_CLAIM_TIMEOUT_SECONDS OFFICE_INVITATION_DELIVERY_BATCH_LIMIT OFFICE_INVITATION_EXPIRY_SWEEP_BATCH_LIMIT OFFICE_INVITATION_LIFETIME_SECONDS OFFICE_INVITATION_PUBLIC_BASE_URL OFFICE_INVITATION_STALE_CLAIM_BATCH_LIMIT OFFICE_PUBLIC_ORIGIN'.split())
NAME = re.compile(r"[A-Z][A-Z0-9_]*\Z")

def die(): raise SystemExit(1)
def credential(path: Path):
    values = {}
    try: lines = path.read_text(encoding="utf-8").splitlines()
    except OSError: die()
    for line in lines:
        if not line or line.startswith('#'): continue
        if '=' not in line: die()
        key, value = line.split('=', 1)
        if not NAME.fullmatch(key) or key not in ALLOWED or key in values: die()
        values[key] = value
    if not values: die()
    return values

def main():
    if len(sys.argv) != 3 or sys.argv[1] not in ('web','migrate'): die()
    mode, cred = sys.argv[1:]
    root = Path('/opt/jax-platform-pair'); selection = Path('/etc/jax/platform-pair-selection.json')
    if os.environ.get('JAX_PAIR_TEST_MODE') == '1' and os.geteuid() == 0 and os.environ.get('JAX_PAIR_TEST_ROOT','').startswith('/tmp/'):
        root = Path(os.environ['JAX_PAIR_TEST_ROOT']); selection = root/'platform-pair-selection.json'
    lock = os.open(root/'.runtime.lock', os.O_RDONLY); fcntl.flock(lock, fcntl.LOCK_SH); os.set_inheritable(lock, True)
    st = selection.stat()
    if stat.S_ISLNK(st.st_mode) or st.st_uid != 0 or st.st_gid != 0 or stat.S_IMODE(st.st_mode) != 0o644: die()
    try: x=json.loads(selection.read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError): die()
    if set(x) != {'release','jax_platform_revision','jax_revision','manifest_sha256'}: die()
    release=Path(x['release']).resolve(strict=True); releases=(root/'releases').resolve(strict=True)
    if release.parent != releases or not all(isinstance(x[k],str) for k in x): die()
    if __import__('hashlib').sha256((release/'pair-manifest.json').read_bytes()).hexdigest()!=x['manifest_sha256']: die()
    import importlib.util
    helper = root/'libexec/pair_artifact_readiness.py' if root != Path('/opt/jax-platform-pair') else Path('/usr/local/libexec/pair_artifact_readiness.py')
    spec=importlib.util.spec_from_file_location('pair_readiness',helper)
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    try: module.verify(release, expected_jxp=x['jax_platform_revision'], expected_jax=x['jax_revision'], ancestor_stop=root if root != Path('/opt/jax-platform-pair') else None)
    except (module.PairReadinessError, OSError): die()
    child=credential(Path(cred)); child['PATH']='/usr/bin:/bin'; child['JAX_REPO_PATH']=str(release/'jax')
    backend=release/'jax-platform'/'backend'; os.chdir(backend)
    python=str(backend/'.venv/bin/python')
    argv=[python,'-I','-S','-m','uvicorn','main:app','--host','0.0.0.0','--port','8080','--proxy-headers',f"--forwarded-allow-ips={child.get('JAX_TRUSTED_PROXIES','')}",'--no-access-log','--log-level','warning'] if mode=='web' else [python,'-I','-S','-m','db.migrate']
    if mode=='web' and not child['JAX_TRUSTED_PROXIES']: die()
    os.execve(python,argv,child)
if __name__ == '__main__': main()
