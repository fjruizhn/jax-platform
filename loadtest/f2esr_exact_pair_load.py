"""Carga del chat web F2-D (2026-10-02) -- ORQUESTADOR.

Mide el costo PROPIO de la plataforma en el camino nuevo de F2-C/F2-D (proyeccion
gobernada de `api/governed_chat.py` + bandeja de salida durable de
`webchat_f2d/repository.py` y `transport.py`) con el patron de la casa
(`historial_orquestar.py`): backend REAL (`main:app`, un solo proceso uvicorn,
127.0.0.1:BACKEND_PORT -- la misma topologia que `jax-platform.service`), proveedor
de modelo FALSO por HTTP real (`chat_f2d_fake_ollama.py`, apuntado con
`JAX_OLLAMA_URL`; la faceta `jax_local` resuelve a transporte `ollama` en
`facet_binding`), `httpx.AsyncClient` real de punta a punta, base
`jax_memory_test_f2esr`.

ENDPOINT MEDIDO (el que usa el frontend: `api.post('/chat', ...)` con baseURL `/api`):
    POST /api/chat   {"message": ..., "facet": "jax_local"}

PEOR CASO RAZONABLE:
  - historial de conversacion LLENO: cada usuario virtual hace CALENTAMIENTO_TURNOS
    turnos reales antes de medir, hasta MAX_TURNS=20 (40 mensajes que viajan al
    proveedor en cada pedido, asistente de ~16 KB cada uno);
  - respuesta ASCII del proveedor de CARGA_RESPUESTA_CHARS (16 000 bytes de payload), un contrato
    sin claims => candidato no gobernado, que SI cruza F2-C y la bandeja F2-D completa
    (OUTPUT_PREPARED -> TRANSPORT_COMMITTING -> OUTPUT_COMMITTED_TO_TRANSPORT);
  - bandeja con N_RELLENO filas de relleno (miles) mas las propias de la corrida;
  - N_USUARIOS usuarios distintos (cada uno con su historial y su conversacion de
    memoria), un trabajador en bucle cerrado por usuario: concurrencia c = c usuarios
    escribiendo a la vez.

USO: all inputs are explicit.  `--db-env` is a mode-0600 file containing exactly
JAX_DB_HOST/JAX_DB_PORT/JAX_DB_USER/JAX_DB_PASSWORD/JAX_DB_NAME, closed to
127.0.0.1:13338/jax_memory_test_f2esr.  Each run also names a new `--output` file,
an exact platform/JAX SHA pair, a closed pair label, repetition, and 8000 or 16000
provider response bytes.  `--smoke` retains the real 50-user, two-turn warmup and
c=1 measurement while avoiding the full five-level duration.

It never reads `/etc/jax/.env`, starts no production service, and never targets
`jax_memory`, ports 7777/8080, or Ollama 11434.  A MariaDB named lock makes the
destructive seed/cleanup cycle exclusive; each result file records p95 above 500 ms
as diagnostic latency degradation.  Rejection is reserved for request errors,
degraded responses, or F2-D final outbox/lifecycle counts that do not match the
full request count.
"""
from __future__ import annotations

import asyncio
import argparse
import base64
import json
import logging
import os
import platform
import secrets
import signal
import subprocess
import sys
import tempfile
import time
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx
import pymysql

sys.path.insert(0, str(Path(__file__).parent))

import f2esr_exact_pair_explain as explain  # noqa: E402
import f2esr_exact_pair_seed as siembra  # noqa: E402

LOGGER = logging.getLogger(__name__)

def esperar_puerto(host, port, timeout=90):
    import socket
    until = time.time() + timeout
    while time.time() < until:
        try:
            with socket.create_connection((host, port), timeout=1): return
        except OSError: time.sleep(.2)
    raise RuntimeError(f"{host}:{port} did not become ready")

def esperar_http_ok(url, timeout=90):
    until = time.time() + timeout
    while time.time() < until:
        try:
            response = httpx.get(url, timeout=3)
            if response.status_code < 500: return response
        except httpx.HTTPError as exc:  # fail-soft: bounded readiness retry; timeout reports the startup failure.
            LOGGER.warning("HTTP readiness retry after %s", type(exc).__name__)
        time.sleep(.2)
    raise RuntimeError(f"{url} did not become ready")

