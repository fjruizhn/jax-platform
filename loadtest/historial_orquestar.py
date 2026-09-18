"""Carga del historial de pipelines (Task 10, 2026-09-18) -- ORQUESTADOR.

Reproduce la corrida de `docs/carga-historial-2026-09-18.md`: siembra el peor
caso (`historial_seed.py`), levanta un Jacobs falso HTTP real
(`historial_fake_jacobs.py`, 127.0.0.1:FAKE_JACOBS_PORT) y el backend REAL de
este repo (`main:app`, un solo proceso uvicorn, 127.0.0.1:BACKEND_PORT --
la MISMA topología que `jax-platform.service` en producción), mide con
`httpx.AsyncClient` real (HTTP de punta a punta, nunca ASGITransport en
proceso) los dos endpoints de abajo a cada nivel de concurrencia, y al
terminar (éxito o error) SIEMPRE limpia lo que sembró
(`historial_limpiar.py`) y mata los dos procesos.

ENDPOINTS MEDIDOS:
    GET /api/pipelines                        (listado, peor caso: 50/página con causa+costo)
    GET /api/pipelines/{big_pipeline_id}/results   (detalle, peor caso: 6 pasos ~80.000 chars c/u)

USO (desde la raíz del repo, con las dependencias de backend/requirements.txt
instaladas -- fastapi, uvicorn, httpx, pymysql, bcrypt, python-jose):
    python3 loadtest/historial_orquestar.py
    CARGA_RAPIDA=1 python3 loadtest/historial_orquestar.py   # solo c=1, para probar el arnés

Requiere `sudo -n cat /etc/jax/.env` (credenciales de conexión) y que
`jax_memory_test` exista con el esquema vigente. NUNCA toca `jax_memory` ni
los puertos 7777/8080 -- ver `_verificar_no_apunta_a_produccion()`, que
revienta ANTES de levantar nada si algo los pisa.

Los resultados quedan en loadtest/_resultados.json (no se commitea: es
la salida de una corrida, no la herramienta). Los NÚMEROS que respalda
docs/carga-historial-2026-09-18.md salen de la corrida del 2026-09-18
09:56-09:58 CST -- esta corrida es para REPRODUCIR el método, no reemplaza
esos números salvo que se documenten aparte, con fecha (LAS CUATRO DEL
RENDIMIENTO: una medición vieja es una VERDAD OPERACIONAL caducada).
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

# ---------------------------------------------------------------------------
# CONSTANTES DE LA CORRIDA -- visibles acá, no enterradas en el cuerpo. Los
# mismos valores que documenta docs/carga-historial-2026-09-18.md.
# ---------------------------------------------------------------------------
LOADTEST_DIR = Path(__file__).parent
BACKEND_DIR = LOADTEST_DIR.parent / "backend"

FAKE_JACOBS_PORT = 17777
BACKEND_PORT = 18080
BACKEND_URL = f"http://127.0.0.1:{BACKEND_PORT}"
FAKE_JACOBS_URL = f"http://127.0.0.1:{FAKE_JACOBS_PORT}"

# Puertos de PRODUCCIÓN: LAS MANOS real (7777) y jax-platform real (8080).
# Ninguno de los dos puertos de arriba puede coincidir con estos -- si
# alguien los cambia sin querer, _verificar_no_apunta_a_produccion() revienta
# antes de levantar nada.
PUERTOS_DE_PRODUCCION = {7777, 8080}
BASE_DE_PRUEBA = "jax_memory_test"  # la ÚNICA base contra la que corre esto

NIVELES_DE_CONCURRENCIA = [1, 25, 50, 100, 150, 200]  # 25 = valor de rondas anteriores del proyecto
N_POR_NIVEL_LISTA = {1: 200}       # default para el resto: min(2000, c*20) -- ver _n_para()
N_POR_NIVEL_DETALLE = {1: 100}     # default para el resto: min(1000, c*10) -- ver _n_para()


def _n_para(c: int, tope: int, factor: int, especiales: dict) -> int:
    return especiales.get(c, min(tope, c * factor))


def _cargar_env_produccion() -> dict:
    r = subprocess.run(["sudo", "-n", "cat", "/etc/jax/.env"], capture_output=True, text=True, check=True)
    env = {}
    for linea in r.stdout.splitlines():
        linea = linea.strip()
        if linea and not linea.startswith("#") and "=" in linea:
            k, _, v = linea.partition("=")
            env[k.strip()] = v.strip()
    return env


def _verificar_no_apunta_a_produccion(env: dict) -> None:
    """Falla RUIDOSO antes de levantar nada si algo, por lo que sea, quedó
    apuntando a producción. No confía en que las constantes de arriba estén
    bien: vuelve a mirar los valores que de verdad van a ir al subprocess."""
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


def construir_env(seed: dict, tmp: Path) -> dict:
    env = dict(os.environ)
    env.update(_cargar_env_produccion())
    # AISLAMIENTO -- mismo método que backend/tests/conftest.py y que la
    # ronda de carga anterior (docs/carga-prevuelo-y-continuar-2026-09-17.md).
    env["JAX_DB_NAME"] = BASE_DE_PRUEBA
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
    env["JAX_EJECUTOR_PYTHON"] = str(tmp / "no-existe" / "python")  # nunca puede lanzar nada real
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
        "JAX_SEED_SUPERADMIN_EMAIL": "superadmin-semilla-carga@example.invalid",
        "JAX_SEED_TENANT_NAME": "Tenant de la carga de Task 10",
    }.items():
        env[k] = v

    env["PYTHONPATH"] = str(BACKEND_DIR)
    env["CARGA_BIG_PIPELINE_ID"] = seed["big_pipeline_id"]
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
        except Exception as e:  # noqa: BLE001 -- reintento hasta el timeout
            ultimo = e
        time.sleep(0.3)
    raise RuntimeError(f"{url} no respondió 2xx/4xx en {timeout}s (último error: {ultimo})")


async def _una(cliente: httpx.AsyncClient, metodo: str, url: str, headers: dict):
    t0 = time.perf_counter()
    try:
        r = await cliente.request(metodo, url, headers=headers)
        ms = (time.perf_counter() - t0) * 1000
        if r.status_code >= 500:
            return None, True, r.status_code, 0
        return ms, False, r.status_code, len(r.content)
    except Exception:
        return None, True, None, 0


def percentil(valores, p):
    if not valores:
        return None
    ordenados = sorted(valores)
    k = max(1, min(len(ordenados), -(-int(round(p * len(ordenados) / 100.0)))))
    return ordenados[k - 1]


async def correr_tanda(url: str, headers: dict, c: int, n: int, metodo: str = "GET") -> dict:
    limits = httpx.Limits(max_connections=c + 5, max_keepalive_connections=c + 5)
    async with httpx.AsyncClient(limits=limits, timeout=30.0) as cliente:
        sem = asyncio.Semaphore(c)
        latencias, tamanos, codigos = [], [], {}
        errores = 0

        async def tarea():
            nonlocal errores
            async with sem:
                ms, fallo, code, size = await _una(cliente, metodo, url, headers)
                if fallo:
                    errores += 1
                else:
                    latencias.append(ms)
                    tamanos.append(size)
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
        "bytes_min": min(tamanos) if tamanos else None,
        "bytes_max": max(tamanos) if tamanos else None,
        "bytes_promedio": round(sum(tamanos) / len(tamanos), 0) if tamanos else None,
    }


async def main_async() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="carga-historial-"))
    seed_json = tmp / "seed_result.json"
    niveles = [1] if os.environ.get("CARGA_RAPIDA") else NIVELES_DE_CONCURRENCIA

    print(f"[orquestador] sembrando el peor caso (historial_seed.py) -> {seed_json}")
    subprocess.run([sys.executable, str(LOADTEST_DIR / "historial_seed.py"), str(seed_json)], check=True)
    seed = json.loads(seed_json.read_text())

    env = construir_env(seed, tmp)
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

        from jose import jwt as _jwt  # el mismo secreto que usa auth/jwt.py
        ahora = int(time.time())
        token = _jwt.encode(
            {"user_id": str(seed["user_id"]), "tenant_id": "1", "role": "operator",
             "tv": 0, "exp": ahora + 3600, "type": "access"},
            env["JAX_JWT_SECRET"], algorithm="HS256",
        )
        headers = {"Authorization": f"Bearer {token}"}

        # --- Verificación previa: el camino real, no un literal (lección de U5) ---
        r_list = httpx.get(f"{BACKEND_URL}/api/pipelines", headers=headers, timeout=15.0)
        cuerpo_list = r_list.json()
        n_pipelines = len(cuerpo_list.get("pipelines", []))
        con_causa = sum(1 for p in cuerpo_list["pipelines"] if p.get("causa"))
        con_costo = sum(1 for p in cuerpo_list["pipelines"] if p.get("costo_usd") is not None)
        print(f"[orquestador] GET /api/pipelines (verificación): status={r_list.status_code} "
              f"bytes={len(r_list.content)} pipelines={n_pipelines} has_more={cuerpo_list.get('has_more')} "
              f"con_causa={con_causa} con_costo_usd={con_costo}")
        if not (r_list.status_code == 200 and n_pipelines == 50 and cuerpo_list["has_more"] is True
                and con_causa == 50 and con_costo == 50):
            raise RuntimeError("la verificación previa de /api/pipelines no dio el peor caso esperado")

        url_detalle = f"{BACKEND_URL}/api/pipelines/{seed['big_pipeline_id']}/results"
        r_det = httpx.get(url_detalle, headers=headers, timeout=15.0)
        cuerpo_det = r_det.json()
        n_pasos = len(cuerpo_det.get("steps", []))
        print(f"[orquestador] GET /api/pipelines/{{id}}/results (verificación): "
              f"status={r_det.status_code} bytes={len(r_det.content)} pasos={n_pasos}")
        if not (r_det.status_code == 200 and n_pasos == 6 and len(r_det.content) > 400_000):
            raise RuntimeError("la verificación previa del detalle no dio el peor caso esperado")

        resultados = {"verificacion": {
            "lista_bytes": len(r_list.content), "lista_pipelines": n_pipelines,
            "lista_con_causa": con_causa, "lista_con_costo": con_costo,
            "detalle_bytes": len(r_det.content), "detalle_pasos": n_pasos,
        }, "lista": [], "detalle": []}

        for c in niveles:
            n = _n_para(c, 2000, 20, N_POR_NIVEL_LISTA)
            r = await correr_tanda(f"{BACKEND_URL}/api/pipelines", headers, c, n)
            print(f"[LISTA] c={c} n={n} -> {r}")
            resultados["lista"].append(r)

        for c in niveles:
            n = _n_para(c, 1000, 10, N_POR_NIVEL_DETALLE)
            r = await correr_tanda(url_detalle, headers, c, n)
            print(f"[DETALLE] c={c} n={n} -> {r}")
            resultados["detalle"].append(r)

        salida = LOADTEST_DIR / "_resultados.json"
        salida.write_text(json.dumps(resultados, indent=2, ensure_ascii=False))
        print(f"[orquestador] {salida} escrito")

    finally:
        for proc in (proc_backend, proc_jacobs):
            if proc is None:
                continue
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except ProcessLookupError:
                pass
        for proc in (proc_backend, proc_jacobs):
            if proc is None:
                continue
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass
        log_jacobs.close()
        log_backend.close()
        print("[orquestador] procesos detenidos")

        # LIMPIEZA SIEMPRE, incluso si la carga reventó a mitad de camino:
        # sin esto, un error deja 600 pipelines y ~2,86M filas de relleno
        # colgando en jax_memory_test.
        print("[orquestador] limpiando la siembra (historial_limpiar.py)...")
        subprocess.run([sys.executable, str(LOADTEST_DIR / "historial_limpiar.py"), str(seed_json)], check=True)


if __name__ == "__main__":
    asyncio.run(main_async())
