"""Carga de Task 8 (descartar-pipelines, 2026-09-22) -- ORQUESTADOR.

LAS CUATRO #4, decisión del coordinador: mide `GET /api/pipelines` y
`GET /api/pipelines?estado=discarded` contra la base de TEST (nunca
producción), con un usuario sembrado A ESCALA (>= 5.000 pipelines, 20 %
descartados) y otro con la forma EXTREMA (pocos vivos, muchos descartados) --
`descartados_seed.py`. Levanta un Jacobs falso HTTP real
(`historial_fake_jacobs.py`, reusado tal cual: ninguno de los dos endpoints
medidos llama a Jacobs, pero el backend arranca con su URL configurada) y el
backend REAL de este repo (`main:app`, un solo proceso uvicorn,
127.0.0.1:BACKEND_PORT -- la MISMA topología que `jax-platform.service` en
producción), mide con `httpx.AsyncClient` real (HTTP de punta a punta) los
dos endpoints a cada nivel de concurrencia, para los DOS usuarios, y al
terminar (éxito o error) SIEMPRE limpia lo que sembró
(`descartados_limpiar.py`) y mata los dos procesos.

Mismo arnés de seguridad que `historial_orquestar.py` (Task 10, 2026-09-18):
puertos de carga distintos de los de producción, `JAX_DB_NAME` fijo a
`jax_memory_test`, JWT de la corrida generado al vuelo y NUNCA igual al de
producción (jax-platform#146, ronda 7) -- ver `_abortar_si_el_secreto_de_carga_coincide_con_produccion`.

USO (desde la raíz del repo, con backend/requirements.txt instalado y
JAX_REPO_PATH apuntando a un checkout de jax con jax#257+jax#259 --
ver descartados_seed.py):
    JAX_REPO_PATH=/home/fruiz/worktrees/jax-master-para-tests \
    python3 loadtest/descartados_orquestar.py
    CARGA_RAPIDA=1 ... python3 loadtest/descartados_orquestar.py   # solo c=1

Requiere `sudo -n cat /etc/jax/.env` y que `jax_memory_test` exista. NUNCA
toca `jax_memory` ni los puertos 7777/8080 -- `_verificar_no_apunta_a_produccion()`
revienta ANTES de levantar nada si algo los pisa.

Los resultados quedan en loadtest/_descartados_resultados.json (no se
commitea). Los NÚMEROS que respalda
docs/carga-descartados-pipelines-2026-09-22.md salen de la corrida ahí
documentada -- esta corrida es para REPRODUCIR el método.
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
import uuid
from pathlib import Path
from urllib.parse import urlparse

import httpx
from jose import jwt as _jwt

# ---------------------------------------------------------------------------
# CONSTANTES DE LA CORRIDA -- mismos valores/criterio que historial_orquestar.py.
# ---------------------------------------------------------------------------
LOADTEST_DIR = Path(__file__).parent
BACKEND_DIR = LOADTEST_DIR.parent / "backend"

FAKE_JACOBS_PORT = 17778   # distinto del de historial_orquestar.py (17777) -- las dos cargas podrían correr a la vez
BACKEND_PORT = 18081       # ídem (historial usa 18080)
BACKEND_URL = f"http://127.0.0.1:{BACKEND_PORT}"
FAKE_JACOBS_URL = f"http://127.0.0.1:{FAKE_JACOBS_PORT}"

PUERTOS_DE_PRODUCCION = {7777, 8080}
BASE_DE_PRUEBA = "jax_memory_test"

NIVELES_DE_CONCURRENCIA = [1, 25, 50, 100, 150, 200]  # mismo barrido que historial_orquestar.py
N_POR_NIVEL = {1: 200}  # default para el resto: min(2000, c*20)


def _n_para(c: int) -> int:
    return N_POR_NIVEL.get(c, min(2000, c * 20))


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
    """Idéntica a historial_orquestar.py::_abortar_si_el_secreto_de_carga_coincide_con_produccion
    (jax-platform#146, ronda 7) -- copiada, no importada: los dos
    orquestadores son procesos independientes y esta función es la barrera
    de seguridad más importante del archivo, más clara sin una dependencia
    cruzada entre los dos scripts de carga."""
    if not jwt_secret_de_carga or jwt_secret_de_carga == jwt_secret_de_produccion:
        raise SystemExit(
            "el backend de carga esta firmando con la llave de PRODUCCION (o "
            "sin ninguna) -- ABORTANDO, no se mide sobre un entorno que puede "
            "emitir tokens validos tambien contra produccion")


def _verificar_no_apunta_a_produccion(env: dict) -> None:
    if BACKEND_PORT in PUERTOS_DE_PRODUCCION or FAKE_JACOBS_PORT in PUERTOS_DE_PRODUCCION:
        raise RuntimeError(
            f"BACKEND_PORT={BACKEND_PORT} / FAKE_JACOBS_PORT={FAKE_JACOBS_PORT}: "
            f"pisa un puerto de PRODUCCIÓN {PUERTOS_DE_PRODUCCION} -- ABORTANDO.")
    if env.get("JAX_DB_NAME") != BASE_DE_PRUEBA:
        raise RuntimeError(
            f"JAX_DB_NAME={env.get('JAX_DB_NAME')!r} != {BASE_DE_PRUEBA!r} -- "
            f"ABORTANDO, esto NUNCA corre contra jax_memory.")
    for var in ("LAS_MANOS_URL", "JACOBS_URL", "JAX_PLATFORM_URL"):
        host_puerto = urlparse(env.get(var, "")).netloc or env.get(var, "")
        puerto = urlparse(env.get(var, "")).port
        if not host_puerto.startswith("127.0.0.1"):
            raise RuntimeError(f"{var}={env.get(var)!r}: tiene que ser 127.0.0.1 -- ABORTANDO.")
        if puerto in PUERTOS_DE_PRODUCCION:
            raise RuntimeError(f"{var}={env.get(var)!r}: pisa un puerto de PRODUCCIÓN -- ABORTANDO.")


def construir_env(tmp: Path) -> dict:
    env = dict(os.environ)
    env.update(_cargar_env_produccion())
    env["JAX_DB_NAME"] = BASE_DE_PRUEBA
    # SEGURIDAD (jax-platform#146, ronda 7) -- ver la nota de
    # historial_orquestar.py::construir_env, mismo motivo exacto: llave
    # propia de la corrida, nunca la de `_cargar_env_produccion()`.
    env["JAX_JWT_SECRET"] = secrets.token_urlsafe(48)
    env["LAS_MANOS_URL"] = FAKE_JACOBS_URL
    env["JACOBS_URL"] = f"{FAKE_JACOBS_URL}/jacobs"
    env["JAX_PLATFORM_URL"] = BACKEND_URL
    env["JAX_OLLAMA_URL"] = "http://ollama.invalid:11434"
    env["CANARY_INTERVAL_SECONDS"] = "0"
    env["JAX_LAS_MANOS_CREDENCIAL_PLATAFORMA"] = secrets.token_urlsafe(40)
    env["FERNET_KEY"] = base64.urlsafe_b64encode(os.urandom(32)).decode()

    env["JAX_USAGE_SPOOL_DIR"] = str(tmp / "usage-spool")
    env["JAX_FACET_SEAL_PATH"] = str(tmp / "facet-cache-seal")
    env["JAX_KILL_SWITCH_PATH"] = str(tmp / "interruptor" / "PAUSE")
    env["JAX_EJECUTOR_PAUSA"] = str(tmp / "ejecutor-pausa" / "PAUSA")
    env["JAX_EJECUTOR_PYTHON"] = str(tmp / "no-existe" / "python")
    env["JAX_MISSIONS_DIR"] = str(tmp / "missions")
    env["JAX_REPO_BASE"] = str(tmp / "repo")
    env["JAX_AUDIT_LOG_PATH"] = str(tmp / "audit.jsonl")
    env["JAX_BIN"] = str(tmp / "bin" / "jax")
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
        "JAX_SEED_SUPERADMIN_EMAIL": "superadmin-semilla-carga-descartados@example.invalid",
        "JAX_SEED_TENANT_NAME": "Tenant de la carga de Task 8 (descartar-pipelines)",
    }.items():
        env[k] = v

    env["PYTHONPATH"] = str(BACKEND_DIR)
    # historial_fake_jacobs.py exige un CARGA_BIG_PIPELINE_ID -- ninguno de
    # los dos endpoints medidos acá lo pide nunca (son listados puros, sin
    # llamar a Jacobs), pero el falso lo necesita para arrancar. Un uuid
    # cualquiera alcanza.
    env["CARGA_BIG_PIPELINE_ID"] = str(uuid.uuid4())
    env["CARGA_FAKE_JACOBS_PORT"] = str(FAKE_JACOBS_PORT)
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
        if r.status_code >= 500:
            return None, True, r.status_code, 0
        return ms, False, r.status_code, len(r.content)
    except Exception:  # fail-soft: una petición que se cae cuenta como error del turno
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
        "c": c, "n": n, "ok": len(latencias), "errores": errores,
        "segundos": round(segundos, 3),
        "rps": round(total / segundos, 2) if segundos > 0 else None,
        "p50_ms": round(percentil(latencias, 50), 2) if latencias else None,
        "p95_ms": round(percentil(latencias, 95), 2) if latencias else None,
        "p99_ms": round(percentil(latencias, 99), 2) if latencias else None,
        "max_ms": round(max(latencias), 2) if latencias else None,
        "codigos": codigos,
    }


def _token_para(user_id: int, tenant_id: str, jwt_secret: str) -> str:
    ahora = int(time.time())
    return _jwt.encode(
        {"user_id": str(user_id), "tenant_id": tenant_id, "role": "operator",
         "tv": 0, "exp": ahora + 3600, "type": "access"},
        jwt_secret, algorithm="HS256",
    )


async def main_async() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="carga-descartados-"))
    seed_json = tmp / "seed_result.json"
    niveles = [1] if os.environ.get("CARGA_RAPIDA") else NIVELES_DE_CONCURRENCIA

    print(f"[orquestador] sembrando (descartados_seed.py) -> {seed_json}")
    subprocess.run([sys.executable, str(LOADTEST_DIR / "descartados_seed.py"), str(seed_json)], check=True)
    seed = json.loads(seed_json.read_text())

    env = construir_env(tmp)
    _verificar_no_apunta_a_produccion(env)

    log_jacobs = open(tmp / "fake_jacobs.log", "w")
    log_backend = open(tmp / "backend.log", "w")
    proc_jacobs = proc_backend = None
    try:
        proc_jacobs = subprocess.Popen(
            [sys.executable, str(LOADTEST_DIR / "historial_fake_jacobs.py")],
            env=env, stdout=log_jacobs, stderr=subprocess.STDOUT, start_new_session=True,
        )
        esperar_puerto("127.0.0.1", FAKE_JACOBS_PORT)
        print(f"[orquestador] fake_jacobs arriba, pid={proc_jacobs.pid}")

        proc_backend = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1",
             "--port", str(BACKEND_PORT), "--log-level", "warning"],
            cwd=str(BACKEND_DIR), env=env, stdout=log_backend, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        esperar_puerto("127.0.0.1", BACKEND_PORT, timeout=60.0)
        r = esperar_http_ok(f"{BACKEND_URL}/api/health", timeout=60.0)
        print(f"[orquestador] backend arriba, pid={proc_backend.pid}, /api/health -> {r.status_code}")

        # VERIFICACIÓN DURA (no confía en construir_env): el proceso REAL
        # corre contra jax_memory_test, no contra producción.
        environ = Path(f"/proc/{proc_backend.pid}/environ").read_bytes()
        pares = dict(p.split(b"=", 1) for p in environ.split(b"\x00") if b"=" in p)
        db_name = pares.get(b"JAX_DB_NAME", b"").decode()
        jacobs_url = pares.get(b"JACOBS_URL", b"").decode()
        if db_name != BASE_DE_PRUEBA:
            raise RuntimeError(f"proceso real con JAX_DB_NAME={db_name!r} -- ABORTANDO")
        if jacobs_url != f"{FAKE_JACOBS_URL}/jacobs":
            raise RuntimeError(f"proceso real con JACOBS_URL={jacobs_url!r} -- ABORTANDO")
        print(f"[orquestador] VERIFICADO /proc/{proc_backend.pid}/environ: "
              f"JAX_DB_NAME={db_name} JACOBS_URL={jacobs_url}")

        # SEGURIDAD (jax-platform#146, ronda 7): el secreto de FIRMA se lee
        # del proceso YA LEVANTADO, nunca del `env` en memoria de este
        # script, y se compara contra el de producción releído aparte.
        jwt_secret_de_carga = pares.get(b"JAX_JWT_SECRET", b"").decode()
        jwt_secret_de_produccion = _cargar_env_produccion().get("JAX_JWT_SECRET")
        _abortar_si_el_secreto_de_carga_coincide_con_produccion(
            jwt_secret_de_carga, jwt_secret_de_produccion)

        resultados = {"seed": {
            k: {kk: vv for kk, vv in v.items() if kk != "todos_los_pipelines"}
            for k, v in seed["usuarios"].items()
        }}

        for etiqueta, datos in seed["usuarios"].items():
            token = _token_para(datos["user_id"], datos["tenant_id"], jwt_secret_de_carga)
            headers = {"Authorization": f"Bearer {token}"}

            # --- Verificación previa: el camino real, no un literal (lección de U5) ---
            r_todos = httpx.get(f"{BACKEND_URL}/api/pipelines", headers=headers, timeout=15.0)
            r_desc = httpx.get(f"{BACKEND_URL}/api/pipelines", headers=headers,
                                params={"estado": "discarded"}, timeout=15.0)
            print(f"[orquestador] usuario={etiqueta}: GET /api/pipelines status={r_todos.status_code} "
                  f"bytes={len(r_todos.content)} pipelines={len(r_todos.json().get('pipelines', []))} "
                  f"has_more={r_todos.json().get('has_more')} | "
                  f"?estado=discarded status={r_desc.status_code} bytes={len(r_desc.content)} "
                  f"pipelines={len(r_desc.json().get('pipelines', []))} has_more={r_desc.json().get('has_more')}")
            if r_todos.status_code != 200 or r_desc.status_code != 200:
                raise RuntimeError(f"la verificación previa para {etiqueta} no dio 200")

            resultados[etiqueta] = {"todos": [], "descartados": []}
            for c in niveles:
                n = _n_para(c)
                r = await correr_tanda(f"{BACKEND_URL}/api/pipelines", headers, c, n)
                print(f"[{etiqueta}/TODOS] c={c} n={n} -> {r}")
                resultados[etiqueta]["todos"].append(r)
            for c in niveles:
                n = _n_para(c)
                r = await correr_tanda(f"{BACKEND_URL}/api/pipelines?estado=discarded", headers, c, n)
                print(f"[{etiqueta}/DESCARTADOS] c={c} n={n} -> {r}")
                resultados[etiqueta]["descartados"].append(r)

        salida = LOADTEST_DIR / "_descartados_resultados.json"
        salida.write_text(json.dumps(resultados, indent=2, ensure_ascii=False))
        print(f"[orquestador] {salida} escrito")

    finally:
        for proc in (proc_backend, proc_jacobs):
            if proc is None:
                continue
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except ProcessLookupError:  # fail-soft: el grupo de procesos ya no existe
                pass
        for proc in (proc_backend, proc_jacobs):
            if proc is None:
                continue
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except ProcessLookupError:  # fail-soft: el SIGTERM ya lo tumbó
                    pass
        log_jacobs.close()
        log_backend.close()
        print("[orquestador] procesos detenidos")

        print("[orquestador] limpiando la siembra (descartados_limpiar.py)...")
        subprocess.run([sys.executable, str(LOADTEST_DIR / "descartados_limpiar.py"), str(seed_json)], check=True)


if __name__ == "__main__":
    asyncio.run(main_async())