def percentil(values, p):
    ordered = sorted(values)
    return ordered[max(0, (len(ordered) * p + 99) // 100 - 1)] if ordered else None

# ---------------------------------------------------------------------------
# CONSTANTES DE LA CORRIDA -- visibles aca, no enterradas en el cuerpo.
# ---------------------------------------------------------------------------
LOADTEST_DIR = Path(__file__).parent
BACKEND_DIR = LOADTEST_DIR.parent / "backend"

FAKE_OLLAMA_PORT = 17434
BACKEND_PORT = 18080
PUERTO_SIN_NADIE = 17777  # LAS MANOS / Jacobs de la carga: no escucha nada (no se usan: jax_local va por ollama)
BACKEND_URL = f"http://127.0.0.1:{BACKEND_PORT}"
FAKE_OLLAMA_URL = f"http://127.0.0.1:{FAKE_OLLAMA_PORT}"
SIN_NADIE_URL = f"http://127.0.0.1:{PUERTO_SIN_NADIE}"

PUERTOS_DE_PRODUCCION = {7777, 8080}   # LAS MANOS real / jax-platform real
PUERTO_OLLAMA_REAL = 11434
BASE_DE_PRUEBA = siembra.BASE_DE_PRUEBA
# El backend importa el nucleo JAX (F2-C/F2-D) desde aca, EN SOLO LECTURA: la carga no escribe nada ahi.
JAX_REPO_PATH_SOLO_LECTURA = None

FACETA = "jax_local"
RESPUESTA_CHARS = 16_000
MAX_TURNS = 20               # espejo de api/chat.py::MAX_TURNS -- el historial lleno
CALENTAMIENTO_TURNOS = MAX_TURNS
NIVELES_DE_CONCURRENCIA = [1, 5, 10, 25, 50]
PETICIONES_POR_NIVEL = {1: 100, 5: 150, 10: 200, 25: 375, 50: 600}  # multiplos de c

DB_ENV_KEYS = frozenset({"JAX_DB_HOST", "JAX_DB_PORT", "JAX_DB_USER", "JAX_DB_PASSWORD", "JAX_DB_NAME"})
DB_ENV_CERRADO = {"JAX_DB_HOST": "127.0.0.1", "JAX_DB_PORT": "13338", "JAX_DB_NAME": BASE_DE_PRUEBA}
PARES_GOBERNADOS = {
    "f2d": ("f2-c.renderer.2", "f2-c.domain.2"),
    "production-baseline": ("f2-c.renderer.2", "f2-c.domain.2"),
    "sr": ("f2-c.renderer.3", "f2-c.domain.5"),
}
SHA_COMPLETO = re.compile(r"^[0-9a-f]{40}$")


def _n_por_trabajador(c: int) -> int:
    return PETICIONES_POR_NIVEL[c] // c


def construir_env(tmp: Path, db_env: dict, jax_repo: Path, backend_dir: Path, jax_config: Path | None = None,
                  *, respuesta_chars: int = RESPUESTA_CHARS) -> dict:
    if set(db_env) != DB_ENV_KEYS or any(not db_env[key] for key in DB_ENV_KEYS):
        raise RuntimeError("isolated DB env must contain exactly the five database keys")
    if any(db_env[key] != value for key, value in DB_ENV_CERRADO.items()):
        raise RuntimeError("isolated DB env does not name the closed F2-E SR database")
    env = {k: v for k, v in os.environ.items() if k in {"PATH", "LANG", "LC_ALL", "TZ"}}
    env.update(db_env)
    env["JAX_DB_NAME"] = db_env["JAX_DB_NAME"]
    # Llave de firma PROPIA, nueva en cada corrida (ver historial_orquestar.construir_env):
    # un token minteado aca jamas vale contra produccion.
    env["JAX_JWT_SECRET"] = secrets.token_urlsafe(48)
    env["JAX_REPO_PATH"] = str(jax_repo.resolve())
    env["JAX_CONFIG_PATH"] = str((jax_config or jax_repo / "config.toml").resolve())
    env["JAX_OLLAMA_URL"] = FAKE_OLLAMA_URL
    env["LAS_MANOS_URL"] = SIN_NADIE_URL
    env["JACOBS_URL"] = f"{SIN_NADIE_URL}/jacobs"
    env["JAX_PLATFORM_URL"] = BACKEND_URL
    env["CANARY_INTERVAL_SECONDS"] = "0"
    env["JAX_LAS_MANOS_CREDENCIAL_PLATAFORMA"] = secrets.token_urlsafe(40)
    env["FERNET_KEY"] = base64.urlsafe_b64encode(os.urandom(32)).decode()
    env["JAX_USAGE_SPOOL_DIR"] = str(tmp / "usage-spool")
    env["JAX_FACET_SEAL_PATH"] = str(tmp / "facet-cache-seal")
    env["JAX_KILL_SWITCH_PATH"] = str(tmp / "interruptor" / "PAUSE")
    env["JAX_EJECUTOR_PAUSA"] = str(tmp / "ejecutor-pausa" / "PAUSA")
    env["JAX_EJECUTOR_PYTHON"] = str(tmp / "no-existe" / "python")
    env["JAX_REPO_BASE"] = str(tmp / "repo")
    env["JAX_AUDIT_LOG_PATH"] = str(tmp / "audit.jsonl")
    env["JAX_ADJUNTOS_DIR"] = str(tmp / "adjuntos")
    env["JAX_PROXY_CARRIL_RAIZ"] = str(tmp / "carril")
    for k, v in {
        "JAX_ADJUNTO_MAX_BYTES": "10485760", "JAX_ADJUNTO_MAX_CHARS": "8000",
        "JAX_ADJUNTO_MAX_PAGINAS": "20", "JAX_ADJUNTO_MAX_POR_MENSAJE": "1",
        "JAX_ADJUNTO_IMAGENES_EN_PROCESO": "1", "JAX_ADJUNTO_SUBIDAS_EN_PROCESO": "1",
        "JAX_ADJUNTO_PDF_PROCESOS": "1", "JAX_ADJUNTO_PDF_TIMEOUT_SEGUNDOS": "5",
        "JAX_ADJUNTOS_TTL_HORAS": "24", "JAX_ADJUNTOS_CUOTA_BYTES_USUARIO": "524288000",
        "JAX_ADJUNTOS_DISCO_LIBRE_MINIMO_BYTES": "1073741824",
        "JAX_ADJUNTOS_SUBIDAS_POR_MINUTO": "600", "JAX_ADJUNTOS_RECHAZO_ESPERA_MS": "0",
        "JAX_SEED_SUPERADMIN_EMAIL": "superadmin-semilla-carga-chat@example.invalid",
        "JAX_SEED_TENANT_NAME": "Tenant de la carga del chat F2-D",
    }.items():
        env[k] = v
    env["PYTHONPATH"] = str(backend_dir.resolve())
    env["CARGA_FAKE_OLLAMA_PORT"] = str(FAKE_OLLAMA_PORT)
    env["CARGA_RESPUESTA_CHARS"] = str(respuesta_chars)
    return env


def _verificar_no_apunta_a_produccion(env: dict) -> None:
    """Falla RUIDOSO antes de levantar nada si algo quedo apuntando a produccion. No confia
    en las constantes de arriba: vuelve a mirar los valores que de verdad iran al proceso."""
    prohibidos = PUERTOS_DE_PRODUCCION | {PUERTO_OLLAMA_REAL}
    for nombre, puerto in (("BACKEND_PORT", BACKEND_PORT), ("FAKE_OLLAMA_PORT", FAKE_OLLAMA_PORT),
                           ("PUERTO_SIN_NADIE", PUERTO_SIN_NADIE)):
        if puerto in prohibidos:
            raise RuntimeError(f"{nombre}={puerto} pisa un puerto REAL {sorted(prohibidos)} -- ABORTANDO.")
    if env.get("JAX_DB_NAME") != BASE_DE_PRUEBA:
        raise RuntimeError(
            f"JAX_DB_NAME={env.get('JAX_DB_NAME')!r} != {BASE_DE_PRUEBA!r} -- ABORTANDO, "
            f"esto NUNCA corre contra jax_memory.")
    for var in ("LAS_MANOS_URL", "JACOBS_URL", "JAX_PLATFORM_URL", "JAX_OLLAMA_URL"):
        url = urlparse(env.get(var, ""))
        if url.hostname != "127.0.0.1":
            raise RuntimeError(f"{var}={env.get(var)!r}: tiene que ser 127.0.0.1 -- ABORTANDO.")
        if url.port in prohibidos:
            raise RuntimeError(f"{var}={env.get(var)!r}: pisa un puerto REAL -- ABORTANDO.")


def cpu_ticks_de_stat(linea: str) -> int:
    """utime + stime (campos 14 y 15 de /proc/<pid>/stat). El nombre del proceso va entre
    parentesis y puede traer espacios y parentesis: se parte DESPUES del ultimo ')'."""
    campos = linea.rpartition(")")[2].split()
    return int(campos[11]) + int(campos[12])


def _cpu_s(pid: int) -> float:
    return cpu_ticks_de_stat(Path(f"/proc/{pid}/stat").read_text()) / os.sysconf("SC_CLK_TCK")


def _rss_mb(pid: int) -> float:
    for linea in Path(f"/proc/{pid}/status").read_text().splitlines():
        if linea.startswith("VmRSS:"):
            return round(int(linea.split()[1]) / 1024, 1)
    return 0.0


def resumir_tanda(*, c: int, n: int, latencias: list, errores: int, degradados: int,
                  segundos: float, codigos: dict, cpu_s: float, bytes_resp: list | None = None) -> dict:
    total = len(latencias) + errores
    redondeo = lambda v: round(v, 2) if v is not None else None  # noqa: E731
    return {
        "c": c, "n": n, "ok": len(latencias), "errores": errores, "degradados": degradados,
        "segundos": round(segundos, 3),
        "rps": round(total / segundos, 2) if segundos > 0 else None,
        "p50_ms": redondeo(percentil(latencias, 50)), "p95_ms": redondeo(percentil(latencias, 95)),
        "p99_ms": redondeo(percentil(latencias, 99)),
        "max_ms": redondeo(max(latencias)) if latencias else None,
        "codigos": codigos,
        "cpu_backend_s": round(cpu_s, 2),
        "cpu_ms_por_peticion": round(cpu_s * 1000 / total, 2) if total else None,
        "bytes_respuesta_promedio": round(sum(bytes_resp) / len(bytes_resp)) if bytes_resp else None,
    }


async def correr_tanda(tokens: list[str], c: int, por_trabajador: int, pid_backend: int,
                       etiqueta: str = "") -> dict:
    """c trabajadores en bucle cerrado, cada uno con SU usuario (su historial, su conversacion)."""
    limits = httpx.Limits(max_connections=c + 5, max_keepalive_connections=c + 5)
    latencias, codigos, bytes_resp = [], {}, []
    errores = degradados = 0
    async with httpx.AsyncClient(limits=limits, timeout=180.0) as cliente:
        async def trabajador(w: int):
            nonlocal errores, degradados
            headers = {"Authorization": f"Bearer {tokens[w]}"}
            for i in range(por_trabajador):
                cuerpo = {"message": f"Pregunta de carga {etiqueta}{w}-{i}: resumi los pendientes "
                                     f"del trimestre y propone prioridades.", "facet": FACETA}
                t0 = time.perf_counter()
                try:
                    r = await cliente.post(f"{BACKEND_URL}/api/chat", json=cuerpo, headers=headers)
                except Exception:  # fail-soft: una peticion de la carga que se cae (red, timeout) cuenta como error y no tumba la corrida
                    errores += 1
                    codigos[None] = codigos.get(None, 0) + 1
                    continue
                ms = (time.perf_counter() - t0) * 1000
                codigos[r.status_code] = codigos.get(r.status_code, 0) + 1
                if r.status_code != 200:
                    errores += 1
                    continue
                latencias.append(ms)
                bytes_resp.append(len(r.content))
                # 200 con texto de contingencia NO es exito: el camino gobernado devuelve 200 aun degradado.
                if b'"contract_degraded":false' not in r.content or b'"governed_plain":true' not in r.content:
                    degradados += 1

        cpu0 = _cpu_s(pid_backend)
        t0 = time.perf_counter()
        await asyncio.gather(*(trabajador(w) for w in range(c)))
        segundos = time.perf_counter() - t0
        cpu = _cpu_s(pid_backend) - cpu0
    r = resumir_tanda(c=c, n=c * por_trabajador, latencias=latencias, errores=errores,
                      degradados=degradados, segundos=segundos, codigos=codigos, cpu_s=cpu,
                      bytes_resp=bytes_resp)
    r["rss_backend_mb"] = _rss_mb(pid_backend)
    return r


def _estado_bandeja(conn, user_ids: list[int]) -> dict:
    """Lo que dejo la corrida en la bandeja: por estado y por contract_state, y eventos por fila."""
    marcas = ", ".join(["%s"] * len(user_ids))
    sujetos = [str(u) for u in user_ids]
    with conn.cursor() as cur:
        cur.execute(f"SELECT state, contract_state, COUNT(*) FROM governed_output_outbox "
                    f"WHERE subject_id IN ({marcas}) GROUP BY state, contract_state", sujetos)
        por_estado = [{"state": s, "contract_state": cs, "filas": n} for s, cs, n in cur.fetchall()]
        cur.execute(f"""SELECT n_ev, COUNT(*) FROM (
                          SELECT o.outbox_id, COUNT(e.event_id) n_ev FROM governed_output_outbox o
                          LEFT JOIN governed_output_lifecycle_events e ON e.outbox_id = o.outbox_id
                          WHERE o.subject_id IN ({marcas}) GROUP BY o.outbox_id) t GROUP BY n_ev""", sujetos)
        eventos = {int(k): int(v) for k, v in cur.fetchall()}
        cur.execute(f"SELECT COALESCE(AVG(LENGTH(response_payload)),0) FROM governed_output_outbox "
                    f"WHERE subject_id IN ({marcas})", sujetos)
        (payload_prom,) = cur.fetchone()
    conn.rollback()
    return {"por_estado": por_estado, "filas_por_n_eventos": eventos,
            "payload_promedio_bytes": round(float(payload_prom))}


def _entorno_de_la_corrida(conn, jax_repo: Path, *, args, core: dict) -> dict:
    def git(ruta: str, *args: str) -> str:
        r = subprocess.run(["git", "-c", f"safe.directory={ruta}", "-C", ruta, *args],
                           capture_output=True, text=True)
        return r.stdout.strip() or f"UNAVAILABLE({r.stderr.strip()[:60]})"
    with conn.cursor() as cur:
        cur.execute("SELECT VERSION()")
        (version,) = cur.fetchone()
        cur.execute("SHOW VARIABLES WHERE Variable_name IN ('max_connections','innodb_buffer_pool_size')")
        variables = dict(cur.fetchall())
    conn.rollback()
    return {
        "fecha": datetime.now().astimezone().isoformat(timespec="seconds"),
        "host": platform.node(), "cpus": os.cpu_count(), "python": platform.python_version(),
        "sha_harness": git(str(LOADTEST_DIR.parent), "rev-parse", "HEAD"),
        "sha_plataforma_esperado": args.expected_platform_sha,
        "sha_jax_esperado": args.expected_jax_sha,
        "sha_jax_repo_path": git(str(jax_repo), "rev-parse", "HEAD"),
        "label": args.label, "repetition": args.repetition,
        "core": core,
        "mariadb": version, "mariadb_variables": variables,
        "uvicorn_workers": 1, "pool_db_maxsize": 10,
        "respuesta_chars": RESPUESTA_CHARS, "max_turns": MAX_TURNS,
    }


def _verificar_binding_de_la_faceta(conn) -> None:
    """La carga solo es valida si la faceta resuelve a OLLAMA (el proveedor falso). Si
    alguien reconfiguro jax_memory_test, un chat podria salir a un proveedor real."""
    with conn.cursor() as cur:
        cur.execute("SELECT f.transport FROM facet f JOIN facet_binding b "
                    "ON b.facet_key = f.`key` AND b.role = 'primary' WHERE f.`key` = %s", (FACETA,))
        filas = cur.fetchall()
    conn.rollback()
    if [f[0] for f in filas] != ["ollama"]:
        raise RuntimeError(f"facet_binding de {FACETA} en {BASE_DE_PRUEBA} no resuelve a ollama: {filas!r}")


def _read_env_file(path: Path) -> dict:
    if not path.is_file() or (path.stat().st_mode & 0o777) != 0o600:
        raise RuntimeError("--db-env must be a 0600 isolated env file")
    values = {}
    for line in path.read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise RuntimeError("--db-env has an invalid line")
        key, value = line.split("=", 1)
        if key in values:
            raise RuntimeError("--db-env repeats a database key")
        values[key] = value
    if set(values) != DB_ENV_KEYS or any(not values[key] for key in DB_ENV_KEYS):
        raise RuntimeError("--db-env must contain exactly the five database keys")
    if any(values[key] != value for key, value in DB_ENV_CERRADO.items()):
        raise RuntimeError("--db-env must target 127.0.0.1:13338/jax_memory_test_f2esr")
    return values

def _sha(path: Path) -> str:
    return subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True).strip()


def _comprobar_sha_limpio(path: Path, esperado: str, nombre: str) -> str:
    if not SHA_COMPLETO.fullmatch(esperado):
        raise RuntimeError(f"--expected-{nombre}-sha must be an exact 40-character lowercase SHA")
    actual = _sha(path)
    if actual != esperado:
        raise RuntimeError(f"{nombre} SHA differs from its required exact checkout")
    dirty = subprocess.check_output(["git", "-C", str(path), "status", "--porcelain"], text=True)
    if dirty:
        raise RuntimeError(f"{nombre} checkout is not clean")
    return actual


def _preflight_core(backend_dir: Path, env: dict, label: str) -> dict:
    """Ask the imported production bridge itself for both module origins and versions.

    This runs in a fresh process using the exact backend subprocess environment, so a
    stale module from this harness process cannot make a pair look valid.
    """
    expected = PARES_GOBERNADOS[label]
    probe = """
import json
import pathlib
from api.governed_chat import _core, _lifecycle_core
parts = _core()
lifecycle = _lifecycle_core()
from policy.governance import governed_domain, governed_renderer
print(json.dumps({
  'renderer': governed_domain.GOVERNED_RENDERER_API_VERSION,
  'domain': governed_domain.GOVERNED_DOMAIN_SPEC_VERSION,
  'lifecycle': lifecycle.OUTPUT_LIFECYCLE_API_VERSION,
  'origins': {
    'governed_chat': str(pathlib.Path(__import__('api.governed_chat', fromlist=['x']).__file__).resolve()),
    'governed_renderer': str(pathlib.Path(governed_renderer.__file__).resolve()),
    'governed_domain': str(pathlib.Path(governed_domain.__file__).resolve()),
    'output_lifecycle': str(pathlib.Path(lifecycle.__file__).resolve()),
  },
  'core_arity': len(parts),
}))
"""
    result = subprocess.run([sys.executable, "-c", probe], cwd=backend_dir, env=env,
                            capture_output=True, text=True, check=False)
    if result.returncode:
        raise RuntimeError(f"F2 core preflight failed: {result.stderr.strip()[-800:]}")
    try:
        evidence = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("F2 core preflight did not emit JSON evidence") from exc
    if (evidence.get("renderer"), evidence.get("domain")) != expected:
        raise RuntimeError(f"{label} requires F2-C {expected!r}, received "
                           f"{(evidence.get('renderer'), evidence.get('domain'))!r}")
    root = Path(env["JAX_REPO_PATH"]).resolve()
    if evidence.get("lifecycle") != "f2-d.lifecycle.2" or evidence.get("core_arity") != 8:
        raise RuntimeError("F2 core/lifecycle version preflight failed")
    for origin in evidence.get("origins", {}).values():
        if not Path(origin).resolve().is_relative_to(root) and origin != str((backend_dir / "api" / "governed_chat.py").resolve()):
            raise RuntimeError(f"F2 core loaded outside its paired checkout: {origin}")
    return evidence


async def main_async(args) -> None:
    global RESPUESTA_CHARS
    RESPUESTA_CHARS = args.chars
    if args.output.exists():
        raise RuntimeError("--output must name a new, unique result file")
    if not args.output.parent.is_dir():
        raise RuntimeError("--output parent must already exist")
    tmp = Path(tempfile.mkdtemp(prefix="carga-chat-f2d-"))
    seed_json = tmp / "seed_result.json"
    niveles = [1] if args.smoke else NIVELES_DE_CONCURRENCIA
    calentamiento = 2 if args.smoke else CALENTAMIENTO_TURNOS

    db_env = _read_env_file(args.db_env)
    _comprobar_sha_limpio(args.backend_dir.parent, args.expected_platform_sha, "platform")
    _comprobar_sha_limpio(args.jax_repo, args.expected_jax_sha, "jax")
    env = construir_env(tmp, db_env, args.jax_repo, args.backend_dir, args.jax_config,
                         respuesta_chars=args.chars)
    _verificar_no_apunta_a_produccion(env)
    core = _preflight_core(args.backend_dir, env, args.label)

    conn = siembra.conectar(env)
    siembra.preparar_corrida(conn)
    try:
        _verificar_binding_de_la_faceta(conn)
        health_base = siembra.health_base(conn)
    except Exception:
        siembra.liberar_exclusion(conn)
        conn.close()
        raise
    t_inicio = time.time()

    proc_fake = proc_backend = None
    log_fake = open(tmp / "fake_ollama.log", "w")
    log_backend = open(tmp / "backend.log", "w")
    resultados: dict = {}
    try:
        print("[orquestador] sembrando usuarios y relleno de la bandeja (chat_f2d_siembra.py)")
        seed = siembra.sembrar(conn)
        seed_json.write_text(json.dumps(seed))
        user_ids = seed["user_ids"]
        resultados["entorno"] = _entorno_de_la_corrida(conn, args.jax_repo, args=args, core=core)

        proc_fake = subprocess.Popen(
            [sys.executable, str(LOADTEST_DIR / "f2esr_fake_ollama.py")],
            env=env, stdout=log_fake, stderr=subprocess.STDOUT, start_new_session=True)
        esperar_puerto("127.0.0.1", FAKE_OLLAMA_PORT)
        print(f"[orquestador] ollama falso arriba, pid={proc_fake.pid}")

        proc_backend = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1",
             "--port", str(BACKEND_PORT), "--log-level", "warning"],
            cwd=str(args.backend_dir), env=env, stdout=log_backend, stderr=subprocess.STDOUT,
            start_new_session=True)
        esperar_puerto("127.0.0.1", BACKEND_PORT, timeout=90.0)
        r = esperar_http_ok(f"{BACKEND_URL}/api/health", timeout=90.0)
        print(f"[orquestador] backend arriba, pid={proc_backend.pid}, /api/health -> {r.status_code}")

        # VERIFICACION DURA: el proceso REAL, no el `env` en memoria.
        environ = Path(f"/proc/{proc_backend.pid}/environ").read_bytes()
        pares = dict(p.split(b"=", 1) for p in environ.split(b"\x00") if b"=" in p)
        real = {k.decode(): pares.get(k, b"").decode()
                for k in (b"JAX_DB_NAME", b"JAX_OLLAMA_URL", b"JAX_REPO_PATH")}
        if (real["JAX_DB_NAME"] != db_env["JAX_DB_NAME"] or real["JAX_OLLAMA_URL"] != FAKE_OLLAMA_URL
                or real["JAX_REPO_PATH"] != str(args.jax_repo.resolve())):
            raise RuntimeError(f"proceso real con entorno inesperado {real} -- ABORTANDO")
        print(f"[orquestador] VERIFICADO /proc/{proc_backend.pid}/environ: {real}")
        jwt_secret = pares.get(b"JAX_JWT_SECRET", b"").decode()
        if not jwt_secret or jwt_secret != env["JAX_JWT_SECRET"]:
            raise RuntimeError("backend did not retain the isolated JWT secret")

        from jose import jwt as _jwt
        ahora = int(time.time())
        tokens = [_jwt.encode({"user_id": str(u), "tenant_id": "1", "role": "operator", "tv": 0,
                               "exp": ahora + 4 * 3600, "type": "access"}, jwt_secret, algorithm="HS256")
                  for u in user_ids]

        # --- Verificacion previa: el camino real de punta a punta, no un literal (leccion de U5) ---
        rp = httpx.post(f"{BACKEND_URL}/api/chat", headers={"Authorization": f"Bearer {tokens[0]}"},
                        json={"message": "Pregunta de verificacion previa.", "facet": FACETA}, timeout=60.0)
        cuerpo = rp.json()
        stats = httpx.get(f"{FAKE_OLLAMA_URL}/_stats", timeout=5.0).json()
        print(f"[orquestador] verificacion previa: status={rp.status_code} bytes={len(rp.content)} "
              f"contract_state={cuerpo.get('contract_state')} degraded={cuerpo.get('contract_degraded')} "
              f"governed_plain={cuerpo.get('governed_plain')} chars={len(cuerpo.get('response', ''))} "
              f"stats_proveedor_falso={stats}")
        if not (rp.status_code == 200 and cuerpo.get("governed_plain") is True
                and cuerpo.get("contract_degraded") is False and cuerpo.get("response_id")
                and len(cuerpo.get("response", "")) >= RESPUESTA_CHARS * 0.9 and stats["chat"] == 1):
            raise RuntimeError("la verificacion previa no dio el camino gobernado esperado "
                               f"(cuerpo: {str(cuerpo)[:300]!r})")
        fila = None
        for _ in range(50):  # el OUTPUT_COMMITTED_TO_TRANSPORT se escribe DESPUES de enviar el cuerpo
            with conn.cursor() as cur:
                cur.execute("SELECT state, (SELECT COUNT(*) FROM governed_output_lifecycle_events e "
                            "WHERE e.outbox_id = o.outbox_id) FROM governed_output_outbox o "
                            "WHERE response_id=%s", (cuerpo["response_id"],))
                fila = cur.fetchone()
            conn.rollback()
            if fila and fila[0] == "OUTPUT_COMMITTED_TO_TRANSPORT":
                break
            time.sleep(0.1)
        print(f"[orquestador] bandeja para response_id={cuerpo['response_id'][:8]}...: {fila}")
        if fila != ("OUTPUT_COMMITTED_TO_TRANSPORT", 3):
            raise RuntimeError(f"la bandeja no registro el ciclo completo: {fila!r}")

        resultados["verificacion_previa"] = {
            "bytes": len(rp.content), "contract_state": cuerpo.get("contract_state"),
            "chars_respuesta": len(cuerpo["response"]), "bandeja": list(fila)}

        # --- Calentamiento: llena el historial de CADA usuario (MAX_TURNS=20), por HTTP real ---
        n_u = len(tokens)
        print(f"[orquestador] calentamiento: {n_u} usuarios x {calentamiento} turnos "
              f"(historial de {min(calentamiento, MAX_TURNS) * 2} mensajes por usuario)")
        resultados["calentamiento"] = await correr_tanda(
            tokens, n_u, calentamiento, proc_backend.pid, etiqueta="calentamiento-")
        print(f"[CALENTAMIENTO] {resultados['calentamiento']}")

        resultados["niveles"] = []
        for c in niveles:
            por_trab = _n_por_trabajador(c) if not args.smoke else 5
            r = await correr_tanda(tokens, c, por_trab, proc_backend.pid)
            print(f"[CHAT] c={c} n={r['n']} -> {r}")
            resultados["niveles"].append(r)

        stats = httpx.get(f"{FAKE_OLLAMA_URL}/_stats", timeout=5.0).json()
        resultados["proveedor_falso_stats"] = stats
        # Dejar que terminen los post-commit en segundo plano (shadow validation, memoria)
        await asyncio.sleep(3.0)
        esperado_final = 1 + (len(tokens) * calentamiento) + sum(r["n"] for r in resultados["niveles"])
        # F2-D commits after terminal send.  Give that bounded tail time to finish,
        # then validate every response row rather than accepting a partial snapshot.
        for _ in range(50):
            resultados["bandeja_al_final"] = _estado_bandeja(conn, user_ids)
            if resultados["bandeja_al_final"]["filas_por_n_eventos"] == {3: esperado_final}:
                break
            await asyncio.sleep(0.1)
        print(f"[orquestador] bandeja al final: {resultados['bandeja_al_final']}")
        resultados["explain"] = explain.explicar(conn)
        for e in resultados["explain"][1:]:
            print(f"[EXPLAIN] {e['consulta']}: banderas={e['banderas']} plan="
                  f"{[(p.get('key'), p.get('type'), p.get('rows'), p.get('Extra')) for p in e['plan']]}")
        lineas_log = (tmp / "backend.log").read_text(errors="replace").splitlines()
        resultados["log_backend"] = {
            "tracebacks": sum(1 for x in lineas_log if x.startswith("Traceback")),
            "deadlocks_1213": sum(1 for x in lineas_log if x.startswith("DB error") and "1213" in x),
            "otras_lineas_DB_error": sum(1 for x in lineas_log if x.startswith("DB error") and "1213" not in x),
            "lineas_ERROR_de_logging": sum(1 for x in lineas_log if x.startswith("ERROR")),
        }
        print(f"[orquestador] log del backend: {resultados['log_backend']}")

        lotes = [resultados["calentamiento"], *resultados["niveles"]]
        fallos = []
        degradacion_latencia = []
        for lote in lotes:
            lote["latency_degraded"] = lote["p95_ms"] is not None and lote["p95_ms"] > 500
            if lote["latency_degraded"]:
                degradacion_latencia.append({"fase": "warmup" if lote is resultados["calentamiento"] else "measurement",
                                             "c": lote["c"], "p95_ms": lote["p95_ms"]})
            if lote["errores"] or lote["degradados"]:
                fallos.append(f"c={lote['c']} errors={lote['errores']} degraded={lote['degradados']}")
        estados = resultados["bandeja_al_final"]
        if estados["por_estado"] != [{"state": "OUTPUT_COMMITTED_TO_TRANSPORT", "contract_state": "VALID", "filas": esperado_final}]:
            fallos.append(f"outbox states={estados['por_estado']!r}, expected {esperado_final} VALID committed")
        if estados["filas_por_n_eventos"] != {3: esperado_final}:
            fallos.append(f"outbox lifecycle events={estados['filas_por_n_eventos']!r}, expected 3 per row")
        resultados["criterio"] = {
            "p95_ms_maximo": 500,
            "errores_requeridos": 0,
            "degradados_requeridos": 0,
            "outbox_filas_esperadas": esperado_final,
            "resultado": "ACCEPTED" if not fallos else "REJECTED",
            "fallos": fallos,
            "latency_degraded": degradacion_latencia,
            "primer_umbral_degradado": degradacion_latencia[0] if degradacion_latencia else None,
        }
        salida = args.output
        salida.write_text(json.dumps(resultados, indent=2, ensure_ascii=False, default=str))
        print(f"[orquestador] {salida} escrito")
        if fallos:
            raise RuntimeError("invalid load measurement: " + "; ".join(fallos))

    finally:
        for proc in (proc_backend, proc_fake):
            if proc is None:
                continue
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except ProcessLookupError:  # fail-soft: el grupo ya no existe (termino solo) -- nada que matar
                pass
        for proc in (proc_backend, proc_fake):
            if proc is None:
                continue
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except ProcessLookupError:  # fail-soft: el SIGTERM lo tumbo entre el timeout y este SIGKILL
                    pass
        log_fake.close()
        log_backend.close()
        print("[orquestador] procesos detenidos")

        # LIMPIEZA SIEMPRE, por marcadores (funciona aunque la siembra se cortara a la mitad).
        print("[orquestador] limpiando la siembra (chat_f2d_siembra.limpiar)...")
        try:
            conn.rollback()
        except pymysql.Error:  # fail-soft: la conexion pudo morir con la corrida; se abre otra para limpiar
            conn = siembra.conectar(env)
        try:
            borrado = siembra.limpiar(conn, health_base)
            print(f"[orquestador] borrado: {borrado}")
        finally:
            siembra.liberar_exclusion(conn)
            conn.close()
        print(f"[orquestador] duracion total {time.time() - t_inicio:.0f}s; logs en {tmp}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-env", type=Path, required=True)
    parser.add_argument("--jax-repo", type=Path, required=True)
    parser.add_argument("--backend-dir", type=Path, required=True)
    parser.add_argument("--jax-config", type=Path, required=True)
    parser.add_argument("--expected-platform-sha", required=True)
    parser.add_argument("--expected-jax-sha", required=True)
    parser.add_argument("--label", choices=sorted(PARES_GOBERNADOS), required=True)
    parser.add_argument("--repetition", type=int, required=True)
    parser.add_argument("--chars", choices=(8000, 16000), type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--smoke", action="store_true")
    asyncio.run(main_async(parser.parse_args()))
