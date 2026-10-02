"""Carga de GET /api/admin/models/sync/estado -- HTTP de punta a punta
(2026-09-27, pedido del coordinador: "el endpoint que se lanza es
GET /admin/models/sync/estado completo, no solo sus SQL").

Levanta un solo proceso `uvicorn main:app` REAL (la MISMA topología que
`jax-platform.service` en producción) contra una base de PRUEBA PROPIA
(nunca `jax_memory` ni `jax_memory_test` a secas -- ver `BASE_DE_PRUEBA`,
con sufijo dedicado, creada por este script y borrada por nombre al
terminar, éxito o error). Mide con `httpx.AsyncClient` real -- auth JWT
incluida contra un superadmin real, sembrado por el propio `run_seed()` de
la app al arrancar (mismo camino que producción, no un atajo) -- en el PEOR
CASO: 200 filas terminadas en `catalogo_sync_ejecucion` (el tope real de
`catalogo_sync_registro.RETENCION_FILAS`) más una fila 'corriendo'.

Mismo método que `loadtest/historial_orquestar.py` (mismo repo, ya
auditado): un proceso uvicorn real, aislado de producción por env (puertos,
rutas, secretos propios), verificado leyendo `/proc/<pid>/environ` del
proceso YA LEVANTADO -- nunca confiando en el `env` en memoria de este
script.

MINOR-9 (auditoría adversarial, 2026-09-27): al pegarle al endpoint REAL
(`sync_estado()`, api/admin/models.py) -- y no directo a las dos consultas
SQL como `catalogo_sync_estado_medir.py` -- cada petición medida acá
EJECUTA de verdad el `UPDATE` de `marcar_huerfanas_interrumpidas()` que
`sync_estado()` corre antes de leer (nunca sólo el SELECT): los números de
abajo YA incluyen ese costo, no hace falta medirlo aparte.

USO (desde la raíz del repo):
    python3 loadtest/catalogo_sync_estado_http_orquestar.py
    CARGA_RAPIDA=1 python3 loadtest/catalogo_sync_estado_http_orquestar.py   # solo c=1, para probar el arnés

Resultado en loadtest/_resultados_catalogo_sync_estado_http.json (no se
commitea). Base de prueba borrada por nombre al final, siempre.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import secrets
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

LOADTEST_DIR = Path(__file__).parent
BACKEND_DIR = LOADTEST_DIR.parent / "backend"

BACKEND_PORT = 18081  # distinto de 18080 (historial_orquestar.py): pueden correr a la vez sin pisarse
BACKEND_URL = f"http://127.0.0.1:{BACKEND_PORT}"
PUERTOS_DE_PRODUCCION = {7777, 8080}

# Sufijo PROPIO y reconocible (nunca `jax_memory_test` a secas, nunca
# `jax_memory`) -- creada por asegurar_base_de_test() (clona el ESQUEMA de
# la plantilla, sin datos) y borrada por nombre en el `finally`.
SUFIJO_DE_BASE = "cargasyncestadohttp"
BASE_DE_PRUEBA = f"jax_memory_test_{SUFIJO_DE_BASE}"

FILAS_TERMINADAS = 200  # RETENCION_FILAS real -- ver catalogo_sync_registro.py

# Niveles de concurrencia: 1 y 25 son los pedidos explícitamente; 50/100/150
# se agregan para poder decir CON CUÁNTOS empieza a degradar, no solo si
# aguanta 25.
NIVELES_DE_CONCURRENCIA = [1, 25, 50, 100, 150]
N_POR_NIVEL = {1: 200, 25: 500}  # default para el resto: min(1000, c*10)


def _n_para(c: int) -> int:
    return N_POR_NIVEL.get(c, min(1000, c * 10))


def _cargar_env_produccion() -> dict:
    r = subprocess.run(["sudo", "-n", "cat", "/etc/jax/.env"], capture_output=True, text=True, check=True)
    env = {}
    for linea in r.stdout.splitlines():
        linea = linea.strip()
        if linea and not linea.startswith("#") and "=" in linea:
            k, _, v = linea.partition("=")
            env[k.strip()] = v.strip()
    return env


def _abortar_si_el_secreto_de_carga_coincide_con_produccion(
        jwt_secret_de_carga: str, jwt_secret_de_produccion: str | None) -> None:
    """Mismo control que historial_orquestar.py -- nunca imprime ninguno de
    los dos secretos."""
    if not jwt_secret_de_carga or jwt_secret_de_carga == jwt_secret_de_produccion:
        raise SystemExit(
            "el backend de carga esta firmando con la llave de PRODUCCION (o "
            "sin ninguna) -- ABORTANDO")


def _verificar_no_apunta_a_produccion(env: dict) -> None:
    if BACKEND_PORT in PUERTOS_DE_PRODUCCION:
        raise RuntimeError(f"BACKEND_PORT={BACKEND_PORT} pisa un puerto de PRODUCCIÓN -- ABORTANDO.")
    if env.get("JAX_DB_NAME") != BASE_DE_PRUEBA or env["JAX_DB_NAME"] in ("jax_memory", "jax_memory_test"):
        raise RuntimeError(f"JAX_DB_NAME={env.get('JAX_DB_NAME')!r} inválido -- ABORTANDO.")
    for var in ("LAS_MANOS_URL", "JACOBS_URL", "JAX_PLATFORM_URL"):
        parsed = urlparse(env.get(var, ""))
        if parsed.hostname != "127.0.0.1" or parsed.port in PUERTOS_DE_PRODUCCION:
            raise RuntimeError(f"{var}={env.get(var)!r}: tiene que ser 127.0.0.1 fuera de puertos de producción -- ABORTANDO.")


def construir_env(tmp: Path, jax_repo_path: str) -> dict:
    """Mismo criterio de aislamiento que backend/tests/conftest.py (líneas
    citadas en los comentarios de cada asignación) y que
    loadtest/historial_orquestar.py -- adaptado: acá NO hace falta un Jacobs
    falso (el endpoint medido no lo toca), así que LAS_MANOS_URL/JACOBS_URL
    apuntan a un puerto que rechaza la conexión al instante
    (127.0.0.1:9, discard -- mismo truco que conftest.py), sin levantar un
    proceso extra."""
    env = dict(os.environ)
    env.update(_cargar_env_produccion())
    env["JAX_DB_NAME"] = BASE_DE_PRUEBA
    env["JAX_JWT_SECRET"] = secrets.token_urlsafe(48)
    env["JAX_REPO_PATH"] = jax_repo_path
    env["JAX_CONFIG_PATH"] = str(Path(jax_repo_path) / "config" / "config.toml")
    env["LAS_MANOS_URL"] = "http://127.0.0.1:9"
    env["JACOBS_URL"] = "http://127.0.0.1:9/jacobs"
    env["JAX_PLATFORM_URL"] = "http://127.0.0.1:9"
    env["JAX_OLLAMA_URL"] = "http://ollama.invalid:11434"
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
        "JAX_SEED_SUPERADMIN_EMAIL": "superadmin-carga-sync-estado@example.invalid",
        "JAX_SEED_TENANT_NAME": "Tenant de la carga de sync/estado",
    }.items():
        env[k] = v

    env["PYTHONPATH"] = str(BACKEND_DIR)
    return env


def esperar_puerto(host: str, port: int, timeout: float = 40.0) -> None:
    import socket
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with socket.create_connection((host, port), timeout=1):
                return
        except OSError:
            time.sleep(0.2)
    raise RuntimeError(f"{host}:{port} no respondió en {timeout}s")


def esperar_http_ok(url: str, timeout: float = 40.0) -> httpx.Response:
    t0 = time.time()
    ultimo = None
    while time.time() - t0 < timeout:
        try:
            r = httpx.get(url, timeout=3.0)
            if r.status_code < 500:
                return r
        except Exception as e:  # fail-soft: sonda de arranque, reintenta hasta el timeout
            ultimo = e
        time.sleep(0.3)
    raise RuntimeError(f"{url} no respondió 2xx/4xx en {timeout}s (último error: {ultimo})")


async def _una(cliente: httpx.AsyncClient, url: str, headers: dict):
    t0 = time.perf_counter()
    try:
        r = await cliente.get(url, headers=headers)
        ms = (time.perf_counter() - t0) * 1000
        if r.status_code >= 400:
            return None, True, r.status_code, 0
        return ms, False, r.status_code, len(r.content)
    except Exception:  # fail-soft: una petición de carga que revienta cuenta como fallo de ESA petición
        return None, True, None, 0


def percentil(valores, p):
    if not valores:
        return None
    ordenados = sorted(valores)
    k = max(1, min(len(ordenados), -(-int(round(p * len(ordenados) / 100.0)))))
    return ordenados[k - 1]


async def correr_tanda(url: str, headers: dict, c: int, n: int) -> dict:
    limits = httpx.Limits(max_connections=c + 5, max_keepalive_connections=c + 5)
    async with httpx.AsyncClient(limits=limits, timeout=30.0) as cliente:
        sem = asyncio.Semaphore(c)
        latencias, codigos = [], {}
        errores = 0

        async def tarea():
            nonlocal errores
            async with sem:
                ms, fallo, code, _size = await _una(cliente, url, headers)
                if fallo:
                    errores += 1
                else:
                    latencias.append(ms)
                codigos[code] = codigos.get(code, 0) + 1

        t0 = time.perf_counter()
        await asyncio.gather(*(tarea() for _ in range(n)))
        segundos = time.perf_counter() - t0

    total = len(latencias) + errores
    return {
        "c": c, "n": n, "ok": len(latencias), "errores": errores, "segundos": round(segundos, 3),
        "rps": round(total / segundos, 2) if segundos > 0 else None,
        "p50_ms": round(percentil(latencias, 50), 2) if latencias else None,
        "p95_ms": round(percentil(latencias, 95), 2) if latencias else None,
        "p99_ms": round(percentil(latencias, 99), 2) if latencias else None,
        "max_ms": round(max(latencias), 2) if latencias else None,
        "codigos": codigos,
    }


async def _abrir_candado_sostenido(env: dict):
    """MAJOR-A (tercera ronda de la auditoría adversarial, 2026-09-27):
    sostiene, en una conexión DEDICADA que vive durante TODA la medición, el
    mismo candado que `model_catalog.sync_all()` sostiene durante un sync
    real (`_NOMBRE_CANDADO_SYNC`). El criterio de huérfana ahora es "candado
    de trabajo libre Y `iniciado_en` más viejo que el margen de gracia"
    (`catalogo_sync_registro.MARGEN_GRACIA_SEGUNDOS`, 60s) -- sin este
    candado sostenido, la fila 'corriendo' sembrada por `_sembrar_peor_caso`
    se marcaría 'error' en cuanto pasara ese margen, mucho antes de terminar
    los 5 niveles de concurrencia, y el peor caso medido ("200 terminadas +
    1 corriendo") dejaría de sostenerse durante la corrida completa. Quien
    abre esta conexión la cierra (eso libera el candado).

    MAJOR-1 (quinta ronda de la auditoría adversarial, 2026-09-27): el
    nombre pasa por `model_catalog.nombre_candado()` -- MINOR-1 de la ronda
    anterior calificó el candado real con la base actual
    (`jax_catalogo_sync:<base>`), y este script seguía tomando el nombre
    SIN calificar. `marcar_huerfanas_interrumpidas()` mira el calificado,
    lo ve libre desde el minuto cero, y a los 60s (el margen de gracia)
    marca 'error' la fila 'corriendo' sembrada -- la medición de c=25 en
    adelante corría contra "200 terminadas, ninguna corriendo" (el caso
    FÁCIL), no contra el peor caso que el script dice medir, sin ningún
    aviso de que el escenario había cambiado a mitad de camino."""
    import aiomysql
    sys.path.insert(0, str(BACKEND_DIR))
    from model_catalog import _NOMBRE_CANDADO_SYNC, nombre_candado

    conn = await aiomysql.connect(
        host=env["JAX_DB_HOST"], port=int(env.get("JAX_DB_PORT", 3306)),
        user=env["JAX_DB_USER"], password=env["JAX_DB_PASSWORD"],
        db=env["JAX_DB_NAME"], autocommit=True,
    )
    async with conn.cursor() as cur:
        candado = await nombre_candado(cur, _NOMBRE_CANDADO_SYNC)
        await cur.execute("SELECT GET_LOCK(%s, 5)", (candado,))
        (obtenido,) = await cur.fetchone()
    if obtenido != 1:
        conn.close()
        raise RuntimeError(
            f"no se pudo tomar el candado de trabajo ({candado}) para sostenerlo durante la carga")
    return conn


def _detectar_degradacion(resultados: list[dict], p95_base_ms: float) -> str:
    """Primer nivel cuyo p95 pasa 3x el de c=1, o cuyos errores no son cero
    -- criterio simple y explícito, no "se ve peor" a ojo."""
    for r in resultados:
        if r["errores"]:
            return f"c={r['c']}: {r['errores']} error(es)"
        if r["p95_ms"] is not None and p95_base_ms and r["p95_ms"] > 3 * p95_base_ms:
            return f"c={r['c']}: p95={r['p95_ms']}ms > 3x el de c=1 ({p95_base_ms}ms)"
    return "ninguno de los niveles medidos degrada por este criterio"


async def _sembrar_peor_caso(env: dict) -> None:
    """Filas de catalogo_sync_ejecucion -- conexión propia y corta, con las
    credenciales de conexión que el propio backend usa (env ya construido)."""
    import aiomysql

    pool = await aiomysql.create_pool(
        host=env["JAX_DB_HOST"], port=int(env.get("JAX_DB_PORT", 3306)),
        user=env["JAX_DB_USER"], password=env["JAX_DB_PASSWORD"],
        db=env["JAX_DB_NAME"], minsize=1, maxsize=5, autocommit=False,
    )
    try:
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                for i in range(FILAS_TERMINADAS):
                    await cur.execute(
                        "INSERT INTO catalogo_sync_ejecucion "
                        "(origen, estado, pasos_total, paso_actual, iniciado_en, terminado_en, resultado) "
                        "VALUES ('manual', 'ok', 9, 9, UTC_TIMESTAMP() - INTERVAL %s SECOND, "
                        "UTC_TIMESTAMP() - INTERVAL %s SECOND, %s)",
                        (FILAS_TERMINADAS - i + 10, FILAS_TERMINADAS - i, json.dumps({"ok": True})),
                    )
                # iniciado_en=UTC_TIMESTAMP() (fresco, MAJOR-A tercera ronda):
                # el endpoint real interrumpe huérfanas en CADA GET -- el
                # criterio nuevo es candado de trabajo libre Y `iniciado_en`
                # más viejo que el margen de gracia. El candado
                # (`jax_catalogo_sync`) está libre de verdad durante toda esta
                # medición (nada llama a `model_catalog.sync_all()`), así que
                # sin un `iniciado_en` fresco esta fila se marcaría 'error' en
                # la primera petición medida y el peor caso ("200 terminadas +
                # 1 corriendo") dejaría de sostenerse durante toda la corrida.
                await cur.execute(
                    "INSERT INTO catalogo_sync_ejecucion "
                    "(origen, estado, pasos_total, paso_actual, iniciado_en) "
                    "VALUES ('programado', 'corriendo', 9, 4, UTC_TIMESTAMP())"
                )
            await conn.commit()
    finally:
        pool.close()
        await pool.wait_closed()


def _preparar_base_de_prueba(tmp: Path, jax_repo_dir: Path) -> None:
    """SÍNCRONA a propósito: `fijar_base_de_test()`/`asegurar_base_de_test()`
    corren su PROPIO `asyncio.run()` por dentro -- llamarlas desde DENTRO del
    loop de `main_async()` revienta con "cannot be called from a running
    event loop" (mismo bug ya encontrado y corregido en
    `catalogo_sync_estado_medir.py`). Por eso esta función se llama ANTES de
    `asyncio.run(main_async(...))`, nunca desde adentro."""
    print(f"[orquestador] clonando jax (checkout propio) -> {jax_repo_dir}")
    subprocess.run(
        ["git", "clone", "--depth=1", "--branch", "master",
         "https://github.com/fjruizhn/Jax.git", str(jax_repo_dir)],
        check=True, capture_output=True,
    )

    print(f"[orquestador] preparando la base de prueba {BASE_DE_PRUEBA} (esquema clonado, sin datos)")
    sys.path.insert(0, str(BACKEND_DIR))
    _preparar_env_para_migraciones(str(jax_repo_dir))
    os.environ["JAX_TEST_DB_SUFIJO"] = SUFIJO_DE_BASE
    from base_de_test import asegurar_base_de_test, fijar_base_de_test
    fijar_base_de_test()
    if os.environ.get("JAX_DB_NAME") != BASE_DE_PRUEBA:
        raise RuntimeError(f"fijar_base_de_test() resolvió {os.environ.get('JAX_DB_NAME')!r}, no {BASE_DE_PRUEBA!r}")
    asegurar_base_de_test(BASE_DE_PRUEBA)


async def main_async(tmp: Path, jax_repo_dir: Path) -> None:
    niveles = [1] if os.environ.get("CARGA_RAPIDA") else NIVELES_DE_CONCURRENCIA

    env = construir_env(tmp, str(jax_repo_dir))
    _verificar_no_apunta_a_produccion(env)

    print(f"[orquestador] sembrando el peor caso ({FILAS_TERMINADAS} filas terminadas + 1 corriendo)")
    await _sembrar_peor_caso(env)

    print("[orquestador] tomando el candado de trabajo real -- lo sostiene toda la medición")
    conn_candado = await _abrir_candado_sostenido(env)

    log_backend = open(tmp / "backend.log", "w")
    proc_backend = None
    try:
        proc_backend = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1",
             "--port", str(BACKEND_PORT), "--log-level", "warning"],
            cwd=str(BACKEND_DIR), env=env, stdout=log_backend, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        esperar_puerto("127.0.0.1", BACKEND_PORT, timeout=60.0)
        r = esperar_http_ok(f"{BACKEND_URL}/api/health", timeout=60.0)
        print(f"[orquestador] backend arriba, pid={proc_backend.pid}, /api/health -> {r.status_code}")

        environ = Path(f"/proc/{proc_backend.pid}/environ").read_bytes()
        pares = dict(p.split(b"=", 1) for p in environ.split(b"\x00") if b"=" in p)
        db_name = pares.get(b"JAX_DB_NAME", b"").decode()
        if db_name != BASE_DE_PRUEBA:
            raise RuntimeError(f"proceso real con JAX_DB_NAME={db_name!r} -- ABORTANDO")
        print(f"[orquestador] VERIFICADO /proc/{proc_backend.pid}/environ: JAX_DB_NAME={db_name}")

        jwt_secret_de_carga = pares.get(b"JAX_JWT_SECRET", b"").decode()
        jwt_secret_de_produccion = _cargar_env_produccion().get("JAX_JWT_SECRET")
        _abortar_si_el_secreto_de_carga_coincide_con_produccion(jwt_secret_de_carga, jwt_secret_de_produccion)

        # run_seed() (lifespan de main.py, ya corrido al levantar el proceso
        # de arriba) sembró jax_tenants(1)/jax_users(1) con role='superadmin'
        # -- MISMO camino que producción, no un atajo. Se mintea el JWT acá,
        # con el secreto de CARGA leído de /proc de arriba.
        from jose import jwt as _jwt
        ahora = int(time.time())
        token = _jwt.encode(
            {"user_id": "1", "tenant_id": "1", "role": "superadmin",
             "tv": 0, "exp": ahora + 3600, "type": "access"},
            jwt_secret_de_carga, algorithm="HS256",
        )
        headers = {"Authorization": f"Bearer {token}"}

        url = f"{BACKEND_URL}/api/admin/models/sync/estado"
        r_verif = httpx.get(url, headers=headers, timeout=15.0)
        cuerpo = r_verif.json()
        print(f"[orquestador] GET {url} (verificación): status={r_verif.status_code} "
              f"corriendo={cuerpo.get('corriendo') is not None} "
              f"ultima_id={(cuerpo.get('ultima') or {}).get('id')}")
        if r_verif.status_code != 200 or cuerpo.get("corriendo") is None or cuerpo.get("ultima") is None:
            raise RuntimeError(
                f"la verificación previa no dio el peor caso esperado (corriendo + última): {cuerpo}")

        resultados = {"verificacion": {
            "status": r_verif.status_code, "corriendo_id": cuerpo["corriendo"]["id"],
            "ultima_id": cuerpo["ultima"]["id"], "filas_terminadas_sembradas": FILAS_TERMINADAS,
        }, "medidas": []}

        for c in niveles:
            n = _n_para(c)
            r = await correr_tanda(url, headers, c, n)
            print(f"[sync/estado] c={c} n={n} -> {r}")
            resultados["medidas"].append(r)

        # MAJOR-1 (quinta ronda de la auditoría adversarial, 2026-09-27): la
        # verificación de ARRIBA (antes del loop) sólo probaba que el peor
        # caso ESTABA sembrado al principio -- el defecto real (candado sin
        # calificar, ya arreglado arriba) hacía que la fila 'corriendo' se
        # cayera a 'error' A MITAD de la corrida, sin que nada lo notara: la
        # medición de c=25 en adelante corría contra el caso FÁCIL (nada
        # corriendo) y el script terminaba igual, en verde, con números que
        # no medían lo que decía medir. Se repite la MISMA comprobación
        # DESPUÉS del loop -- si la fila ya no está 'corriendo', el script
        # tiene que fallar fuerte, no reportar números silenciosamente
        # inválidos.
        r_verif_final = httpx.get(url, headers=headers, timeout=15.0)
        cuerpo_final = r_verif_final.json()
        if r_verif_final.status_code != 200 or cuerpo_final.get("corriendo") is None:
            raise RuntimeError(
                "la fila 'corriendo' sembrada ya NO estaba 'corriendo' al terminar la medición -- "
                f"el peor caso se perdió a mitad de la corrida (candado sin calificar, huérfanas "
                f"espurias, u otra causa): {cuerpo_final}")
        if cuerpo_final["corriendo"]["id"] != resultados["verificacion"]["corriendo_id"]:
            raise RuntimeError(
                "la fila 'corriendo' al final es OTRA distinta de la sembrada -- "
                f"esperada id={resultados['verificacion']['corriendo_id']}, "
                f"vista id={cuerpo_final['corriendo']['id']}")
        resultados["verificacion_final"] = {
            "status": r_verif_final.status_code, "corriendo_id": cuerpo_final["corriendo"]["id"],
        }
        print(f"[orquestador] verificación final: la fila 'corriendo' (id={cuerpo_final['corriendo']['id']}) "
              "sigue sembrada -- el peor caso se sostuvo toda la corrida")

        p95_base = next((r["p95_ms"] for r in resultados["medidas"] if r["c"] == 1), None)
        resultados["degradacion"] = _detectar_degradacion(resultados["medidas"], p95_base)
        print(f"[orquestador] degradación: {resultados['degradacion']}")

        salida = LOADTEST_DIR / "_resultados_catalogo_sync_estado_http.json"
        salida.write_text(json.dumps(resultados, indent=2, ensure_ascii=False))
        print(f"[orquestador] {salida} escrito")

    finally:
        if proc_backend is not None:
            try:
                os.killpg(os.getpgid(proc_backend.pid), signal.SIGTERM)
            except ProcessLookupError:  # fail-soft: ya había terminado solo
                pass
            try:
                proc_backend.wait(timeout=10)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(proc_backend.pid), signal.SIGKILL)
                except ProcessLookupError:  # fail-soft: el SIGTERM ya lo tumbó entre el timeout y este SIGKILL
                    pass
        log_backend.close()
        print("[orquestador] backend detenido")

        conn_candado.close()
        print("[orquestador] candado de trabajo liberado")

        print(f"[orquestador] borrando la base de prueba {BASE_DE_PRUEBA} por nombre")
        await _borrar_base_de_prueba(env)


def _preparar_env_para_migraciones(jax_repo_path: str) -> None:
    """Lo mínimo que fijar_base_de_test()/asegurar_base_de_test() (SÍNCRONAS,
    su propio asyncio.run() por dentro -- por eso van fuera del loop de
    main_async) necesitan: credenciales reales de conexión (mismo origen que
    tests/entorno_de_produccion.cargar, sudo -n cat) y JAX_REPO_PATH -- las
    migraciones B9 lo exigen."""
    r = subprocess.run(["sudo", "-n", "cat", "/etc/jax/.env"], capture_output=True, text=True, check=True)
    for linea in r.stdout.splitlines():
        linea = linea.strip()
        if linea and not linea.startswith("#") and "=" in linea:
            k, _, v = linea.partition("=")
            os.environ.setdefault(k.strip(), v.strip())
    os.environ["JAX_REPO_PATH"] = jax_repo_path
    os.environ["JAX_CONFIG_PATH"] = str(Path(jax_repo_path) / "config" / "config.toml")


async def _borrar_base_de_prueba(env: dict) -> None:
    import aiomysql

    nombre = env["JAX_DB_NAME"]
    if not nombre.startswith("jax_memory_test_") or nombre in ("jax_memory", "jax_memory_test"):
        raise RuntimeError(f"me niego a hacer DROP DATABASE de {nombre!r} -- no tiene forma de base de prueba propia")
    conn = await aiomysql.connect(
        host=env["JAX_DB_HOST"], port=int(env.get("JAX_DB_PORT", 3306)),
        user=env["JAX_DB_USER"], password=env["JAX_DB_PASSWORD"], autocommit=True,
    )
    try:
        async with conn.cursor() as cur:
            await cur.execute(f"DROP DATABASE IF EXISTS `{nombre}`")
    finally:
        conn.close()


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="carga-sync-estado-http-"))
    jax_repo_dir = tmp / "jax-repo"
    _preparar_base_de_prueba(tmp, jax_repo_dir)
    asyncio.run(main_async(tmp, jax_repo_dir))


if __name__ == "__main__":
    main()
