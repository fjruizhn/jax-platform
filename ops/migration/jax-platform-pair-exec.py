#!/usr/bin/python3
"""Credential-only paired runtime launcher; never inherits service environment."""
import fcntl, json, os, re, stat, sys
from pathlib import Path

ALLOWED = frozenset('''
CLAUDE_CODE_OAUTH_TOKEN FERNET_KEY FRONTEND_ORIGIN GUION_EJECUTOR JACOBS_URL
JAX_ADJUNTOS_DIR JAX_ADJUNTOS_CUOTA_BYTES_USUARIO JAX_ADJUNTOS_DISCO_LIBRE_MINIMO_BYTES JAX_ADJUNTOS_RECHAZO_ESPERA_MS JAX_ADJUNTOS_SUBIDAS_POR_MINUTO JAX_ADJUNTOS_TTL_HORAS
JAX_ADJUNTO_IMAGENES_EN_PROCESO JAX_ADJUNTO_MAX_BYTES JAX_ADJUNTO_MAX_CHARS JAX_ADJUNTO_MAX_PAGINAS JAX_ADJUNTO_MAX_POR_MENSAJE JAX_ADJUNTO_PDF_PROCESOS JAX_ADJUNTO_PDF_TIMEOUT_SEGUNDOS JAX_ADJUNTO_SUBIDAS_EN_PROCESO JAX_AUDIT_LOG_PATH
JAX_CONFIG_PATH JAX_DB_CONNECT_TIMEOUT_SECONDS JAX_DB_HOST JAX_DB_NAME JAX_DB_PASSWORD JAX_DB_PORT JAX_DB_USER
JAX_EJECUTOR_ADMIN_USUARIO JAX_EJECUTOR_AUTO_COMPACT_WINDOW JAX_EJECUTOR_BASH_MAX_OUTPUT_LENGTH JAX_EJECUTOR_CANARIO_PUERTO JAX_EJECUTOR_CARACTERES_POR_TOKEN_MIN JAX_EJECUTOR_CERCO_SONDAS JAX_EJECUTOR_CONTROLADOR_LLAVE JAX_EJECUTOR_CUENTA JAX_EJECUTOR_CUENTA_HOME JAX_EJECUTOR_FILE_READ_MAX_OUTPUT_TOKENS JAX_EJECUTOR_FRENO_ESTADO JAX_EJECUTOR_FRENO_KNOWN_HOSTS JAX_EJECUTOR_FRENO_LLAVE JAX_EJECUTOR_FRENO_REMOTOS JAX_EJECUTOR_GANCHO_TOPE_S JAX_EJECUTOR_HUELLA_KNOWN_HOSTS JAX_EJECUTOR_HUELLA_LLAVE JAX_EJECUTOR_HUELLA_ORIGEN_IP JAX_EJECUTOR_INVENTARIO JAX_EJECUTOR_LIB JAX_EJECUTOR_LLAVES_ROOT JAX_EJECUTOR_MAX_ENTRADA_TOKENS JAX_EJECUTOR_MISIONES JAX_EJECUTOR_NODE_BIN JAX_EJECUTOR_PAUSA JAX_EJECUTOR_POLITICA JAX_EJECUTOR_PYTHON JAX_EJECUTOR_REGISTRO JAX_EJECUTOR_SSH_PUERTO JAX_EJECUTOR_TURNO_TOPE_S JAX_EJECUTOR_VIGIA_ESPERA_S JAX_EJECUTOR_VIGIA_LATIDO JAX_EJECUTOR_VIGIA_LATIDO_CADA_S JAX_EJECUTOR_VIGIA_LATIDO_MAX_S
JAX_ENVIRONMENT JAX_GITHUB_TOKEN JAX_JWT_SECRET JAX_KILL_SWITCH_PATH JAX_LAS_MANOS_CREDENCIAL_PLATAFORMA JAX_MEMORY_EMBED_COLUMN JAX_MIGRATE_DB_READINESS_ATTEMPTS JAX_MIGRATE_DB_READINESS_TIMEOUT_SECONDS JAX_OLLAMA_CPU_URL JAX_OLLAMA_URL JAX_OPERATIONAL_TRUSTED_HOSTS JAX_PLATFORM_URL
JAX_PROXY_CARRIL_HOST JAX_PROXY_CARRIL_MAX_SALIDA_TOKENS JAX_PROXY_CARRIL_MODELO JAX_PROXY_CARRIL_PENSAMIENTO JAX_PROXY_CARRIL_PUERTO JAX_PROXY_CARRIL_RAIZ JAX_PROXY_CARRIL_TOPE_S JAX_PROXY_CARRIL_UPSTREAM
JAX_PUBLIC_HOST_ORIGIN_BINDINGS JAX_REFRESH_COOKIE_SECURE JAX_REPO_BASE JAX_REPO_PATH JAX_SEED_SUPERADMIN_EMAIL JAX_SEED_TENANT_NAME JAX_TRUSTED_PROXIES JAX_WORKSPACE_DIR LAS_MANOS_URL OFFICE_BROKER_ALLOWED_BASE_URLS OFFICE_INVITATION_CLAIM_TIMEOUT_SECONDS OFFICE_INVITATION_DELIVERY_BATCH_LIMIT OFFICE_INVITATION_EXPIRY_SWEEP_BATCH_LIMIT OFFICE_INVITATION_LIFETIME_SECONDS OFFICE_INVITATION_PUBLIC_BASE_URL OFFICE_INVITATION_STALE_CLAIM_BATCH_LIMIT OFFICE_PUBLIC_ORIGIN PIPELINE_DETAIL_PATH TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID
'''.split())
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

