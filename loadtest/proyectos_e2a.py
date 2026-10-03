"""Carga de Proyectos E2a (T12, 2026-10-03) -- ORQUESTADOR + SIEMBRA + MEDIDOR.

Mismo método que `proyectos_e1.py` (que se IMPORTA y se reutiliza: base de prueba propia,
credenciales de `~/.config/jax/test-db.env`, backend REAL de este repo en un uvicorn propio en
127.0.0.1, HTTP de punta a punta, limpieza SIEMPRE), con estas diferencias:

  * Puertos propios (18280 backend, 15273 vite, 18281 LAS MANOS falso): no chocan con los de
    E1 (18180/15173) ni con los guiones de historial (18080).
  * La base es PROPIA de la corrida (`jax_memory_test_<sufijo>`, que E1 llama «peor-caso»): el
    clonador de la plantilla se niega por sus 5 triggers (BaseDeTestInvalida), y las pruebas
    escriben tablas append-only. Se crea, se arma por los caminos de la suite y se ELIMINA entera.
    El nombre lo valida `proyectos_e1._verificar_nombre_propio`: jamás `jax_memory`.
  * El workspace (`JAX_WORKSPACE_DIR`) es un directorio temporal: nunca `~/jax-workspace`.
  * LAS MANOS es FALSO (`ManosFalsas`, un servidor HTTP mínimo en un puerto propio): no hay GPU,
    ni OCR, ni datos reales. El despachador REAL (T7) corre contra él.

SUBCOMANDOS (desde la raíz del repo, con el venv del backend):
    medir      la corrida de carga de punta a punta (subida de LACTOVI con 20 chats, 10 subidas
               simultáneas, EXPLAIN sobre 10.000 filas). Escribe `_resultados_proyectos_e2a.json` en
               ~/.cache/jax-loadtest/ (o en $JAX_LOADTEST_RESULTADOS_DIR), fuera del repo
    reprocesar el escenario de «Reprocesar» (jax-platform#186): un fuente/ de 20.000 archivos en 2.000
               subcarpetas donde la ficha NO sirve y hay que buscar por sha256, 10 usuarios reprocesando
               a la vez con el cupo por defecto (los 429 son lo esperado), 20 usuarios de chat y 5 de la
               lista de documentos en paralelo. Escribe `_resultados_proyectos_e2a_reprocesar.json` junto
               al de `medir`.
    visual     base propia + backend + LAS MANOS falso + vite, y espera (SIGTERM/Ctrl-C) para la
               revisión visual; al salir limpia
    limpiar SUFIJO   elimina la base propia de UNA corrida (el sufijo de 8 hex que imprime
               `[base]`), por si una corrida murió sin limpiar

ENTORNO que hay que dar (igual que la suite):
    set -a; . ~/.config/jax/test-db.env; set +a
    JAX_REPO_PATH=/home/fruiz/worktrees/jax-proyectos-e2a \\
    JAX_CONFIG_PATH=/home/fruiz/worktrees/jax-proyectos-e2a/config/config.toml \\
    PYTHONPATH=/home/fruiz/worktrees/jax-proyectos-e2a python3 loadtest/proyectos_e2a.py medir

LOTE: 120 archivos con los TAMAÑOS de `~/jax-workspace/proyectos/lacteos-victoria/fuente/`
(se leen con `lstat`; el contenido NUNCA se abre ni se copia), con bytes aleatorios y extensiones
aceptadas por la plataforma.

CHAT EN PARALELO: el turno real llama a un modelo y no se llama a ninguno. Como E1, se mide la
parte de PLATAFORMA del turno con la autorización de proyecto: `ProjectScopeAuthorityResolver
.resolve_scope` + `MariaDBB9Reader.retrieve_authorized` (lo que corre `api/chat.py` antes y justo
antes de tocar el modelo), directo contra la base; y, además, `GET /api/proyectos/{id}` por el
MISMO uvicorn que recibe la subida (la autorización de proyecto de E1 pasando por el event loop y
el pool que comparte con la subida). Un «turno» son las dos cosas seguidas, 20 usuarios distintos
en bucle cerrado, sin tiempo de pensar (más duro que un usuario real). NO se mide: guardar el
mensaje en la memoria (`save_message`, fire-and-forget hacia el motor de memoria, necesita
embeddings) ni el costo del modelo.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import signal
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

LOADTEST_DIR = Path(__file__).parent
# Donde escribe `medir` sus resultados: FUERA del repo (ronda final, menor 11), para que una
# corrida no deje archivos sin seguimiento en el arbol de trabajo. Por defecto
# ~/.cache/jax-loadtest/; otra carpeta con esta variable.
VARIABLE_RESULTADOS = "JAX_LOADTEST_RESULTADOS_DIR"
ARCHIVO_RESULTADOS = "_resultados_proyectos_e2a.json"


def ruta_de_resultados(entorno=None) -> Path:
    entorno = os.environ if entorno is None else entorno
    carpeta = Path(entorno.get(VARIABLE_RESULTADOS) or Path.home() / ".cache" / "jax-loadtest")
    carpeta.mkdir(parents=True, exist_ok=True)
    return carpeta / ARCHIVO_RESULTADOS
sys.path.insert(0, str(LOADTEST_DIR))

import proyectos_e1 as e1  # noqa: E402  (reutiliza la base de prueba, el token y las utilidades de E1)

REPO_DIR = e1.REPO_DIR
BACKEND_DIR = e1.BACKEND_DIR
FRONTEND_DIR = e1.FRONTEND_DIR

BACKEND_PORT = 18280
VITE_PORT = 15273
MANOS_PORT = 18281
BACKEND_URL = f"http://127.0.0.1:{BACKEND_PORT}"
PREFIJO = "carga-e2a-"
DOMINIO = e1.DOMINIO
PASSWORD_DE_PRUEBA = "carga-e2a-prueba-1"   # solo para la base de prueba, nunca un secreto real
TENANT_ID = e1.TENANT_ID
ENCABEZADO_CREDENCIAL = "X-Jax-Credencial-Servicio"   # el mismo de credencial_las_manos.ENCABEZADO

FUENTE_LACTOVI = Path.home() / "jax-workspace" / "proyectos" / "lacteos-victoria" / "fuente"
WORKSPACE_REAL = Path.home() / "jax-workspace"

# --- Escenario -----------------------------------------------------------------------------------
USUARIOS_CHAT = 20
PEQUENOS = 10                    # subidas simultáneas, a proyectos distintos
RONDAS_PEQUENAS = 5
ARCHIVOS_POR_LOTE_PEQUENO = 4
SEGUNDOS_ANTES = 30              # chat solo, antes de subir
SEGUNDOS_DESPUES = 15            # chat solo, tras el último estado terminal
REPETICIONES_GRANDE = 3          # el lote de LACTOVI se sube varias veces (cada una con bytes nuevos)
SEGUNDOS_ENTRE_REPETICIONES = 5
RETRASO_MANOS_S = float(os.environ.get("E2A_RETRASO_MANOS_S", "3"))
FILAS_EXPLAIN = 10_000
TOPE_RUTAS_POR_TRABAJO_MANOS = 50
if os.environ.get("CARGA_RAPIDA"):   # solo para probar el arnés: números NO válidos
    SEGUNDOS_ANTES, SEGUNDOS_DESPUES, RONDAS_PEQUENAS, REPETICIONES_GRANDE, SEGUNDOS_ENTRE_REPETICIONES = 4, 3, 1, 2, 2

# --- Escenario «Reprocesar» ------------------------------------------------------------------------
ARCHIVOS_FUENTE = 20_000
SUBCARPETAS_FUENTE = 2_000
TAMANO_FUENTE = 4096            # todos los rellenos pesan LO MISMO que el objetivo: el prefiltro por tamano no ayuda
PROFUNDIDAD_OBJETIVO = 8
USUARIOS_REPROCESAR = 10
CLIENTES_LISTA = 5
SEGUNDOS_ANTES_R, SEGUNDOS_REPROCESAR, SEGUNDOS_DESPUES_R = 20, 60, 10
# Pausa de quien recibe un 429 antes de volver a pedir. 0,01 s = el peor caso (un cliente que insiste sin descanso);
# 1 s = un cliente que respeta el rechazo. `E2A_PAUSA_429_S` la cambia.
PAUSA_TRAS_429_S = float(os.environ.get("E2A_PAUSA_429_S", "0.01"))
if os.environ.get("CARGA_RAPIDA"):   # solo para probar el arnés: números NO válidos
    ARCHIVOS_FUENTE, SUBCARPETAS_FUENTE, SEGUNDOS_ANTES_R, SEGUNDOS_REPROCESAR, SEGUNDOS_DESPUES_R = 600, 60, 3, 6, 3

ESTADOS_TERMINALES = ("listo", "parcial", "error", "sin_extractor", "cancelado")


# ---------------------------------------------------------------------------
# Puras (con pruebas en test_proyectos_e2a.py)
# ---------------------------------------------------------------------------
def tamanos_de_referencia(carpeta: Path) -> list[int]:
    """Tamaños (bytes) de los archivos regulares bajo `carpeta`, por `lstat`: NO abre ni lee ningún
    archivo y no sigue enlaces."""
    import stat as _stat
    tamanos = []
    for raiz, _dirs, archivos in os.walk(carpeta, followlinks=False):
        for nombre in archivos:
            st = os.lstat(os.path.join(raiz, nombre))
            if _stat.S_ISREG(st.st_mode):
                tamanos.append(st.st_size)
    return tamanos


def plan_de_lote(tamanos: list[int], extensiones) -> list[tuple[str, int]]:
    """[(nombre, bytes)] con los mismos tamaños, extensiones aceptadas (en rotación, en orden fijo) y
    nombres únicos. Determinista."""
    exts = sorted(extensiones)
    if not exts:
        raise ValueError("sin extensiones aceptadas no hay lote que armar")
    return [(f"documento-{i:03d}{exts[i % len(exts)]}", b) for i, b in enumerate(tamanos)]


def escribir_lote(carpeta: Path, plan: list[tuple[str, int]]) -> list[Path]:
    """Escribe cada archivo del plan con bytes ALEATORIOS (sha256 distinto en cada uno: nada es un
    duplicado) y del tamaño exacto. Devuelve las rutas, en el orden del plan."""
    carpeta.mkdir(parents=True, exist_ok=True)
    rutas = []
    for nombre, total in plan:
        ruta = carpeta / nombre
        with open(ruta, "wb") as f:
            restante = total
            while restante > 0:
                bloque = min(restante, 4 * 1024 * 1024)
                f.write(os.urandom(bloque))
                restante -= bloque
        rutas.append(ruta)
    return rutas


def por_fase(muestras, *, subidas, procesos) -> dict[str, list[float]]:
    """Reparte `(instante_de_inicio, ms)` por el instante en que EMPEZÓ el turno (epoch). `subidas` y
    `procesos` son listas de ventanas `[ini, fin)`: la subida del lote y, tras ella, el procesamiento hasta
    que todas las filas son terminales. Antes de la primera subida es «antes»; después del último
    procesamiento, «despues»; entre una repetición y otra, «reposo»."""
    fases: dict[str, list[float]] = {"antes": [], "subida": [], "procesamiento": [], "reposo": [], "despues": []}
    primero = min((i for i, _ in subidas), default=None)
    ultimo = max((f for _, f in procesos), default=None)

    def dentro(t, ventanas):
        return any(i <= t < f for i, f in ventanas)
    for t, ms in muestras:
        if primero is None or t < primero:
            fases["antes"].append(ms)
        elif dentro(t, subidas):
            fases["subida"].append(ms)
        elif dentro(t, procesos):
            fases["procesamiento"].append(ms)
        elif ultimo is not None and t >= ultimo:
            fases["despues"].append(ms)
        else:
            fases["reposo"].append(ms)
    return fases


def instante_en_que(muestras, *, desde: float, esperadas: int, que: str) -> float | None:
    """Primer instante `>= desde` en que el sondeo `(t, total, en_cola, terminales)` cumple: `sin_en_cola` = ya
    están registradas las `esperadas` filas y ninguna sigue `en_cola`; `terminal` = las `esperadas` son terminales."""
    if que not in ("sin_en_cola", "terminal"):
        raise ValueError(f"que={que!r}")
    for t, total, en_cola, terminales in muestras:
        if t < desde:
            continue
        if que == "sin_en_cola" and total >= esperadas and en_cola == 0:
            return t
        if que == "terminal" and terminales >= esperadas:
            return t
    return None


def alertas_de_plan(filas: list[dict]) -> list[str]:
    return sorted({s for f in filas for s in ("Using filesort", "Using temporary") if s in (f.get("Extra") or "")})


def leer_rss_kb(texto_status: str) -> dict:
    """VmRSS y VmHWM (pico) de `/proc/<pid>/status`, en kB."""
    def campo(nombre):
        m = re.search(rf"^{nombre}:\s+(\d+)\s+kB", texto_status, re.M)
        return int(m.group(1)) if m else None
    return {"rss_kb": campo("VmRSS"), "pico_kb": campo("VmHWM")}


def verificar_workspace_propio(ruta: Path) -> None:
    """El workspace de la corrida jamás es el real, ni uno de sus padres ni de sus hijos."""
    r, real = Path(ruta).resolve(), WORKSPACE_REAL.resolve()
    if r == real or real in r.parents or r in real.parents:
        raise RuntimeError(f"{ruta} es o contiene el workspace real {real} -- ABORTANDO")


def plan_de_fuente(total: int, subcarpetas: int) -> list[str]:
    """`total` rutas relativas a `fuente/`, repartidas en `subcarpetas` carpetas de primer nivel (`d0000`...),
    todas distintas. Cada 250 carpetas una cuelga de 5 niveles mas (`n1/n2/n3/n4/n5`): el recorrido
    tiene que bajar. Determinista. Nada cae bajo `zzz/` (el objetivo va ahi, al final del orden)."""
    if total < 1 or subcarpetas < 1 or subcarpetas > total:
        raise ValueError(f"total={total} subcarpetas={subcarpetas}")
    base, resto = divmod(total, subcarpetas)
    rutas = []
    for k in range(subcarpetas):
        carpeta = f"d{k:04d}" + ("/n1/n2/n3/n4/n5" if k % 250 == 0 else "")
        rutas += [f"{carpeta}/f{j:03d}.pdf" for j in range(base + (1 if k < resto else 0))]
    return rutas


def ruta_del_objetivo(i: int, profundidad: int = PROFUNDIDAD_OBJETIVO) -> str:
    """Donde vive el original del documento `i`: bajo `zzz/` (el ultimo en el orden del recorrido, asi que
    todos los rellenos se leen antes) y `profundidad` niveles adentro. Su nombre NO se parece a
    `nombre_original` (`Informe-<i>.pdf`): la preferencia por nombre no acorta la busqueda."""
    if profundidad < 1:
        raise ValueError("profundidad")
    return "zzz/" + "/".join(f"n{n}" for n in range(1, profundidad)) + f"/scan-{i:02d}.pdf"


def escribir_fuente(carpeta: Path, rutas: list[str], tamano: int) -> int:
    """Escribe cada ruta bajo `carpeta` con `tamano` bytes ALEATORIOS (cada sha256 distinto). Devuelve cuantos."""
    creadas: set[Path] = set()
    for r in rutas:
        destino = carpeta / r
        if destino.parent not in creadas:
            destino.parent.mkdir(parents=True, exist_ok=True)
            creadas.add(destino.parent)
        destino.write_bytes(os.urandom(tamano))
    return len(rutas)


def peticiones_por_segundo(n: int, segundos: float) -> float:
    return 0.0 if segundos <= 0 else round(n / segundos, 2)


def resumen_de_reprocesar(muestras: list[dict], segundos: float) -> dict:
    """`muestras`: [{status, ms}]. Cuenta por codigo, rps (de todas y de las 202) y latencias de las 202 y de todas."""
    codigos: dict[str, int] = {}
    for m in muestras:
        k = str(m["status"])
        codigos[k] = codigos.get(k, 0) + 1
    ok = [m["ms"] for m in muestras if m["status"] == 202]
    return {"peticiones": len(muestras), "codigos": codigos, "rps_total": peticiones_por_segundo(len(muestras), segundos),
            "rps_202": peticiones_por_segundo(len(ok), segundos),
            "latencia_202_ms": _resumen_fase(ok), "latencia_todas_ms": _resumen_fase([m["ms"] for m in muestras])}


def contar_descriptores(pid: int) -> int:
    return len(os.listdir(f"/proc/{pid}/fd"))


# ---------------------------------------------------------------------------
# LAS MANOS falso
# ---------------------------------------------------------------------------
class ManosFalsas(ThreadingHTTPServer):
    """Lo mínimo de LAS MANOS que usa el despachador (T7): `POST /procesamiento/trabajos` -> 202 con
    `job_id`; `GET /procesamiento/trabajos/{id}` -> `running` hasta `retraso_s` y después `completed`
    con un resultado `ok` por ruta. Exige la credencial de servicio, como el real. No procesa nada."""
    daemon_threads = True

    def __init__(self, direccion, *, credencial: str, retraso_s: float):
        super().__init__(direccion, _ManejadorManos)
        self.credencial = credencial
        self.retraso_s = retraso_s
        self._trabajos: dict[str, tuple[list[str], float]] = {}
        self._candado = threading.Lock()
        self.trabajos_recibidos = 0
        self.rutas_recibidas = 0
        self._hilo: threading.Thread | None = None

    def arrancar(self) -> None:
        self._hilo = threading.Thread(target=self.serve_forever, daemon=True, name="manos-falsas")
        self._hilo.start()

    def parar(self) -> None:
        self.shutdown()
        self.server_close()
        if self._hilo:
            self._hilo.join(timeout=5)

    def crear(self, rutas: list[str]) -> str:
        job_id = f"falso-{uuid.uuid4().hex[:16]}"
        with self._candado:
            self._trabajos[job_id] = (rutas, time.monotonic() + self.retraso_s)
            self.trabajos_recibidos += 1
            self.rutas_recibidas += len(rutas)
        return job_id

    def consultar(self, job_id: str) -> dict | None:
        with self._candado:
            t = self._trabajos.get(job_id)
        if t is None:
            return None
        rutas, fin = t
        if time.monotonic() < fin:
            return {"job_id": job_id, "estado": "running", "resultados": []}
        return {"job_id": job_id, "estado": "completed",
                "resultados": [{"archivo": r, "estado": "ok", "carpeta_procesado": f"procesado-{i:04d}"}
                               for i, r in enumerate(rutas)]}


class _ManejadorManos(BaseHTTPRequestHandler):
    server: ManosFalsas

    def log_message(self, *_args):   # silencio: la corrida imprime lo suyo
        pass

    def _responder(self, estado: int, cuerpo) -> None:
        datos = json.dumps(cuerpo).encode()
        self.send_response(estado)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(datos)))
        self.end_headers()
        self.wfile.write(datos)

    def _autorizado(self) -> bool:
        if self.headers.get(ENCABEZADO_CREDENCIAL, "") != self.server.credencial:
            self._responder(401, {"detail": "credencial invalida"})
            return False
        return True

    def do_POST(self):
        largo = int(self.headers.get("Content-Length", "0") or 0)
        crudo = self.rfile.read(largo) if largo else b""
        if not self._autorizado():
            return
        if self.path != "/procesamiento/trabajos":
            return self._responder(404, {"detail": "no existe"})
        try:
            cuerpo = json.loads(crudo or b"null")
        except ValueError:
            return self._responder(422, {"detail": "cuerpo ilegible"})
        rutas = cuerpo.get("rutas") if isinstance(cuerpo, dict) else None
        if (not isinstance(cuerpo.get("project_uuid") if isinstance(cuerpo, dict) else None, str)
                or not isinstance(rutas, list) or not rutas or not isinstance(cuerpo.get("usuario"), str)):
            return self._responder(422, {"detail": "faltan project_uuid, rutas o usuario"})
        if len(rutas) > TOPE_RUTAS_POR_TRABAJO_MANOS:
            return self._responder(422, {"detail": f"demasiadas rutas: {len(rutas)} > {TOPE_RUTAS_POR_TRABAJO_MANOS}"})
        self._responder(202, {"job_id": self.server.crear([str(r) for r in rutas])})

    def do_GET(self):
        if not self._autorizado():
            return
        m = re.fullmatch(r"/procesamiento/trabajos/([\w-]+)", self.path)
        trabajo = self.server.consultar(m.group(1)) if m else None
        if trabajo is None:
            return self._responder(404, {"detail": "trabajo desconocido"})
        self._responder(200, trabajo)


# ---------------------------------------------------------------------------
# Siembra (la base es propia: se elimina entera, no hace falta limpiar fila por fila)
# ---------------------------------------------------------------------------
def nuevo_sufijo() -> str:
    return uuid.uuid4().hex[:8]


def sembrar(sufijo: str, *, amigable: bool = False) -> dict:
    """Usuarios y proyectos de la corrida. `amigable`: nombres legibles para las capturas."""
    conn = e1._conectar()
    try:
        return _sembrar(conn, sufijo, amigable)
    finally:
        conn.close()


def _sembrar(conn, sufijo: str, amigable: bool) -> dict:
    import bcrypt
    ahora = "2026-10-03 00:00:00.000000"
    pw = bcrypt.hashpw(PASSWORD_DE_PRUEBA.encode(), bcrypt.gensalt(rounds=4)).decode()

    def usuario(cur, etiqueta):
        correo = f"{PREFIJO}{etiqueta}-{sufijo}@{DOMINIO}"
        cur.execute("INSERT INTO jax_users (tenant_id, email, password_hash, role, status, token_version) "
                    "VALUES (%s, %s, %s, 'operator', 'active', 0)", (TENANT_ID, correo, pw))
        return cur.lastrowid, correo

    with conn.cursor() as cur:
        dueno_chat, _ = usuario(cur, "dueno")
        chat = [usuario(cur, f"chat{i:02d}")[0] for i in range(USUARIOS_CHAT)]
        subidor, email_subidor = usuario(cur, "subidor")
        pequenos = [usuario(cur, f"peq{i:02d}")[0] for i in range(PEQUENOS)]
    conn.commit()

    def proyecto(cur, nombre):
        cur.execute("INSERT INTO projects (project_uuid, name, description, status) VALUES (%s, %s, %s, 'active')",
                    (str(uuid.uuid4()), nombre, "proyecto de la carga E2a"))
        pid = cur.lastrowid
        cur.execute("SELECT project_uuid FROM projects WHERE id = %s", (pid,))
        return pid, cur.fetchone()[0]

    def nombre(clave, bonito):
        return bonito if amigable else f"{PREFIJO}{sufijo}-{clave}"

    membresias, scopes = [], []
    with conn.cursor() as cur:
        p_chat, u_chat = proyecto(cur, nombre("chat", "Atención al cliente"))
        p_lactovi, u_lactovi = proyecto(cur, nombre("lactovi", "Lácteos Victoria"))
        p_peq = [proyecto(cur, nombre(f"peq{i:02d}", f"Proyecto menor {i + 1}")) for i in range(PEQUENOS)]
    conn.commit()

    def alta(pid, uid, papel, origen, por):
        membresias.append((str(uuid.uuid4()), pid, TENANT_ID, uid, papel, origen, ahora, f"user:{por}", ahora))

    for pid in [p_chat, p_lactovi] + [p for p, _ in p_peq]:
        scopes.append((pid, TENANT_ID, "ACTIVE", ahora, f"user:{dueno_chat}", ahora))
    alta(p_chat, dueno_chat, "OWNER", "CREATOR", dueno_chat)
    for uid in chat:
        alta(p_chat, uid, "CONTRIBUTOR", "EXPLICIT", dueno_chat)
    alta(p_lactovi, subidor, "OWNER", "CREATOR", subidor)
    for (pid, _), uid in zip(p_peq, pequenos):
        alta(pid, uid, "OWNER", "CREATOR", uid)
    if amigable:   # el selector del chat ofrece varios proyectos al usuario de las capturas
        alta(p_peq[0][0], subidor, "CONTRIBUTOR", "EXPLICIT", pequenos[0])
        alta(p_peq[1][0], subidor, "CONTRIBUTOR", "EXPLICIT", pequenos[1])
        alta(p_chat, subidor, "VIEWER", "EXPLICIT", dueno_chat)
    with conn.cursor() as cur:
        cur.executemany("INSERT INTO jax_project_scope (project_id, tenant_id, status, created_at, created_by, updated_at) "
                        "VALUES (%s,%s,%s,%s,%s,%s)", scopes)
        cur.executemany("INSERT INTO jax_project_membership (membership_id, project_id, tenant_id, user_id, project_role, "
                        "status, grant_origin, created_at, created_by, updated_at) "
                        "VALUES (%s,%s,%s,%s,%s,'ACTIVE',%s,%s,%s,%s)", membresias)
    conn.commit()
    semilla = {"sufijo": sufijo, "dueno_chat": dueno_chat, "chat": chat, "subidor": subidor, "email_subidor": email_subidor,
               "pequenos": pequenos, "p_chat": p_chat, "p_lactovi": p_lactovi, "u_lactovi": u_lactovi,
               "p_pequenos": [p for p, _ in p_peq], "tenant_id": str(TENANT_ID)}
    print(f"[siembra] proyectos={2 + PEQUENOS} usuarios={1 + USUARIOS_CHAT + 1 + PEQUENOS}", file=sys.stderr)
    return semilla


def sembrar_documentos_visuales(semilla: dict) -> None:
    """Documentos en todos los estados para las capturas (solo `visual`). Los `en_cola` los toma el
    despachador y los deja `procesando` contra el LAS MANOS falso (con retraso largo)."""
    import pymysql
    env = e1._cargar_env_de_prueba()
    conn = pymysql.connect(host=env["JAX_DB_HOST"], port=int(env["JAX_DB_PORT"]), user=env["JAX_DB_USER"],
                           password=env["JAX_DB_PASSWORD"], database=e1._base_activa(), charset="utf8mb4")
    try:
        filas = [("Balance general 2025.xlsx", 184_320, "xlsx", "listo", None, 0),
                 ("Contrato proveedor Lácteos del Sur.pdf", 2_457_600, "pdf", "listo", None, 0),
                 ("Factura 0142.pdf", 98_304, "pdf", "parcial", None, 0),
                 ("Foto de recepción de mercadería.jpg", 3_145_728, "jpg", "error", "ocr_fallo", 0),
                 ("Planilla de turnos.xls", 61_440, "xls", "sin_extractor", None, 0),
                 ("Inventario 2024.csv", 40_960, "csv", "cancelado", None, 0),
                 ("Acta de reunión.docx", 28_672, "docx", "en_cola", None, 0),
                 ("Escaneo de cheque.png", 512_000, "png", "en_cola", None, 0),
                 ("Notas viejas.txt", 2_048, "txt", "listo", None, 1)]
        with conn.cursor() as cur:
            for i, (nombre, b, tipo, estado, error, oculto) in enumerate(filas):
                sha = hashlib.sha256(f"visual-{i}".encode()).hexdigest()
                ruta = f"proyectos/{semilla['u_lactovi']}/entrada/visual/{i:02d}-{nombre}"
                cur.execute(
                    "INSERT INTO project_documents (project_id, sha256, nombre_original, ruta_entrada, bytes, tipo, estado, "
                    "error, subido_por, oculto_at, oculto_por) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,"
                    f"{'CURRENT_TIMESTAMP(6)' if oculto else 'NULL'},%s)",
                    (semilla["p_lactovi"], sha, nombre, ruta, b, tipo, estado, error, semilla["subidor"],
                     semilla["subidor"] if oculto else None))
        conn.commit()
    finally:
        conn.close()


def sembrar_filas_explain(semilla: dict, total: int = FILAS_EXPLAIN) -> int:
    """Completa `project_documents` hasta `total` filas: 60 % en LACTOVI, el resto repartido en los demás
    proyectos; 20 % ocultas; ~2 % `en_cola` (sin job), ~8 % abiertas con job, el resto terminales con job."""
    import pymysql
    env = e1._cargar_env_de_prueba()
    conn = pymysql.connect(host=env["JAX_DB_HOST"], port=int(env["JAX_DB_PORT"]), user=env["JAX_DB_USER"],
                           password=env["JAX_DB_PASSWORD"], database=e1._base_activa(), charset="utf8mb4")
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM project_documents")
            existentes = cur.fetchone()[0]
            cur.execute("SELECT id, project_uuid FROM projects WHERE id IN (%s)" % ",".join(["%s"] * (2 + PEQUENOS)),
                        [semilla["p_chat"], semilla["p_lactovi"], *semilla["p_pequenos"]])
            uuids = dict(cur.fetchall())
        duenos = {semilla["p_chat"]: semilla["dueno_chat"], semilla["p_lactovi"]: semilla["subidor"],
                  **dict(zip(semilla["p_pequenos"], semilla["pequenos"]))}
        nuevas = max(0, total - existentes)
        grandes = int(nuevas * 0.6)
        otros = [semilla["p_chat"], *semilla["p_pequenos"]]
        filas = []
        for k in range(nuevas):
            pid = semilla["p_lactovi"] if k < grandes else otros[(k - grandes) % len(otros)]
            r = k % 50
            estado = ("en_cola" if r == 1 else "pendiente" if r in (2, 3) else "procesando" if r == 4
                      else "error" if r == 5 else "parcial" if r == 6 else "listo")
            job = None if estado == "en_cola" else f"job-explain-{k % 40:03d}"
            oculto = k % 5 == 0
            sha = hashlib.sha256(f"explain-{k}".encode()).hexdigest()
            filas.append((pid, sha, f"documento-{k:06d}.pdf",
                          f"proyectos/{uuids[pid]}/entrada/lote{k // 50:04d}/f{k:06d}.pdf",
                          f"procesado-{k:06d}" if estado == "listo" else None, 1000 + k, "pdf", estado,
                          "falla" if estado == "error" else None, job, duenos[pid],
                          "2026-10-03 00:00:00.000000" if oculto else None, duenos[pid] if oculto else None))
        sql = ("INSERT INTO project_documents (project_id, sha256, nombre_original, ruta_entrada, carpeta_procesado, bytes, "
               "tipo, estado, error, job_id, subido_por, oculto_at, oculto_por) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)")
        with conn.cursor() as cur:
            for i in range(0, len(filas), 1000):
                cur.executemany(sql, filas[i:i + 1000])
            conn.commit()
            cur.execute("ANALYZE TABLE project_documents")   # estadísticas al día, como las deja el auto-recalculo
            cur.fetchall()
            cur.execute("SELECT COUNT(*), SUM(oculto_at IS NOT NULL), SUM(estado='en_cola') FROM project_documents")
            n, ocultas, en_cola = cur.fetchone()
        print(f"[explain] project_documents: {n} filas ({ocultas} ocultas, {en_cola} en_cola)", file=sys.stderr)
        return int(n)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Procesos
# ---------------------------------------------------------------------------
def _env_del_backend(tmp: Path, workspace: Path) -> dict:
    verificar_workspace_propio(workspace)
    env = e1._env_del_backend(tmp)
    env["LAS_MANOS_URL"] = f"http://127.0.0.1:{MANOS_PORT}"
    env["JACOBS_URL"] = f"http://127.0.0.1:{MANOS_PORT}/jacobs"
    env["JAX_PLATFORM_URL"] = BACKEND_URL
    env["JAX_WORKSPACE_DIR"] = str(workspace)
    env["JAX_SEED_TENANT_NAME"] = "Tenant de la carga de proyectos E2a"
    return env


def _levantar_backend(env: dict, tmp: Path):
    e1._puerto_libre(BACKEND_PORT)
    log = open(tmp / "backend.log", "w")
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port",
                             str(BACKEND_PORT), "--log-level", "warning"],
                            cwd=str(BACKEND_DIR), env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    try:
        e1._esperar_puerto(BACKEND_PORT)
    except Exception:
        proc.kill()
        print((tmp / "backend.log").read_text()[-3000:], file=sys.stderr)
        raise
    pares = dict(p.split(b"=", 1) for p in Path(f"/proc/{proc.pid}/environ").read_bytes().split(b"\x00") if b"=" in p)
    if pares.get(b"JAX_DB_NAME", b"").decode() != e1._base_activa():
        raise RuntimeError("el backend levantado NO apunta a la base de prueba -- ABORTANDO")
    if pares.get(b"JAX_WORKSPACE_DIR", b"").decode() != env["JAX_WORKSPACE_DIR"]:
        raise RuntimeError("el backend levantado NO usa el workspace temporal -- ABORTANDO")
    if pares.get(b"LAS_MANOS_URL", b"").decode() != f"http://127.0.0.1:{MANOS_PORT}":
        raise RuntimeError("el backend levantado NO apunta a LAS MANOS falso -- ABORTANDO")
    return proc, log


def _levantar_manos(env: dict) -> ManosFalsas:
    e1._puerto_libre(MANOS_PORT)
    srv = ManosFalsas(("127.0.0.1", MANOS_PORT), credencial=env["JAX_LAS_MANOS_CREDENCIAL_PLATAFORMA"],
                      retraso_s=RETRASO_MANOS_S)
    srv.arrancar()
    return srv


def _preparar_base() -> tuple[str, str]:
    sufijo = nuevo_sufijo()
    base = e1.base_de_peor_caso(sufijo)
    os.environ["PROYECTOS_E1_BASE"] = base     # la heredan los hijos; e1._conectar() y los chequeos la exigen
    return sufijo, base


# ---------------------------------------------------------------------------
# Hijo: el chat de 20 usuarios (proceso aparte: su carga no compite con el arnés de subida)
# ---------------------------------------------------------------------------
async def _chat_hijo(config: dict) -> None:
    """20 clientes en bucle cerrado. Cada turno = GET /api/proyectos/{P} (autorización de proyecto por el
    uvicorn de la subida) + resolve_scope + retrieve_authorized (la ruta de proyecto del turno, como E1).
    Corre hasta que existe `config['parar']`; vuelca `(inicio_epoch, total_ms, http_ms, b9_ms)` a `config['salida']`."""
    import httpx
    from b9_pool import B9MappingPool
    from db.connection import get_pool
    from jax.memory.b9 import (AuthorizationDenied, MutationAuthorizationRequest, ScopeContext, ScopeDenied,
                               Visibility)
    from jax.memory.b9_mariadb import MariaDBB9Reader
    from jax.memory.scope_authority import ProjectScopeAuthorityResolver
    from jax_engine.memory_prompt_selection import limits_from_environment
    pool = await get_pool()
    pid = config["proyecto"]
    limite = limits_from_environment().candidates
    parar = Path(config["parar"])
    muestras: list[list[float]] = []
    errores: dict[str, int] = {}
    muestras_de_error: dict[str, str] = {}
    n = len(config["usuarios"])
    limits = httpx.Limits(max_connections=n + 5, max_keepalive_connections=n + 5)
    async with httpx.AsyncClient(limits=limits, timeout=60.0) as cli:
        async def cliente(uid: int, cabecera: dict):
            while not parar.exists():
                inicio = time.time()
                t0 = time.perf_counter()
                try:
                    r = await cli.get(f"{config['url']}/api/proyectos/{pid}", headers=cabecera)
                    if r.status_code != 200:
                        k = f"http_{r.status_code}"
                        errores[k] = errores.get(k, 0) + 1
                        muestras_de_error.setdefault(k, r.text[:150])
                        continue
                    t1 = time.perf_counter()
                    alcance = ScopeContext(actor_principal=f"user:{uid}", actor_type="USER", subject_user_id=str(uid),
                                           tenant_id=str(TENANT_ID), project_id=str(pid),
                                           calling_component="jax-platform-web-chat")
                    resuelto = await ProjectScopeAuthorityResolver(pool).resolve_scope(alcance)
                    b9 = B9MappingPool(pool)
                    lector = MariaDBB9Reader(b9, ProjectScopeAuthorityResolver(b9))
                    pedido = MutationAuthorizationRequest(
                        resuelto, "RETRIEVE", Visibility.PROJECT_SHARED if resuelto.project_id else Visibility.TENANT_SHARED)
                    await lector.retrieve_authorized(pedido, limit=limite)
                except (AuthorizationDenied, ScopeDenied):
                    errores["denegado"] = errores.get("denegado", 0) + 1
                    continue
                except Exception as exc:  # fail-soft: cuenta como error de la corrida
                    k = f"otro:{type(exc).__name__}"
                    errores[k] = errores.get(k, 0) + 1
                    muestras_de_error.setdefault(k, repr(exc)[:150])
                    continue
                t2 = time.perf_counter()
                muestras.append([inicio, (t2 - t0) * 1000, (t1 - t0) * 1000, (t2 - t1) * 1000])
        await asyncio.gather(*(cliente(u["id"], {"Authorization": u["autorizacion"]}) for u in config["usuarios"]))
    Path(config["salida"]).write_text(json.dumps({"muestras": muestras, "errores": errores,
                                                  "muestras_de_error": muestras_de_error,
                                                  "pool_maxsize": pool.maxsize}))


def _lanzar_chat(config: dict, tmp: Path) -> subprocess.Popen:
    ruta = tmp / "chat-config.json"
    ruta.write_text(json.dumps(config))
    env = dict(os.environ, **e1._cargar_env_de_prueba(),
               PYTHONPATH=os.pathsep.join([str(BACKEND_DIR), os.environ.get("PYTHONPATH", "")]))
    return subprocess.Popen([sys.executable, str(Path(__file__)), "--chat", str(ruta)], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


# ---------------------------------------------------------------------------
# Hijo: EXPLAIN de las consultas REALES del repositorio
# ---------------------------------------------------------------------------
async def _explain(semilla: dict) -> dict:
    """Las funciones de `proyectos_documentos.repositorio` se llaman de verdad; un parche de
    `Cursor.execute` graba las sentencias que mandan y se les hace EXPLAIN con sus mismos parámetros."""
    import aiomysql
    from db.connection import get_pool
    from jax.memory.scope_authority import MariaDBScopeAuthorityResolver, ProjectRole
    from proyectos_documentos import repositorio as repo
    grabadas: list[tuple[str, str, tuple, float]] = []
    etiqueta = {"x": ""}
    original = aiomysql.Cursor.execute

    async def grabar(self, query, args=None):
        if query.lstrip().upper().startswith(("SELECT", "INSERT", "UPDATE")):
            grabadas.append((etiqueta["x"], query, tuple(args or ()), 0.0))
        return await original(self, query, args)
    aiomysql.Cursor.execute = grabar
    pool = await get_pool()
    # Los papeles que escriben, con la misma capacidad de B9 que usa api/proyectos_documentos.py (no se importa
    # ese módulo: arrastra la autenticación, que exige el secreto JWT del servicio).
    roles_escritura = tuple(r.value for r in ProjectRole
                            if "memory:project:write" in MariaDBScopeAuthorityResolver._project_roles(r)[1])
    p = semilla["p_lactovi"]
    tiempos: dict[str, float] = {}

    async def correr(nombre, coro):
        etiqueta["x"] = nombre
        t0 = time.perf_counter()
        r = await coro
        tiempos[nombre] = round((time.perf_counter() - t0) * 1000, 2)
        return r
    try:
        primera = await correr("listar visibles (1ª página)", repo.listar(pool, project_id=p, ocultos=False, antes_de=None, limite=51))
        await correr("listar visibles (2ª página, antes_de)", repo.listar(pool, project_id=p, ocultos=False, antes_de=primera[-1]["id"], limite=51))
        await correr("listar visibles (proyecto chico, 1ª página)", repo.listar(
            pool, project_id=semilla["p_pequenos"][0], ocultos=False, antes_de=None, limite=51))
        await correr("listar ocultos", repo.listar(pool, project_id=p, ocultos=True, antes_de=None, limite=51))
        await correr("tomar_en_cola", repo.tomar_en_cola(pool, limite=1000))
        await correr("trabajos_abiertos", repo.trabajos_abiertos(pool))
        await correr("INSERT condicional", repo.insertar(
            pool, project_id=p, sha256=hashlib.sha256(b"explain-insertar").hexdigest(), nombre_original="nuevo.pdf",
            ruta_entrada=f"proyectos/{semilla['u_lactovi']}/entrada/explain/nuevo.pdf", bytes_=1, tipo="pdf",
            subido_por=semilla["subidor"], roles_escritura=roles_escritura))
        # Extras (fuera de la lista del brief): las otras consultas que el despachador repite por vuelta.
        await correr("extra: filas_abiertas_de_trabajo", repo.filas_abiertas_de_trabajo(pool, job_id="job-explain-003"))
        await correr("extra: aplicar_resultado (UPDATE)", repo.aplicar_resultado(
            pool, job_id="job-explain-003", ruta_entrada="proyectos/x/entrada/y/z.pdf", estado="listo",
            carpeta_procesado=None, error=None))
        await correr("extra: existente_por_sha", repo.existente_por_sha(pool, project_id=p, sha256=hashlib.sha256(b"explain-0").hexdigest()))
    finally:
        aiomysql.Cursor.execute = original
    salida = []
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            for et, sql, args, _ in grabadas:
                await cur.execute("EXPLAIN " + sql, args)
                cols = [d[0] for d in cur.description]
                filas = [dict(zip(cols, [None if v is None else str(v) for v in r])) for r in await cur.fetchall()]
                real = None
                if sql.lstrip().upper().startswith("SELECT"):   # ANALYZE ejecuta la sentencia: solo sobre SELECT
                    await cur.execute("ANALYZE " + sql, args)
                    cols = [d[0] for d in cur.description]
                    real = [{"tabla": f.get("table"), "rows_estimadas": f.get("rows"), "r_rows": f.get("r_rows")}
                            for f in (dict(zip(cols, [None if v is None else str(v) for v in r])) for r in await cur.fetchall())]
                salida.append({"consulta": et, "ms": tiempos.get(et), "sql": " ".join(sql.split()),
                               "params": [str(a)[:60] for a in args], "plan": filas, "alertas": alertas_de_plan(filas),
                               "filas_leidas_de_verdad": real})
    return {"explain": salida}


def _hijo_explain(semilla: dict) -> dict:
    r = subprocess.run([sys.executable, str(Path(__file__)), "--explain", json.dumps(semilla)], capture_output=True,
                       text=True, timeout=600,
                       env=dict(os.environ, **e1._cargar_env_de_prueba(),
                                PYTHONPATH=os.pathsep.join([str(BACKEND_DIR), os.environ.get("PYTHONPATH", "")])))
    if r.returncode != 0:
        raise RuntimeError(f"--explain falló:\n{r.stderr[-3000:]}")
    return json.loads(r.stdout.splitlines()[-1])


# ---------------------------------------------------------------------------
# La corrida
# ---------------------------------------------------------------------------
def _cuentas_por_estado(conn, project_id: int) -> dict[str, int]:
    with conn.cursor() as cur:
        cur.execute("SELECT estado, COUNT(*) FROM project_documents WHERE project_id = %s GROUP BY estado", (project_id,))
        return {k: int(v) for k, v in cur.fetchall()}


def _conexion_de_lectura():
    """Conexión pymysql en autocommit: cada lectura ve lo ultimo confirmado (con la transaccion abierta de
    `e1._conectar` vería siempre la misma foto)."""
    conn = e1._conectar()
    conn.autocommit(True)
    return conn


class _MuestreoRss:
    """Lee VmRSS del uvicorn cada 100 ms en un hilo."""

    def __init__(self, pid: int):
        self.pid = pid
        self.muestras: list[tuple[float, int]] = []
        self.pico_proceso_kb: int | None = None
        self._parar = threading.Event()
        self._hilo = threading.Thread(target=self._correr, daemon=True)

    def _correr(self):
        while not self._parar.is_set():
            try:
                d = leer_rss_kb(Path(f"/proc/{self.pid}/status").read_text())
            except OSError:   # fail-soft: el proceso ya no esta; se corta el muestreo
                return
            if d["rss_kb"] is not None:
                self.muestras.append((time.time(), d["rss_kb"]))
            self.pico_proceso_kb = d["pico_kb"]
            self._parar.wait(0.1)

    def __enter__(self):
        self._hilo.start()
        return self

    def __exit__(self, *_):
        self._parar.set()
        self._hilo.join(timeout=2)

    def entre(self, ini: float, fin: float) -> list[int]:
        return [kb for t, kb in self.muestras if ini <= t <= fin]


async def _subir(cli, cabecera: dict, project_id: int, rutas: list[Path]) -> dict:
    """POST del lote por streaming (httpx lee cada archivo por bloques: el cliente tampoco carga el lote en memoria)."""
    abiertos = [open(r, "rb") for r in rutas]
    try:
        t0 = time.perf_counter()
        try:
            r = await cli.post(f"{BACKEND_URL}/api/proyectos/{project_id}/documentos", headers=cabecera,
                               files=[("archivos", (ruta.name, f, "application/octet-stream")) for ruta, f in zip(rutas, abiertos)])
            ms = (time.perf_counter() - t0) * 1000
            cuerpo = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            return {"status": r.status_code, "ms": round(ms, 1), "aceptados": len(cuerpo.get("aceptados", [])),
                    "ignorados": len(cuerpo.get("ignorados", [])),
                    "motivos_ignorados": _contar([i.get("motivo") for i in cuerpo.get("ignorados", [])]), "detalle": None if r.status_code == 202 else r.text[:200]}
        except Exception as exc:  # fail-soft: una subida caída cuenta como error y no tumba la corrida
            return {"status": None, "ms": round((time.perf_counter() - t0) * 1000, 1), "aceptados": 0, "ignorados": 0,
                    "detalle": repr(exc)[:200]}
    finally:
        for f in abiertos:
            f.close()


def _contar(valores) -> dict:
    cuentas: dict = {}
    for v in valores:
        cuentas[str(v)] = cuentas.get(str(v), 0) + 1
    return cuentas


def _p(valores, p):
    v = e1.percentil(valores, p)
    return None if v is None else round(v, 1)


def _resumen_fase(ms: list[float]) -> dict:
    return {"n": len(ms), "p50_ms": _p(ms, 50), "p95_ms": _p(ms, 95), "p99_ms": _p(ms, 99),
            "max_ms": round(max(ms), 1) if ms else None}


class _Sondeo:
    """Sondea `project_documents` de un proyecto cada 100 ms en un HILO propio (con el arnés de subida ocupando
    el event loop, un sondeo en el loop mediría el arnés y no la cola). Muestras `(t, total, en_cola, terminales)`."""

    def __init__(self, project_id: int):
        self.project_id = project_id
        self.muestras: list[tuple[float, int, int, int]] = []
        self._parar = threading.Event()
        self._hilo = threading.Thread(target=self._correr, daemon=True)

    def _correr(self):
        conn = _conexion_de_lectura()
        try:
            while not self._parar.is_set():
                c = _cuentas_por_estado(conn, self.project_id)
                self.muestras.append((time.time(), sum(c.values()), c.get("en_cola", 0),
                                      sum(c.get(e, 0) for e in ESTADOS_TERMINALES)))
                self._parar.wait(0.1)
        finally:
            conn.close()

    def __enter__(self):
        self._hilo.start()
        return self

    def __exit__(self, *_):
        self._parar.set()
        self._hilo.join(timeout=5)

    async def esperar(self, *, desde: float, esperadas: int, que: str, timeout: float) -> float | None:
        fin = time.time() + timeout
        while time.time() < fin:
            t = instante_en_que(list(self.muestras), desde=desde, esperadas=esperadas, que=que)
            if t is not None:
                return t
            await asyncio.sleep(0.1)
        return None


def _mib(kb) -> float | None:
    return None if kb is None else round(kb / 1024, 1)


async def _fase_grande(env, semilla, tmp, plan, res) -> None:
    """Sube el lote de LACTOVI `REPETICIONES_GRANDE` veces (bytes aleatorios NUEVOS cada vez: nada es duplicado)
    con 20 usuarios chateando todo el tiempo, y mide cada vez cuándo la última fila deja `en_cola` y cuándo todas
    son terminales."""
    import httpx
    total = sum(b for _, b in plan)
    res["lote"] = {"archivos": len(plan), "bytes": total, "mib": round(total / 2**20, 1), "repeticiones": REPETICIONES_GRANDE,
                   "mayor_bytes": max(b for _, b in plan), "menor_bytes": min(b for _, b in plan)}
    print(f"[lote] {res['lote']}", file=sys.stderr)
    usuarios = [{"id": u, "autorizacion": e1._token(env, u)["Authorization"]} for u in semilla["chat"]]
    parar_chat = tmp / "parar-chat"
    config = {"url": BACKEND_URL, "proyecto": semilla["p_chat"], "usuarios": usuarios, "parar": str(parar_chat),
              "salida": str(tmp / "chat-salida.json")}
    chat = _lanzar_chat(config, tmp)
    h = e1._token(env, semilla["subidor"])
    reps, subidas, procesos = [], [], []
    try:
        with _MuestreoRss(_pid_del_backend(env)) as rss, _Sondeo(semilla["p_lactovi"]) as sondeo:
            await asyncio.sleep(SEGUNDOS_ANTES)
            if chat.poll() is not None:
                raise RuntimeError(f"el hijo de chat murió:\n{chat.stderr.read()[-2000:]}")
            rss_antes = rss.entre(time.time() - 10, time.time())
            async with httpx.AsyncClient(timeout=httpx.Timeout(900.0, connect=10.0)) as cli:
                for r in range(REPETICIONES_GRANDE):
                    carpeta = tmp / f"lote-lactovi-{r}"
                    rutas = escribir_lote(carpeta, plan)
                    esperadas = len(plan) * (r + 1)
                    t_ini = time.time()
                    subida = await _subir(cli, h, semilla["p_lactovi"], rutas)
                    t_fin = time.time()
                    shutil.rmtree(carpeta, ignore_errors=True)
                    print(f"[subida {r + 1}] {subida}", file=sys.stderr)
                    t_sin = await sondeo.esperar(desde=t_ini, esperadas=esperadas, que="sin_en_cola", timeout=120)
                    t_term = await sondeo.esperar(desde=t_ini, esperadas=esperadas, que="terminal", timeout=300)
                    fin_proc = t_term or time.time()
                    subidas.append((t_ini, t_fin))
                    procesos.append((t_fin, fin_proc))
                    en_subida = rss.entre(t_ini, t_fin)
                    reps.append({
                        **subida, "segundos": round(t_fin - t_ini, 2), "mib_por_s": round(res["lote"]["mib"] / max(t_fin - t_ini, 1e-9), 1),
                        "hasta_sin_en_cola_s_desde_inicio": None if t_sin is None else round(t_sin - t_ini, 2),
                        "hasta_sin_en_cola_s_tras_la_respuesta": None if t_sin is None else round(t_sin - t_fin, 2),
                        "hasta_todo_terminal_s_desde_inicio": None if t_term is None else round(t_term - t_ini, 2),
                        "hasta_todo_terminal_s_tras_la_respuesta": None if t_term is None else round(t_term - t_fin, 2),
                        "pico_rss_durante_subida_mib": _mib(max(en_subida)) if en_subida else None})
                    if r + 1 < REPETICIONES_GRANDE:
                        await asyncio.sleep(SEGUNDOS_ENTRE_REPETICIONES)
            await asyncio.sleep(SEGUNDOS_DESPUES)
            rss_pico_kb = max([kb for _, kb in rss.muestras], default=None)
            parar_chat.touch()
            _out, err = chat.communicate(timeout=120)
            if chat.returncode != 0:
                raise RuntimeError(f"el hijo de chat falló:\n{err[-3000:]}")
        datos = json.loads(Path(config["salida"]).read_text())
    finally:
        parar_chat.touch()
        if chat.poll() is None:
            chat.kill()

    muestras = datos["muestras"]

    def fases_de(columna):
        return por_fase([(m[0], m[columna]) for m in muestras], subidas=subidas, procesos=procesos)
    fases, fases_http, fases_b9 = fases_de(1), fases_de(2), fases_de(3)
    sin = fases["antes"] + fases["reposo"] + fases["despues"]
    res["chat"] = {
        "usuarios": len(usuarios), "pool_maxsize_del_hijo": datos["pool_maxsize"], "errores": datos["errores"],
        "muestras_de_error": datos["muestras_de_error"], "turnos": len(muestras),
        "turno_total_ms": {f: _resumen_fase(v) for f, v in fases.items()} | {"sin_subida": _resumen_fase(sin)},
        "parte_http_ms": {f: _resumen_fase(v) for f, v in fases_http.items()} | {"sin_subida": _resumen_fase(fases_http["antes"] + fases_http["reposo"] + fases_http["despues"])},
        "parte_b9_ms": {f: _resumen_fase(v) for f, v in fases_b9.items()} | {"sin_subida": _resumen_fase(fases_b9["antes"] + fases_b9["reposo"] + fases_b9["despues"])}}
    res["chat"]["veredicto"] = e1.veredicto_peor_caso(
        res["chat"]["turno_total_ms"]["sin_subida"], res["chat"]["turno_total_ms"]["subida"], sum(datos["errores"].values()))
    res["subida_grande"] = reps
    base_kb = statistics.median(rss_antes) if rss_antes else None
    picos = [r["pico_rss_durante_subida_mib"] for r in reps if r["pico_rss_durante_subida_mib"] is not None]
    res["rss_uvicorn"] = {
        "antes_mediana_mib": _mib(base_kb), "pico_durante_una_subida_mib": max(picos) if picos else None,
        "pico_de_toda_la_corrida_mib": _mib(rss_pico_kb),
        "crece_en_la_peor_subida_mib": None if not (picos and base_kb) else round(max(picos) - base_kb / 1024, 1),
        "lote_mib": res["lote"]["mib"], "muestras": len(rss.muestras)}


def _esperar_terminales(project_ids: list[int], esperadas: int, timeout: float) -> float | None:
    """Instante en que las `esperadas` filas de esos proyectos son todas terminales (None si no llegan)."""
    conn = _conexion_de_lectura()
    try:
        marcas = ",".join(["%s"] * len(project_ids))
        fin = time.time() + timeout
        while time.time() < fin:
            with conn.cursor() as cur:
                cur.execute(f"SELECT COUNT(*), SUM(estado IN ({','.join(repr(e) for e in ESTADOS_TERMINALES)})) "
                            f"FROM project_documents WHERE project_id IN ({marcas})", project_ids)
                n, term = cur.fetchone()
            if int(n) >= esperadas and int(term or 0) >= esperadas:
                return time.time()
            time.sleep(0.2)
        return None
    finally:
        conn.close()


def _archivos_bajo(workspace: Path) -> int:
    return sum(len(f) for _r, _d, f in os.walk(workspace / "proyectos")) if (workspace / "proyectos").is_dir() else 0


async def _fase_pequenas(env, semilla, tmp, res) -> None:
    """10 subidas simultáneas de lotes chicos a 10 proyectos distintos (cada uno con su dueño), varias rondas."""
    import httpx
    resultados, por_ronda = [], []
    carpeta = tmp / "pequenos"
    for ronda in range(RONDAS_PEQUENAS):
        lotes = []
        for i, (pid, uid) in enumerate(zip(semilla["p_pequenos"], semilla["pequenos"])):
            tam = [(64 + (ronda * 7 + i * 3 + j * 11) % 448) * 1024 for j in range(ARCHIVOS_POR_LOTE_PEQUENO)]
            plan = [(f"r{ronda}-p{i}-{j}.pdf", b) for j, b in enumerate(tam)]
            lotes.append((pid, e1._token(env, uid), escribir_lote(carpeta / f"r{ronda}-p{i}", plan)))
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0),
                                     limits=httpx.Limits(max_connections=PEQUENOS + 2)) as cli:
            t0 = time.perf_counter()
            rs = await asyncio.gather(*(_subir(cli, h, pid, rutas) for pid, h, rutas in lotes))
            por_ronda.append(round(time.perf_counter() - t0, 3))
        resultados += rs
        shutil.rmtree(carpeta, ignore_errors=True)
    t_ult = time.time()
    esperadas = sum(r["aceptados"] for r in resultados)
    terminal = await asyncio.to_thread(_esperar_terminales, semilla["p_pequenos"], esperadas, 120)
    codigos: dict = {}
    for r in resultados:
        k = str(r["status"])
        codigos[k] = codigos.get(k, 0) + 1
    res["subidas_simultaneas"] = {
        "simultaneas": PEQUENOS, "rondas": RONDAS_PEQUENAS, "archivos_por_lote": ARCHIVOS_POR_LOTE_PEQUENO,
        "lotes_totales": len(resultados), "codigos": codigos,
        "respuestas_5xx": sum(1 for r in resultados if r["status"] is None or r["status"] >= 500),
        "no_202": [r for r in resultados if r["status"] != 202][:5],
        "archivos_aceptados": sum(r["aceptados"] for r in resultados),
        "latencia_de_la_subida_ms": _resumen_fase([r["ms"] for r in resultados if r["status"] == 202]),
        "segundos_por_ronda": por_ronda,
        "hasta_todo_terminal_s_tras_la_ultima_subida": None if terminal is None else round(terminal - t_ult, 2)}


async def medir() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="carga-proyectos-e2a-"))
    workspace = tmp / "workspace"
    workspace.mkdir()
    sufijo, base = _preparar_base()
    proc = log = manos = None
    creada = False
    res: dict = {"fecha": time.strftime("%Y-%m-%d %H:%M:%S %Z"), "base": base,
                 "workspace": "directorio temporal (borrado al terminar)"}
    try:
        tamanos = tamanos_de_referencia(FUENTE_LACTOVI)
        if len(tamanos) != 120:
            print(f"[aviso] fuente de LACTOVI tiene {len(tamanos)} archivos, no 120", file=sys.stderr)
        e1._crear_base_vacia(base)
        creada = True
        e1._armar_esquema(base)
        env = _env_del_backend(tmp, workspace)
        manos = _levantar_manos(env)
        proc, log = _levantar_backend(env, tmp)
        print(f"[base] {sufijo} ({base}) backend pid={proc.pid} en {BACKEND_URL}; LAS MANOS falso en :{MANOS_PORT}", file=sys.stderr)
        semilla = sembrar(sufijo)
        import httpx
        lim = httpx.get(f"{BACKEND_URL}/api/proyectos/documentos/limites", headers=e1._token(env, semilla["subidor"]), timeout=30)
        if lim.status_code != 200:
            raise RuntimeError(f"verificación previa: límites -> {lim.status_code} {lim.text[:200]}")
        res["limites"] = lim.json() | {"extensiones": f"{len(lim.json()['extensiones'])} extensiones"}
        plan = plan_de_lote(tamanos, frozenset("." + e for e in lim.json()["extensiones"]))   # la API las publica sin punto

        await _fase_grande(env, semilla, tmp, plan, res)
        await _fase_pequenas(env, semilla, tmp, res)
        res["manos_falso"] = {"trabajos_recibidos": manos.trabajos_recibidos, "rutas_recibidas": manos.rutas_recibidas}
        res["resultado_final_lactovi"] = _estado_final(semilla["p_lactovi"])
        res["archivos_que_quedan_en_el_workspace"] = _archivos_bajo(workspace)

        # EXPLAIN: con el backend parado (el despachador de fondo movería las filas sembradas).
        e1._matar(proc)
        proc = None
        res["filas_explain"] = sembrar_filas_explain(semilla)
        res.update(_hijo_explain(semilla))
        res["explain_veredicto"] = [
            {"consulta": q["consulta"], "alertas": q["alertas"]} for q in res["explain"] if q["alertas"]] or "sin filesort ni temporary"
        texto = (tmp / "backend.log").read_text(errors="replace")
        res["backend_log"] = {"lineas_error": len(re.findall(r"\bERROR\b", texto)), "tracebacks": len(re.findall(r"Traceback", texto)),
                              "deadlock_1213": len(re.findall(r"\b1213\b|Deadlock", texto)),
                              "lock_timeout_1205": len(re.findall(r"\b1205\b|Lock wait timeout", texto))}
        salida = ruta_de_resultados()
        salida.write_text(json.dumps(res, indent=2, ensure_ascii=False, default=str))
        print(f"[orquestador] {salida} escrito")
    finally:
        e1._matar(proc)
        if log:
            log.close()
        if manos:
            manos.parar()
        try:
            if creada:
                e1._borrar_base(base)
        finally:
            os.environ.pop("PROYECTOS_E1_BASE", None)
            shutil.rmtree(tmp, ignore_errors=True)


def _estado_final(project_id: int) -> dict:
    conn = _conexion_de_lectura()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*), COALESCE(SUM(bytes),0) FROM project_documents WHERE project_id = %s", (project_id,))
            n, b = cur.fetchone()
        return {"filas": int(n), "bytes": int(b), "por_estado": _cuentas_por_estado(conn, project_id)}
    finally:
        conn.close()


def _pid_del_backend(env) -> int:
    """pid del uvicorn: el único proceso que escucha en BACKEND_PORT."""
    r = subprocess.run(["ss", "-ltnpH", f"sport = :{BACKEND_PORT}"], capture_output=True, text=True)
    m = re.search(r"pid=(\d+)", r.stdout)
    if not m:
        raise RuntimeError("no se encontró el pid del backend")
    return int(m.group(1))


def visual() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="visual-proyectos-e2a-"))
    workspace = tmp / "workspace"
    workspace.mkdir()
    sufijo, base = _preparar_base()
    proc = vite = log = manos = None
    creada = False
    parar = {"x": False}
    signal.signal(signal.SIGTERM, lambda *_: parar.update(x=True))
    signal.signal(signal.SIGINT, lambda *_: parar.update(x=True))
    try:
        e1._crear_base_vacia(base)
        creada = True
        e1._armar_esquema(base)
        env = _env_del_backend(tmp, workspace)
        manos = _levantar_manos(env)
        manos.retraso_s = 3600.0   # lo despachado se queda `procesando` mientras se mira
        proc, log = _levantar_backend(env, tmp)
        semilla = sembrar(sufijo, amigable=True)
        sembrar_documentos_visuales(semilla)
        e1._puerto_libre(VITE_PORT)
        nodo = os.environ.get("NODE_BIN", str(Path.home() / ".nvm/versions/node/v24.16.0/bin"))
        venv = dict(os.environ, PATH=f"{nodo}:{os.environ['PATH']}", PROYECTOS_E1_BACKEND_URL=BACKEND_URL)
        vite = subprocess.Popen(["npx", "vite", "--config", "vite.proyectos-e1.config.js", "--host", "127.0.0.1",
                                 "--port", str(VITE_PORT), "--strictPort"], cwd=str(FRONTEND_DIR), env=venv,
                                stdout=open(tmp / "vite.log", "w"), stderr=subprocess.STDOUT, start_new_session=True)
        e1._esperar_puerto(VITE_PORT, 90)
        print(f"\nURL:        http://127.0.0.1:{VITE_PORT}/\nUSUARIO:    {semilla['email_subidor']}\n"
              f"CONTRASEÑA: {PASSWORD_DE_PRUEBA}\nPROYECTO:   /proyectos/{semilla['p_lactovi']} (Lácteos Victoria, OWNER)\n"
              f"BACKEND:    {BACKEND_URL}  ({base})\nPara terminar y limpiar: kill -TERM {os.getpid()}", flush=True)
        while not parar["x"]:
            time.sleep(0.5)
    finally:
        e1._matar(vite)
        e1._matar(proc)
        if log:
            log.close()
        if manos:
            manos.parar()
        try:
            if creada:
                e1._borrar_base(base)
        finally:
            os.environ.pop("PROYECTOS_E1_BASE", None)
            shutil.rmtree(tmp, ignore_errors=True)


class _MuestreoFd:
    """Descriptores abiertos del uvicorn cada 20 ms en un hilo; guarda el maximo y las muestras."""

    def __init__(self, pid: int):
        self.pid = pid
        self.maximo = 0
        self.muestras = 0
        self._parar = threading.Event()
        self._hilo = threading.Thread(target=self._correr, daemon=True)

    def _correr(self):
        while not self._parar.is_set():
            try:
                self.maximo = max(self.maximo, contar_descriptores(self.pid))
                self.muestras += 1
            except OSError:   # fail-soft: el proceso ya no esta; se corta el muestreo
                return
            self._parar.wait(0.02)

    def __enter__(self):
        self._hilo.start()
        return self

    def __exit__(self, *_):
        self._parar.set()
        self._hilo.join(timeout=2)


def _sembrar_reprocesar(semilla: dict, workspace: Path) -> list[dict]:
    """Los 10 usuarios que reprocesan pasan a ser CONTRIBUTOR de LACTOVI; el `fuente/` de LACTOVI se llena
    con `ARCHIVOS_FUENTE` rellenos y un objetivo por usuario (hondo, al final del orden), y por cada objetivo
    hay una fila `sin_extractor` SIN `carpeta_procesado` (la ficha no sirve: se busca por sha256)."""
    plan = plan_de_fuente(ARCHIVOS_FUENTE, SUBCARPETAS_FUENTE)
    fuente = workspace / "proyectos" / semilla["u_lactovi"] / "fuente"
    t0 = time.time()
    escribir_fuente(fuente, plan, TAMANO_FUENTE)
    conn = e1._conectar()
    docs = []
    try:
        ahora = "2026-10-03 00:00:00.000000"
        with conn.cursor() as cur:
            for i, uid in enumerate(semilla["pequenos"][:USUARIOS_REPROCESAR]):
                cur.execute("INSERT INTO jax_project_membership (membership_id, project_id, tenant_id, user_id, project_role, "
                            "status, grant_origin, created_at, created_by, updated_at) "
                            "VALUES (%s,%s,%s,%s,'CONTRIBUTOR','ACTIVE','EXPLICIT',%s,%s,%s)",
                            (str(uuid.uuid4()), semilla["p_lactovi"], TENANT_ID, uid, ahora, f"user:{semilla['subidor']}", ahora))
                ruta_obj = ruta_del_objetivo(i)
                contenido = os.urandom(TAMANO_FUENTE)
                (fuente / ruta_obj).parent.mkdir(parents=True, exist_ok=True)
                (fuente / ruta_obj).write_bytes(contenido)
                cur.execute("INSERT INTO project_documents (project_id, sha256, nombre_original, ruta_entrada, bytes, tipo, "
                            "estado, subido_por) VALUES (%s,%s,%s,%s,%s,'pdf','sin_extractor',%s)",
                            (semilla["p_lactovi"], hashlib.sha256(contenido).hexdigest(), f"Informe-{i}.pdf",
                             f"proyectos/{semilla['u_lactovi']}/fuente/{ruta_obj}", TAMANO_FUENTE, semilla["subidor"]))
                docs.append({"usuario": uid, "documento": cur.lastrowid})
        conn.commit()
    finally:
        conn.close()
    print(f"[fuente] {len(plan)} rellenos + {len(docs)} objetivos de {TAMANO_FUENTE} B en {time.time() - t0:.1f} s", file=sys.stderr)
    return docs


def _reponer(documento: int) -> None:
    """Deja el documento otra vez `sin_extractor` (el despachador ya lo pudo mover): es el arnés, no el endpoint."""
    conn = _conexion_de_lectura()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE project_documents SET estado='sin_extractor', job_id=NULL, error=NULL, "
                        "carpeta_procesado=NULL WHERE id=%s", (documento,))
    finally:
        conn.close()


async def _fase_reprocesar(env, semilla, tmp, docs, res) -> None:
    import httpx
    pid_backend = _pid_del_backend(env)
    usuarios = [{"id": u, "autorizacion": e1._token(env, u)["Authorization"]} for u in semilla["chat"]]
    parar_chat = tmp / "parar-chat"
    config = {"url": BACKEND_URL, "proyecto": semilla["p_chat"], "usuarios": usuarios, "parar": str(parar_chat),
              "salida": str(tmp / "chat-salida.json")}
    chat = _lanzar_chat(config, tmp)
    # Una sola peticion, sin nadie mas (ni chat ni lista ni otros usuarios): lo que cuesta buscar por sha256 solo.
    solitaria = []
    d0 = docs[0]
    for _ in range(5):
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0)) as una:
            t0 = time.perf_counter()
            r = await una.post(f"{BACKEND_URL}/api/proyectos/{semilla['p_lactovi']}/documentos/{d0['documento']}/reprocesar",
                               headers=e1._token(env, d0["usuario"]))
            solitaria.append({"status": r.status_code, "ms": (time.perf_counter() - t0) * 1000})
        await asyncio.to_thread(_reponer, d0["documento"])
    muestras: list[dict] = []          # {status, ms, t}
    lista: list[tuple[float, float]] = []   # (inicio, ms) del GET de la lista de documentos
    errores_lista: dict[str, int] = {}
    parar = asyncio.Event()             # detiene a los que reprocesan
    parar_lista = asyncio.Event()       # detiene a los lectores de la lista, al final de todo
    limits = httpx.Limits(max_connections=USUARIOS_REPROCESAR + CLIENTES_LISTA + 5)
    h_lista = e1._token(env, semilla["subidor"])

    async with httpx.AsyncClient(limits=limits, timeout=httpx.Timeout(120.0, connect=10.0)) as cli:
        async def reprocesador(doc: dict, cabecera: dict):
            url = f"{BACKEND_URL}/api/proyectos/{semilla['p_lactovi']}/documentos/{doc['documento']}/reprocesar"
            while not parar.is_set():
                t0 = time.perf_counter()
                inicio = time.time()
                try:
                    r = await cli.post(url, headers=cabecera)
                    estado, ms = r.status_code, (time.perf_counter() - t0) * 1000
                except Exception as exc:  # fail-soft: una peticion caida cuenta como error y no tumba la corrida
                    estado, ms = f"excepcion:{type(exc).__name__}", (time.perf_counter() - t0) * 1000
                muestras.append({"status": estado, "ms": ms, "t": inicio})
                if estado == 202:
                    await asyncio.to_thread(_reponer, doc["documento"])
                elif estado == 429:
                    await asyncio.sleep(PAUSA_TRAS_429_S)

        async def listador():
            while not parar_lista.is_set():
                t0 = time.perf_counter()
                inicio = time.time()
                try:
                    r = await cli.get(f"{BACKEND_URL}/api/proyectos/{semilla['p_lactovi']}/documentos?limite=50", headers=h_lista)
                    if r.status_code != 200:
                        errores_lista[str(r.status_code)] = errores_lista.get(str(r.status_code), 0) + 1
                        continue
                except Exception as exc:  # fail-soft: cuenta como error
                    errores_lista[type(exc).__name__] = errores_lista.get(type(exc).__name__, 0) + 1
                    continue
                lista.append((inicio, (time.perf_counter() - t0) * 1000))

        try:
            with _MuestreoFd(pid_backend) as fds:
                fds_antes = contar_descriptores(pid_backend)
                lectores = [asyncio.create_task(listador()) for _ in range(CLIENTES_LISTA)]
                await asyncio.sleep(SEGUNDOS_ANTES_R)
                if chat.poll() is not None:
                    raise RuntimeError(f"el hijo de chat murio:\n{chat.stderr.read()[-2000:]}")
                fds_sin_carga = fds.maximo
                t_ini = time.time()
                trabajadores = [asyncio.create_task(reprocesador(d, e1._token(env, d["usuario"]))) for d in docs]
                await asyncio.sleep(SEGUNDOS_REPROCESAR)
                parar.set()
                await asyncio.gather(*trabajadores)
                t_fin = time.time()
                await asyncio.sleep(SEGUNDOS_DESPUES_R)      # la lista y el chat siguen: es el «despues» sin carga
                parar_lista.set()
                await asyncio.gather(*lectores)
                fds_final = contar_descriptores(pid_backend)
            parar_chat.touch()
            _out, err = chat.communicate(timeout=120)
            if chat.returncode != 0:
                raise RuntimeError(f"el hijo de chat fallo:\n{err[-3000:]}")
            datos = json.loads(Path(config["salida"]).read_text())
        finally:
            parar.set()
            parar_lista.set()
            parar_chat.touch()
            if chat.poll() is None:
                chat.kill()

    ventana = [(t_ini, t_fin)]
    turnos = por_fase([(m[0], m[1]) for m in datos["muestras"]], subidas=ventana, procesos=[])
    sin_carga = turnos["antes"] + turnos["reposo"]
    lista_f = por_fase([(t, ms) for t, ms in lista], subidas=ventana, procesos=[])
    lista_sin = lista_f["antes"] + lista_f["reposo"]
    mias = [m for m in muestras]
    seg = t_fin - t_ini
    res["reprocesar"] = {
        "fuente": {"archivos_rellenos": ARCHIVOS_FUENTE, "subcarpetas": SUBCARPETAS_FUENTE, "bytes_por_archivo": TAMANO_FUENTE,
                   "profundidad_del_objetivo": PROFUNDIDAD_OBJETIVO, "ficha": "ninguna (busqueda por sha256)",
                   "nombre_coincide": False},
        "usuarios": len(docs), "segundos": round(seg, 2), "cupo": res.get("cupo"), "pausa_tras_429_s": PAUSA_TRAS_429_S,
        "una_sola_peticion_sin_otra_carga": resumen_de_reprocesar(solitaria, 1.0)["latencia_todas_ms"] | {
            "codigos": resumen_de_reprocesar(solitaria, 1.0)["codigos"], "ms": [round(m["ms"], 1) for m in solitaria]},
        "endpoint": resumen_de_reprocesar(mias, seg),
        "descriptores_del_uvicorn": {"antes": fds_antes, "maximo_sin_carga": fds_sin_carga, "maximo_de_toda_la_corrida": fds.maximo,
                                     "final": fds_final, "muestras": fds.muestras, "LimitNOFILE_de_produccion": 1024},
        "chat_en_paralelo": {"usuarios": len(usuarios), "errores": datos["errores"], "turnos": len(datos["muestras"]),
                             "sin_carga_ms": _resumen_fase([m for m in sin_carga]),
                             "durante_reprocesar_ms": _resumen_fase(turnos["subida"]),
                             "veredicto": e1.veredicto_peor_caso(_resumen_fase(sin_carga), _resumen_fase(turnos["subida"]),
                                                                 sum(datos["errores"].values()))},
        "lista_en_paralelo": {"clientes": CLIENTES_LISTA, "errores": errores_lista, "peticiones": len(lista),
                              "sin_carga_ms": _resumen_fase(lista_sin), "durante_reprocesar_ms": _resumen_fase(lista_f["subida"]),
                              "veredicto": e1.veredicto_peor_caso(_resumen_fase(lista_sin), _resumen_fase(lista_f["subida"]),
                                                                  sum(errores_lista.values()))}}


async def medir_reprocesar() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="carga-reprocesar-e2a-"))
    workspace = tmp / "workspace"
    workspace.mkdir()
    sufijo, base = _preparar_base()
    proc = log = manos = None
    creada = False
    res: dict = {"fecha": time.strftime("%Y-%m-%d %H:%M:%S %Z"), "base": base,
                 "workspace": "directorio temporal (borrado al terminar)"}
    try:
        e1._crear_base_vacia(base)
        creada = True
        e1._armar_esquema(base)
        env = _env_del_backend(tmp, workspace)
        manos = _levantar_manos(env)
        proc, log = _levantar_backend(env, tmp)
        print(f"[base] {sufijo} ({base}) backend pid={proc.pid} en {BACKEND_URL}", file=sys.stderr)
        semilla = sembrar(sufijo)
        docs = await asyncio.to_thread(_sembrar_reprocesar, semilla, workspace)
        conn = _conexion_de_lectura()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT config_key, config_value FROM axioma_config WHERE config_key IN (%s, %s)",
                            ("proyectos.documentos.subidas_por_usuario", "proyectos.documentos.subidas_globales"))
                res["cupo"] = dict(cur.fetchall())
        finally:
            conn.close()
        await _fase_reprocesar(env, semilla, tmp, docs, res)
        res["manos_falso"] = {"trabajos_recibidos": manos.trabajos_recibidos, "rutas_recibidas": manos.rutas_recibidas}
        texto = (tmp / "backend.log").read_text(errors="replace")
        res["backend_log"] = {"lineas_error": len(re.findall(r"\bERROR\b", texto)), "tracebacks": len(re.findall(r"Traceback", texto)),
                              "deadlock_1213": len(re.findall(r"\b1213\b|Deadlock", texto)),
                              "lock_timeout_1205": len(re.findall(r"\b1205\b|Lock wait timeout", texto)),
                              "fuente_ilegible": len(re.findall(r"fuente/ ilegible", texto))}
        salida = ruta_de_resultados().with_name("_resultados_proyectos_e2a_reprocesar.json")
        salida.write_text(json.dumps(res, indent=2, ensure_ascii=False, default=str))
        print(f"[orquestador] {salida} escrito")
    finally:
        e1._matar(proc)
        if log:
            log.close()
        if manos:
            manos.parar()
        try:
            if creada:
                e1._borrar_base(base)
        finally:
            os.environ.pop("PROYECTOS_E1_BASE", None)
            shutil.rmtree(tmp, ignore_errors=True)


def _salir_en_sigterm(*_):
    sys.exit(143)   # que corran los `finally`: la limpieza es siempre


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "medir"
    if cmd == "medir":
        signal.signal(signal.SIGTERM, _salir_en_sigterm)
        asyncio.run(medir())
    elif cmd == "reprocesar":
        signal.signal(signal.SIGTERM, _salir_en_sigterm)
        asyncio.run(medir_reprocesar())
    elif cmd == "visual":
        visual()
    elif cmd == "limpiar":
        suf = sys.argv[2] if len(sys.argv) > 2 else None
        os.environ["PROYECTOS_E1_BASE"] = e1.base_de_peor_caso(suf)
        e1._borrar_base(e1.base_de_peor_caso(suf))
    elif cmd == "--chat":
        asyncio.run(_chat_hijo(json.loads(Path(sys.argv[2]).read_text())))
    elif cmd == "--explain":
        print(json.dumps(asyncio.run(_explain(json.loads(sys.argv[2])))))
    else:
        raise SystemExit("uso: proyectos_e2a.py [medir|reprocesar|visual|limpiar SUFIJO]")
