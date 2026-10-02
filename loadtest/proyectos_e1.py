"""Carga de Proyectos E1 (T10, 2026-10-02) -- ORQUESTADOR + SIEMBRA + MEDIDOR.

Mismo método que `historial_orquestar.py` (backend REAL de este repo, un solo
proceso uvicorn en 127.0.0.1, HTTP de punta a punta con httpx, siembra marcada y
limpieza SIEMPRE), con DOS diferencias deliberadas:

  * Las credenciales son las de la base de PRUEBA (`~/.config/jax/test-db.env`,
    o la ruta de `JAX_TEST_DB_ENV`), NUNCA `/etc/jax/.env`: no hace falta sudo y
    no hay forma de que esto llegue a `jax_memory`.
  * Los puertos son propios (18180 backend, 15173 vite) porque 18080 lo usan los
    guiones de historial: no se pisa una corrida ajena.

SUBCOMANDOS (desde la raíz del repo, con el venv del backend):
    medir     siembra, levanta, mide, hace EXPLAIN y limpia (la corrida de carga)
    visual    siembra, levanta backend + vite y espera (SIGTERM/Ctrl-C) para la
              revisión visual en el navegador; al salir limpia
    limpiar SUFIJO   borra lo sembrado por UNA corrida (el sufijo de 8 hex que imprime
              `[siembra]`); sin sufijo no borra nada

ENTORNO que hay que dar (igual que la suite):
    set -a; . ~/.config/jax/test-db.env; set +a
    JAX_REPO_PATH=/home/fruiz/jax JAX_CONFIG_PATH=/home/fruiz/jax/config/config.toml \\
    PYTHONPATH=/home/fruiz/jax python3 loadtest/proyectos_e1.py medir

LO QUE SE SIEMBRA (peor caso del brief): 1.000 proyectos, un usuario miembro de
300 de ellos (10 ARCHIVED, el resto ACTIVE; 1 de cada 30 como OWNER) y 40
usuarios extra como miembros del primer proyecto de ese usuario, para que
miembros y candidatos tengan algo que ordenar. Todo lleva el prefijo
`carga-e1-` (proyectos) o el dominio `example.invalid` (usuarios): la limpieza
borra SOLO eso.

LO QUE NO SE MIDE, y por qué: un turno de chat con proyecto. Exige un LLM real y
ningún loadtest del repo trae una faceta falsa de chat (`historial_fake_jacobs`
simula LAS MANOS, no un modelo). Se mide la parte de proyecto del turno: la
resolución de alcance (`ProjectScopeAuthorityResolver.resolve_scope`, lo que
`_scope_for_chat` llama antes de tocar el modelo). El costo del LLM no es de E1.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

LOADTEST_DIR = Path(__file__).parent
REPO_DIR = LOADTEST_DIR.parent
BACKEND_DIR = REPO_DIR / "backend"
FRONTEND_DIR = REPO_DIR / "frontend"

BASE_DE_PRUEBA = "jax_memory_test"
BACKEND_PORT = 18180
VITE_PORT = 15173
BACKEND_URL = f"http://127.0.0.1:{BACKEND_PORT}"
PUERTOS_DE_PRODUCCION = {7777, 8080, 5173, 3306}

# --- Siembra ---------------------------------------------------------------
N_PROYECTOS = 1000
N_DEL_USUARIO = 300          # idx % 10 < 3
CADA_N_OWNER = 30            # idx % 30 == 0 -> el usuario es OWNER (CREATOR)
CADA_N_ARCHIVADO = 100       # idx % 100 == 2 -> ARCHIVED (10 de los 300)
N_MIEMBROS_EXTRA = 40
PREFIJO_PROYECTO = "carga-e1-"
DOMINIO = "example.invalid"
PASSWORD_DE_PRUEBA = "carga-e1-prueba-1"   # solo para la base de prueba, nunca un secreto real
TENANT_ID = 1

# --- Medición --------------------------------------------------------------
NIVELES = [1, 10, 25, 50, 100, 200]
SEGUNDOS_POR_NIVEL = 20
CONCURRENCIA_OBJETIVO = 50
SEGUNDOS_OBJETIVO = 120
CONCURRENCIA_CHAT = 20
SEGUNDOS_CHAT = 30
# Criterio PROPIO de "se degrada" (no viene del spec): p95 > 250 ms, algún error,
# o el throughput deja de crecer (<5 % sobre el nivel anterior).
if os.environ.get("CARGA_RAPIDA"):   # solo para probar el arnés: números NO válidos
    NIVELES, SEGUNDOS_POR_NIVEL, SEGUNDOS_OBJETIVO, SEGUNDOS_CHAT = [1, 10], 3, 4, 3
P95_DEGRADADO_MS = 250.0
CRECIMIENTO_MINIMO = 1.05


def _cargar_env_de_prueba() -> dict:
    ruta = Path(os.environ.get("JAX_TEST_DB_ENV", Path.home() / ".config/jax/test-db.env"))
    env = {}
    for linea in ruta.read_text().splitlines():
        linea = linea.strip()
        if linea and not linea.startswith("#") and "=" in linea:
            k, _, v = linea.partition("=")
            env[k.strip()] = v.strip().strip('"').strip("'")
    if env.get("JAX_DB_NAME") != BASE_DE_PRUEBA:
        raise SystemExit(f"JAX_DB_NAME={env.get('JAX_DB_NAME')!r} != {BASE_DE_PRUEBA!r} -- ABORTANDO")
    return env


def _conectar():
    import pymysql
    env = _cargar_env_de_prueba()
    conn = pymysql.connect(host=env["JAX_DB_HOST"], port=int(env["JAX_DB_PORT"]), user=env["JAX_DB_USER"],
                           password=env["JAX_DB_PASSWORD"], database=BASE_DE_PRUEBA, autocommit=False,
                           charset="utf8mb4", connect_timeout=10)
    with conn.cursor() as cur:  # barrera dura, no un assert
        cur.execute("SELECT DATABASE()")
        (db,) = cur.fetchone()
    if db != BASE_DE_PRUEBA:
        raise RuntimeError(f"conectado a {db!r} -- ABORTANDO sin escribir nada")
    return conn


# ---------------------------------------------------------------------------
# Siembra y limpieza
# ---------------------------------------------------------------------------
def nuevo_sufijo() -> str:
    return uuid.uuid4().hex[:8]


def patrones_de_limpieza(sufijo: str | None) -> tuple[str, str]:
    """(patrón LIKE de proyectos, patrón LIKE de correos) de UNA corrida. Función pura.
    Sin sufijo válido (8 hex) no hay patrón: borrar por prefijo se llevaría la siembra de
    otra corrida (un `visual` abierto, otra sesión sobre la misma base de prueba)."""
    if not sufijo or not re.fullmatch(r"[0-9a-f]{8}", sufijo):
        raise ValueError(f"sufijo de corrida inválido: {sufijo!r} -- no se borra nada")
    return f"{PREFIJO_PROYECTO}{sufijo}-%", f"{PREFIJO_PROYECTO}%-{sufijo}@{DOMINIO}"


def sembrar(sufijo: str) -> dict:
    import bcrypt
    patrones_de_limpieza(sufijo)  # valida antes de escribir nada
    conn = _conectar()
    ahora = "2026-10-02 00:00:00.000000"
    pw = bcrypt.hashpw(PASSWORD_DE_PRUEBA.encode(), bcrypt.gensalt(rounds=4)).decode()

    def usuario(cur, correo):
        cur.execute("INSERT INTO jax_users (tenant_id, email, password_hash, role, status, token_version) "
                    "VALUES (%s, %s, %s, 'operator', 'active', 0)", (TENANT_ID, correo, pw))
        return cur.lastrowid

    with conn.cursor() as cur:
        miembro = usuario(cur, f"{PREFIJO_PROYECTO}miembro-{sufijo}@{DOMINIO}")
        dueno = usuario(cur, f"{PREFIJO_PROYECTO}dueno-{sufijo}@{DOMINIO}")
        extras = [usuario(cur, f"{PREFIJO_PROYECTO}u{i:02d}-{sufijo}@{DOMINIO}") for i in range(N_MIEMBROS_EXTRA)]
    conn.commit()

    ids, de_miembro = [], []
    with conn.cursor() as cur:
        for i in range(N_PROYECTOS):
            cur.execute("INSERT INTO projects (project_uuid, name, description, status) VALUES (%s, %s, %s, 'active')",
                        (str(uuid.uuid4()), f"{PREFIJO_PROYECTO}{sufijo}-{i:04d}", f"proyecto de carga {i}"))
            ids.append(cur.lastrowid)
    conn.commit()

    scopes, membresias = [], []
    for i, pid in enumerate(ids):
        archivado = i % CADA_N_ARCHIVADO == 2
        scopes.append((pid, TENANT_ID, "ARCHIVED" if archivado else "ACTIVE", ahora, f"user:{dueno}", ahora))
        membresias.append((str(uuid.uuid4()), pid, TENANT_ID, dueno, "OWNER", "CREATOR", ahora, f"user:{dueno}", ahora))
        if i % 10 < 3:
            de_miembro.append(pid)
            rol, origen = ("OWNER", "CREATOR") if i % CADA_N_OWNER == 0 else ("CONTRIBUTOR", "EXPLICIT")
            membresias.append((str(uuid.uuid4()), pid, TENANT_ID, miembro, rol, origen, ahora, f"user:{dueno}", ahora))
    proyecto_con_miembros = ids[0]
    for u in extras:
        membresias.append((str(uuid.uuid4()), proyecto_con_miembros, TENANT_ID, u, "VIEWER", "EXPLICIT", ahora,
                           f"user:{dueno}", ahora))
    with conn.cursor() as cur:
        cur.executemany("INSERT INTO jax_project_scope (project_id, tenant_id, status, created_at, created_by, updated_at) "
                        "VALUES (%s,%s,%s,%s,%s,%s)", scopes)
        cur.executemany("INSERT INTO jax_project_membership (membership_id, project_id, tenant_id, user_id, project_role, "
                        "status, grant_origin, created_at, created_by, updated_at) "
                        "VALUES (%s,%s,%s,%s,%s,'ACTIVE',%s,%s,%s,%s)",
                        [(m[0], m[1], m[2], m[3], m[4], m[5], m[6], m[7], m[8]) for m in membresias])
    conn.commit()
    conn.close()
    semilla = {"sufijo": sufijo, "user_id": miembro, "email": f"{PREFIJO_PROYECTO}miembro-{sufijo}@{DOMINIO}", "tenant_id": str(TENANT_ID),
               "dueno_id": dueno, "proyectos": len(ids), "del_usuario": len(de_miembro),
               "proyecto_con_miembros": proyecto_con_miembros, "proyecto_del_usuario": de_miembro[0],
               "proyecto_ajeno": next(p for p in ids if p not in set(de_miembro))}
    print(f"[siembra] {semilla}", file=sys.stderr)
    return semilla


def limpiar(sufijo: str | None) -> None:
    """Borra SOLO lo que sembró la corrida `sufijo`. Sin sufijo válido no se conecta ni borra."""
    pat_proyecto, pat_correo = patrones_de_limpieza(sufijo)
    conn = _conectar()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM projects WHERE name LIKE %s", (pat_proyecto,))
            ids = [r[0] for r in cur.fetchall()]
            cur.execute("SELECT user_id FROM jax_users WHERE email LIKE %s", (pat_correo,))
            usuarios = [r[0] for r in cur.fetchall()]
            for i in range(0, len(ids), 500):
                lote = ids[i:i + 500]
                ph = ",".join(["%s"] * len(lote))
                cur.execute(f"DELETE FROM jax_project_membership WHERE project_id IN ({ph})", lote)
                cur.execute(f"DELETE FROM jax_project_scope WHERE project_id IN ({ph})", lote)
                cur.execute(f"DELETE FROM projects WHERE id IN ({ph})", lote)
            if usuarios:
                ph = ",".join(["%s"] * len(usuarios))
                cur.execute(f"DELETE FROM jax_project_membership WHERE user_id IN ({ph})", usuarios)
                cur.execute(f"DELETE FROM jax_users WHERE user_id IN ({ph})", usuarios)
        conn.commit()
        print(f"[limpieza] {len(ids)} proyectos y {len(usuarios)} usuarios borrados", file=sys.stderr)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Procesos
# ---------------------------------------------------------------------------
def _puerto_libre(puerto: int) -> None:
    if puerto in PUERTOS_DE_PRODUCCION:
        raise RuntimeError(f"puerto {puerto} es de PRODUCCION -- ABORTANDO")
    with socket.socket() as s:
        if s.connect_ex(("127.0.0.1", puerto)) == 0:
            raise RuntimeError(f"puerto {puerto} ya está en uso (¿corrida ajena?) -- ABORTANDO, no se pisa")


def _esperar_puerto(puerto: int, timeout: float = 60.0) -> None:
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with socket.create_connection(("127.0.0.1", puerto), timeout=1):
                return
        except OSError:
            time.sleep(0.2)
    raise RuntimeError(f"127.0.0.1:{puerto} no respondió en {timeout}s")


def _env_del_backend(tmp: Path) -> dict:
    env = dict(os.environ)
    env.update(_cargar_env_de_prueba())
    for k in ("JAX_REPO_PATH", "JAX_CONFIG_PATH"):
        if not env.get(k):
            raise SystemExit(f"falta {k} en el entorno (ver la cabecera de este archivo)")
    env["JAX_JWT_SECRET"] = secrets.token_urlsafe(48)       # propia, nueva en cada corrida
    env["FERNET_KEY"] = base64.urlsafe_b64encode(os.urandom(32)).decode()
    env["JAX_LAS_MANOS_CREDENCIAL_PLATAFORMA"] = secrets.token_urlsafe(40)
    env["LAS_MANOS_URL"] = "http://127.0.0.1:17999"          # nada escucha: no hay Manos reales
    env["JACOBS_URL"] = "http://127.0.0.1:17999/jacobs"
    env["JAX_PLATFORM_URL"] = BACKEND_URL
    env["JAX_OLLAMA_URL"] = "http://ollama.invalid:11434"
    env["CANARY_INTERVAL_SECONDS"] = "0"
    env["JAX_USAGE_SPOOL_DIR"] = str(tmp / "usage-spool")
    env["JAX_FACET_SEAL_PATH"] = str(tmp / "facet-cache-seal")
    env["JAX_KILL_SWITCH_PATH"] = str(tmp / "interruptor" / "PAUSE")
    env["JAX_EJECUTOR_PAUSA"] = str(tmp / "ejecutor-pausa" / "PAUSA")
    env["JAX_EJECUTOR_PYTHON"] = str(tmp / "no-existe" / "python")
    env["JAX_REPO_BASE"] = str(tmp / "repo")
    env["JAX_AUDIT_LOG_PATH"] = str(tmp / "audit.jsonl")
    env["JAX_ADJUNTOS_DIR"] = str(tmp / "adjuntos")
    env["JAX_PROXY_CARRIL_RAIZ"] = str(tmp / "carril")
    env.update({
        "JAX_ADJUNTO_MAX_BYTES": "10485760", "JAX_ADJUNTO_MAX_CHARS": "8000", "JAX_ADJUNTO_MAX_PAGINAS": "20",
        "JAX_ADJUNTO_MAX_POR_MENSAJE": "1", "JAX_ADJUNTO_IMAGENES_EN_PROCESO": "1",
        "JAX_ADJUNTO_SUBIDAS_EN_PROCESO": "1", "JAX_ADJUNTO_PDF_PROCESOS": "1",
        "JAX_ADJUNTO_PDF_TIMEOUT_SEGUNDOS": "5", "JAX_ADJUNTOS_TTL_HORAS": "24",
        "JAX_ADJUNTOS_CUOTA_BYTES_USUARIO": "524288000", "JAX_ADJUNTOS_DISCO_LIBRE_MINIMO_BYTES": "1073741824",
        "JAX_ADJUNTOS_SUBIDAS_POR_MINUTO": "600", "JAX_ADJUNTOS_RECHAZO_ESPERA_MS": "0",
        "JAX_SEED_SUPERADMIN_EMAIL": "superadmin-semilla-carga@example.invalid",
        "JAX_SEED_TENANT_NAME": "Tenant de la carga de proyectos E1",
    })
    pp = [str(BACKEND_DIR)] + [p for p in [env.get("PYTHONPATH")] if p]
    env["PYTHONPATH"] = os.pathsep.join(pp)
    return env


def _levantar_backend(env: dict, tmp: Path):
    _puerto_libre(BACKEND_PORT)
    log = open(tmp / "backend.log", "w")
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port",
                             str(BACKEND_PORT), "--log-level", "warning"],
                            cwd=str(BACKEND_DIR), env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    try:
        _esperar_puerto(BACKEND_PORT)
    except Exception:
        proc.kill()
        print((tmp / "backend.log").read_text()[-3000:], file=sys.stderr)
        raise
    # VERIFICACIÓN DURA contra el proceso ya levantado, no contra el dict en memoria.
    pares = dict(p.split(b"=", 1) for p in Path(f"/proc/{proc.pid}/environ").read_bytes().split(b"\x00") if b"=" in p)
    if pares.get(b"JAX_DB_NAME", b"").decode() != BASE_DE_PRUEBA:
        raise RuntimeError("el backend levantado NO apunta a la base de prueba -- ABORTANDO")
    return proc, log


def _matar(proc) -> None:
    if proc is None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except ProcessLookupError:  # fail-soft: ya terminó solo
        return
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except ProcessLookupError:  # fail-soft: murió entre el timeout y el kill
            pass


def _token(env: dict, user_id) -> dict:
    from jose import jwt
    t = jwt.encode({"user_id": str(user_id), "tenant_id": str(TENANT_ID), "role": "operator", "tv": 0,
                    "exp": int(time.time()) + 7200, "type": "access"}, env["JAX_JWT_SECRET"], algorithm="HS256")
    return {"Authorization": f"Bearer {t}"}


# ---------------------------------------------------------------------------
# Medición
# ---------------------------------------------------------------------------
def percentil(valores, p):
    if not valores:
        return None
    o = sorted(valores)
    return o[max(0, min(len(o) - 1, int(round(p / 100.0 * len(o))) - 1))]


async def tanda(url: str, headers: dict, c: int, segundos: float) -> dict:
    import httpx
    limits = httpx.Limits(max_connections=c + 5, max_keepalive_connections=c + 5)
    lat, errores, codigos = [], 0, {}
    fin = time.perf_counter() + segundos
    async with httpx.AsyncClient(limits=limits, timeout=30.0) as cli:
        async def trabajador():
            nonlocal errores
            while time.perf_counter() < fin:
                t0 = time.perf_counter()
                try:
                    r = await cli.get(url, headers=headers)
                    ms = (time.perf_counter() - t0) * 1000
                    codigos[r.status_code] = codigos.get(r.status_code, 0) + 1
                    if r.status_code >= 400:
                        errores += 1
                    else:
                        lat.append(ms)
                except Exception:  # fail-soft: una petición caída cuenta como error y no tumba la corrida
                    errores += 1
        t0 = time.perf_counter()
        await asyncio.gather(*(trabajador() for _ in range(c)))
        dur = time.perf_counter() - t0
    total = len(lat) + errores
    return {"c": c, "segundos": round(dur, 1), "n": total, "errores": errores, "codigos": codigos,
            "rps": round(total / dur, 1), "p50_ms": round(percentil(lat, 50), 1) if lat else None,
            "p95_ms": round(percentil(lat, 95), 1) if lat else None,
            "p99_ms": round(percentil(lat, 99), 1) if lat else None, "max_ms": round(max(lat), 1) if lat else None}


def punto_de_degradacion(niveles: list[dict]) -> str:
    previo = None
    for r in niveles:
        motivos = []
        if r["errores"]:
            motivos.append(f"{r['errores']} errores")
        if r["p95_ms"] is not None and r["p95_ms"] > P95_DEGRADADO_MS:
            motivos.append(f"p95 {r['p95_ms']} ms > {P95_DEGRADADO_MS} ms")
        if previo and r["rps"] < previo["rps"] * CRECIMIENTO_MINIMO:
            motivos.append(f"rps {r['rps']} no crece sobre {previo['rps']} (saturación)")
        if motivos:
            return f"c={r['c']}: " + "; ".join(motivos)
        previo = r
    return f"no se degrada hasta c={niveles[-1]['c']} con este criterio"


def _hijo(modo: str, semilla: dict) -> dict:
    """Corre `medir_alcance_chat` o `explain` en un proceso aparte (su propio loop y pool)."""
    r = subprocess.run([sys.executable, str(Path(__file__)), modo, json.dumps(semilla)], capture_output=True,
                       text=True, timeout=600,
                       env=dict(os.environ, **_cargar_env_de_prueba(),
                                PYTHONPATH=os.pathsep.join([str(BACKEND_DIR), os.environ.get("PYTHONPATH", "")])))
    if r.returncode != 0:
        raise RuntimeError(f"{modo} falló:\n{r.stderr[-3000:]}")
    return json.loads(r.stdout.splitlines()[-1])


async def _medir_alcance_chat(semilla: dict) -> dict:
    from db.connection import get_pool
    from jax.memory.b9 import ScopeContext
    from jax.memory.scope_authority import ProjectScopeAuthorityResolver
    pool = await get_pool()
    resolver = ProjectScopeAuthorityResolver(pool)
    uid = semilla["user_id"]
    pid = semilla["proyecto_del_usuario"]
    lat, errores = [], 0
    fin = time.perf_counter() + SEGUNDOS_CHAT

    async def trabajador():
        nonlocal errores
        while time.perf_counter() < fin:
            alcance = ScopeContext(actor_principal=f"user:{uid}", actor_type="USER", subject_user_id=str(uid),
                                   tenant_id=str(TENANT_ID), project_id=str(pid),
                                   calling_component="jax-platform-web-chat")
            t0 = time.perf_counter()
            try:
                res = await resolver.resolve_scope(alcance)
                if str(res.project_id) != str(pid):
                    errores += 1
                    continue
                lat.append((time.perf_counter() - t0) * 1000)
            except Exception:  # fail-soft: cuenta como error de la corrida
                errores += 1
    t0 = time.perf_counter()
    await asyncio.gather(*(trabajador() for _ in range(CONCURRENCIA_CHAT)))
    dur = time.perf_counter() - t0
    return {"c": CONCURRENCIA_CHAT, "segundos": round(dur, 1), "n": len(lat) + errores, "errores": errores,
            "rps": round((len(lat) + errores) / dur, 1), "p50_ms": round(percentil(lat, 50), 1),
            "p95_ms": round(percentil(lat, 95), 1), "p99_ms": round(percentil(lat, 99), 1)}


async def _explain(semilla: dict) -> dict:
    """EXPLAIN de las consultas REALES de project_queries: se graban las sentencias que las funciones
    mandan de verdad (parche de Cursor.execute) y se les hace EXPLAIN con sus mismos parámetros."""
    import aiomysql
    from db.connection import get_pool
    from jax.memory import project_queries as q
    grabadas: list[tuple[str, str, tuple]] = []
    etiqueta = {"x": ""}
    original = aiomysql.Cursor.execute

    async def grabar(self, query, args=None):
        if query.lstrip().upper().startswith("SELECT"):
            grabadas.append((etiqueta["x"], query, tuple(args or ())))
        return await original(self, query, args)
    aiomysql.Cursor.execute = grabar
    uid, pid, owned, extras = semilla["user_id"], semilla["proyecto_del_usuario"], semilla["proyecto_con_miembros"], None
    pool = await get_pool()
    try:
        # El primero del usuario (idx 0) es OWNER con 40 miembros: sirve a miembros y candidatos.
        etiqueta["x"] = "lista (vista activos, 1ª página)"
        await q.list_projects_for_user(pool, tenant_id=TENANT_ID, user_id=uid, view=q.ProjectView.ACTIVOS,
                                       before_id=None, limit=51)
        etiqueta["x"] = "lista (2ª página, antes_de)"
        await q.list_projects_for_user(pool, tenant_id=TENANT_ID, user_id=uid, view=q.ProjectView.ACTIVOS,
                                       before_id=semilla["proyecto_del_usuario"] + 700, limit=51)
        etiqueta["x"] = "lista (vista archivados)"
        await q.list_projects_for_user(pool, tenant_id=TENANT_ID, user_id=uid, view=q.ProjectView.ARCHIVADOS,
                                       before_id=None, limit=51)
        etiqueta["x"] = "detalle"
        await q.get_project_for_user(pool, tenant_id=TENANT_ID, user_id=uid, project_id=pid)
        etiqueta["x"] = "miembros"
        await q.list_project_members(pool, tenant_id=TENANT_ID, user_id=uid, project_id=pid)
        etiqueta["x"] = "candidatos"
        await q.list_invite_candidates(pool, tenant_id=TENANT_ID, user_id=uid, project_id=pid, query="carga", limit=20)
    finally:
        aiomysql.Cursor.execute = original
    salida = []
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            for et, sql, args in grabadas:
                await cur.execute("EXPLAIN " + sql, args)
                cols = [d[0] for d in cur.description]
                filas = [dict(zip(cols, [None if v is None else str(v) for v in r])) for r in await cur.fetchall()]
                señales = sorted({s for f in filas for s in ("Using filesort", "Using temporary")
                                  if s in (f.get("Extra") or "")})
                salida.append({"consulta": et, "sql": " ".join(sql.split()), "params": [str(a) for a in args],
                               "plan": filas, "alertas": señales})
    return {"explain": salida}


async def medir() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="carga-proyectos-e1-"))
    sufijo = nuevo_sufijo()
    semilla = None
    proc = log = None
    try:
        semilla = sembrar(sufijo)
        env = _env_del_backend(tmp)
        proc, log = _levantar_backend(env, tmp)
        print(f"[orquestador] backend arriba pid={proc.pid} en {BACKEND_URL}", file=sys.stderr)
        import httpx
        h = _token(env, semilla["user_id"])
        r = httpx.get(f"{BACKEND_URL}/api/proyectos", headers=h, timeout=30)
        cuerpo = r.json()
        if not (r.status_code == 200 and len(cuerpo["proyectos"]) == 50 and cuerpo["siguiente"]):
            raise RuntimeError(f"verificación previa: se esperaban 50 proyectos y siguiente, llegó {r.status_code} "
                               f"{len(cuerpo.get('proyectos', []))}")
        print(f"[orquestador] verificación: {len(cuerpo['proyectos'])} proyectos, {len(r.content)} bytes", file=sys.stderr)

        res = {"semilla": {k: semilla[k] for k in ("proyectos", "del_usuario")}, "fecha": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
               "bytes_lista": len(r.content)}
        escalera = []
        for c in NIVELES:
            escalera.append(await tanda(f"{BACKEND_URL}/api/proyectos", h, c, SEGUNDOS_POR_NIVEL))
            print(f"[LISTA escalera] {escalera[-1]}", file=sys.stderr)
        res["lista_escalera"] = escalera
        res["lista_degradacion"] = punto_de_degradacion(escalera)
        res["lista_objetivo"] = await tanda(f"{BACKEND_URL}/api/proyectos", h, CONCURRENCIA_OBJETIVO, SEGUNDOS_OBJETIVO)
        print(f"[LISTA objetivo] {res['lista_objetivo']}", file=sys.stderr)
        for nombre, ruta in (("detalle", f"/api/proyectos/{semilla['proyecto_del_usuario']}"),
                             ("miembros", f"/api/proyectos/{semilla['proyecto_con_miembros']}/miembros")):
            res[nombre] = await tanda(f"{BACKEND_URL}{ruta}", h, CONCURRENCIA_OBJETIVO, SEGUNDOS_POR_NIVEL)
            print(f"[{nombre}] {res[nombre]}", file=sys.stderr)
        res["alcance_chat"] = _hijo("--alcance-chat", semilla)
        print(f"[alcance_chat] {res['alcance_chat']}", file=sys.stderr)
        res.update(_hijo("--explain", semilla))
        salida = LOADTEST_DIR / "_resultados_proyectos_e1.json"
        salida.write_text(json.dumps(res, indent=2, ensure_ascii=False))
        print(f"[orquestador] {salida} escrito")
    finally:
        _matar(proc)
        if log:
            log.close()
        try:
            limpiar(sufijo)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def visual() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="visual-proyectos-e1-"))
    proc = vite = log = None
    sufijo = nuevo_sufijo()
    parar = {"x": False}
    signal.signal(signal.SIGTERM, lambda *_: parar.update(x=True))
    signal.signal(signal.SIGINT, lambda *_: parar.update(x=True))
    try:
        semilla = sembrar(sufijo)
        env = _env_del_backend(tmp)
        proc, log = _levantar_backend(env, tmp)
        _puerto_libre(VITE_PORT)
        nodo = os.environ.get("NODE_BIN", str(Path.home() / ".nvm/versions/node/v24.16.0/bin"))
        venv = dict(os.environ, PATH=f"{nodo}:{os.environ['PATH']}", PROYECTOS_E1_BACKEND_URL=BACKEND_URL)
        vite = subprocess.Popen(["npx", "vite", "--config", "vite.proyectos-e1.config.js",
                                 "--host", "127.0.0.1", "--port", str(VITE_PORT), "--strictPort"],
                                cwd=str(FRONTEND_DIR), env=venv, stdout=open(tmp / "vite.log", "w"),
                                stderr=subprocess.STDOUT, start_new_session=True)
        _esperar_puerto(VITE_PORT, 90)
        print(f"\nURL:        http://127.0.0.1:{VITE_PORT}/proyectos\nUSUARIO:    {semilla['email']}\n"
              f"CONTRASEÑA: {PASSWORD_DE_PRUEBA}\nDETALLE:    /proyectos/{semilla['proyecto_con_miembros']} "
              f"(el usuario es OWNER, 40 miembros)\nBACKEND:    {BACKEND_URL}  (jax_memory_test)\n"
              f"Para terminar y limpiar: kill -TERM {os.getpid()}", flush=True)
        while not parar["x"]:
            time.sleep(0.5)
    finally:
        _matar(vite)
        _matar(proc)
        if log:
            log.close()
        try:
            limpiar(sufijo)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "medir"
    if cmd == "medir":
        asyncio.run(medir())
    elif cmd == "visual":
        visual()
    elif cmd == "limpiar":
        limpiar(sys.argv[2] if len(sys.argv) > 2 else None)
    elif cmd == "--alcance-chat":
        print(json.dumps(asyncio.run(_medir_alcance_chat(json.loads(sys.argv[2])))))
    elif cmd == "--explain":
        print(json.dumps(asyncio.run(_explain(json.loads(sys.argv[2])))))
    else:
        raise SystemExit("uso: proyectos_e1.py [medir|visual|limpiar]")