def root_only_credential(path: Path):
    credential_stat = path.stat()
    if (not stat.S_ISREG(credential_stat.st_mode) or credential_stat.st_uid != 0
            or credential_stat.st_gid != 0 or stat.S_IMODE(credential_stat.st_mode) != 0o600):
        die()
    return credential(path)

def main():
    if sys.argv[1:] == ['--list-allowed-keys']:
        print('\n'.join(sorted(ALLOWED)))
        return
    if len(sys.argv) < 3 or sys.argv[1] not in ('web','migrate','office-engine'): die()
    mode, cred, *engine_args = sys.argv[1:]
    if mode in ('web', 'migrate') and engine_args: die()
    root = Path('/opt/jax-platform-pair'); selection = Path('/etc/jax/platform-pair-selection.json')
    if os.environ.get('JAX_PAIR_TEST_MODE') == '1' and os.geteuid() == 0 and os.environ.get('JAX_PAIR_TEST_ROOT','').startswith('/tmp/'):
        root = Path(os.environ['JAX_PAIR_TEST_ROOT']); selection = root/'platform-pair-selection.json'
    lock = os.open(root/'.runtime.lock', os.O_RDONLY); fcntl.flock(lock, fcntl.LOCK_SH); os.set_inheritable(lock, True)
    journal_path = root / 'activation-journal.json'
    try:
        journal_stat = journal_path.stat()
        if not stat.S_ISREG(journal_stat.st_mode) or journal_stat.st_uid != 0 or journal_stat.st_gid != 0 or stat.S_IMODE(journal_stat.st_mode) != 0o644: die()
        journal = json.loads(journal_path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        journal = None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        die()
    if journal is not None:
        expected = {'schema','state','previous_selection_b64','previous_current','target_release','target_manifest_sha256','target_selection_sha256'}
        if not isinstance(journal, dict) or set(journal) != expected or journal.get('schema') != 'axioma.pair-activation/v1': die()
        if journal.get('state') != 'DDL_STARTED': die()
    st = selection.stat()
    if stat.S_ISLNK(st.st_mode) or st.st_uid != 0 or st.st_gid != 0 or stat.S_IMODE(st.st_mode) != 0o644: die()
    try: x=json.loads(selection.read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError): die()
    if set(x) != {'release','jax_platform_revision','jax_revision','manifest_sha256'}: die()
    if journal is not None:
        digest = __import__('hashlib').sha256(selection.read_bytes()).hexdigest()
        if journal['target_release'] != x['release'] or journal['target_manifest_sha256'] != x['manifest_sha256'] or journal['target_selection_sha256'] != digest: die()
    release=Path(x['release']).resolve(strict=True); releases=(root/'releases').resolve(strict=True)
    try: current=(root/'current').resolve(strict=True)
    except OSError: die()
    if release.parent != releases or current != release or not all(isinstance(x[k],str) for k in x): die()
    if __import__('hashlib').sha256((release/'pair-manifest.json').read_bytes()).hexdigest()!=x['manifest_sha256']: die()
    import importlib.util
    helper = root/'libexec/pair_artifact_readiness.py' if root != Path('/opt/jax-platform-pair') else Path('/usr/local/libexec/pair_artifact_readiness.py')
    spec=importlib.util.spec_from_file_location('pair_readiness',helper)
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    try: module.verify(release, expected_jxp=x['jax_platform_revision'], expected_jax=x['jax_revision'], ancestor_stop=root if root != Path('/opt/jax-platform-pair') else None)
    except (module.PairReadinessError, OSError): die()
    child=(root_only_credential(Path(cred)) if mode == 'office-engine' else credential(Path(cred))); child['PATH']='/usr/bin:/bin'; child['JAX_REPO_PATH']=str(release/'jax')
    backend=release/'jax-platform'/'backend'; os.chdir(backend)
    python=str(backend/'.venv/bin/python')
    # -I keeps the child environment isolated; do not add -S here because the
    # venv's own site-packages contains uvicorn and the application modules.
    # The immutable release inventory includes no bytecode cache.  -I ignores
    # PYTHONDONTWRITEBYTECODE, so -B is required for both service processes.
    argv=([python,'-I','-B','-m','uvicorn','main:app','--app-dir',str(backend),'--host','0.0.0.0','--port','8080','--proxy-headers',f"--forwarded-allow-ips={child.get('JAX_TRUSTED_PROXIES','')}",'--no-access-log','--log-level','warning'] if mode=='web' else [python,'-I','-B','-c',"import runpy,sys;sys.path.insert(0,sys.argv[1]);runpy.run_module('db.migrate',run_name='__main__')",str(backend)] if mode == 'migrate' else [python,'-I','-B','-m','virtual_office.engine_activation_cli',*engine_args])
    if mode=='web' and not child['JAX_TRUSTED_PROXIES']: die()
    os.execve(python,argv,child)
if __name__ == '__main__': main()
