"""Carga HTTP local del feed de auditoría, aislada en jax_memory_test.

Ejecutar: python3 loadtest/auditoria_descarte_orquestar.py
Usa exclusivamente 127.0.0.1:33316 (contenedor temporal), DB jax_memory_test,
API 127.0.0.1:18081 y la identidad semilla 1 del esquema de pruebas.
"""
from __future__ import annotations

import asyncio
import os
import secrets
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx
import pymysql
from jose import jwt

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
DB = dict(host="127.0.0.1", port=33316, user="jax_test", password="codex-test-db-only",
          database="jax_memory_test", charset="utf8mb4", autocommit=True)
API_PORT = 18081
URL = f"http://127.0.0.1:{API_PORT}/api/admin/auditoria-descarte"
NIVELES = (1, 5, 10, 25, 50, 100)


def _guardas():
    if DB["database"] != "jax_memory_test" or DB["port"] in {3308, 3306}:
        raise SystemExit("GUARD: destino de DB no es el MariaDB temporal de pruebas")
    if DB["host"] != "127.0.0.1" or API_PORT in {8080, 7777}:
        raise SystemExit("GUARD: host/puerto podría alcanzar un servicio de producción")
    with socket.create_connection((DB["host"], DB["port"]), timeout=2):
        pass


def _sembrar(pipeline_id: str, cantidad: int):
    conn = pymysql.connect(**DB)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO jacobs_pipelines "
                "(pipeline_id,name,invoked_by,mode,status,created_at,updated_at,user_id,tenant_id,owner_ack_at) "
                "VALUES (%s,'carga auditoría','plataforma','supervised','discarded',%s,%s,'1','1',%s)",
                (pipeline_id, time.time(), time.time(), time.time()),
            )
            ahora = time.time()
            for inicio in range(0, cantidad, 1000):
                cur.executemany(
                    "INSERT INTO jacobs_events (pipeline_id,step_id,event_type,payload,ts) "
                    "VALUES (%s,NULL,%s,%s,%s)",
                    [(pipeline_id,
                      ("PIPELINE_DISCARDED", "PIPELINE_RECOVERED", "PIPELINE_HIDDEN", "PIPELINE_RESTORED")[i % 4],
                      '{"user_id":"load-test","motivo":"sembrado"}', ahora - i)
                     for i in range(inicio, min(inicio + 1000, cantidad))],
                )
    finally:
        conn.close()


def _limpiar(pipeline_id: str):
    conn = pymysql.connect(**DB)
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM jacobs_events WHERE pipeline_id=%s", (pipeline_id,))
            cur.execute("DELETE FROM jacobs_pipelines WHERE pipeline_id=%s", (pipeline_id,))
    finally:
        conn.close()


def _entorno(tmp: Path):
    env = dict(os.environ)
    env.update({
        "JAX_DB_HOST": DB["host"], "JAX_DB_PORT": str(DB["port"]), "JAX_DB_USER": DB["user"],
        "JAX_DB_PASSWORD": DB["password"], "JAX_DB_NAME": DB["database"],
        "JAX_REPO_PATH": "/home/fruiz/wt/jax-auditoria-descarte",
        "JAX_CONFIG_PATH": "/home/fruiz/wt/jax-auditoria-descarte/config/config.toml",
        "JAX_JWT_SECRET": secrets.token_urlsafe(48),
        "JAX_FACET_SEAL_PATH": str(tmp / "facet-seal"),
        "JAX_USAGE_SPOOL_DIR": str(tmp / "usage-spool"),
        "JAX_KILL_SWITCH_PATH": str(tmp / "PAUSE"),
        "JAX_AUDIT_LOG_PATH": str(tmp / "audit.jsonl"),
        "JAX_ADJUNTOS_DIR": str(tmp / "adjuntos"),
        "JAX_PROXY_CARRIL_RAIZ": str(tmp / "carril"),
        "JAX_EJECUTOR_PAUSA": str(tmp / "ejecutor-pausa" / "PAUSA"),
        "JAX_EJECUTOR_PYTHON": str(tmp / "no-existe" / "python"),
        "JAX_REPO_BASE": str(tmp / "repo"),
        "LAS_MANOS_URL": "http://127.0.0.1:17779",
        "JACOBS_URL": "http://127.0.0.1:17779/jacobs",
        "JAX_PLATFORM_URL": f"http://127.0.0.1:{API_PORT}",
        "JAX_OLLAMA_URL": "http://ollama.invalid:11434",
        "PYTHONPATH": str(BACKEND),
        "JAX_ADJUNTO_MAX_BYTES": "10485760", "JAX_ADJUNTO_MAX_CHARS": "8000",
        "JAX_ADJUNTO_MAX_PAGINAS": "20", "JAX_ADJUNTO_MAX_POR_MENSAJE": "1",
        "JAX_ADJUNTO_IMAGENES_EN_PROCESO": "1", "JAX_ADJUNTO_SUBIDAS_EN_PROCESO": "1",
        "JAX_ADJUNTO_PDF_PROCESOS": "1", "JAX_ADJUNTO_PDF_TIMEOUT_SEGUNDOS": "5",
        "JAX_ADJUNTOS_TTL_HORAS": "24", "JAX_ADJUNTOS_CUOTA_BYTES_USUARIO": "524288000",
        "JAX_ADJUNTOS_DISCO_LIBRE_MINIMO_BYTES": "1073741824",
        "JAX_ADJUNTOS_SUBIDAS_POR_MINUTO": "600", "JAX_ADJUNTOS_RECHAZO_ESPERA_MS": "0",
    })
    env.pop("JAX_CI_NO_DB", None)
    return env


def _p95(values):
    return sorted(values)[max(0, int(len(values) * .95 + .999999) - 1)]


async def _medir(token: str, concurrencia: int, n: int):
    sem = asyncio.Semaphore(concurrencia)
    latencias, errores = [], []
    async with httpx.AsyncClient(timeout=20, limits=httpx.Limits(max_connections=concurrencia + 2)) as client:
        async def una():
            async with sem:
                inicio = time.perf_counter()
                try:
                    response = await client.get(URL, headers={"Authorization": f"Bearer {token}"})
                    if response.status_code != 200:
                        errores.append(response.status_code)
                    else:
                        latencias.append((time.perf_counter() - inicio) * 1000)
                except Exception as exc:  # fail-soft: cuenta el fallo individual y conserva el resto de la tanda
                    errores.append(type(exc).__name__)
        inicio = time.perf_counter()
        await asyncio.gather(*(una() for _ in range(n)))
        duracion = time.perf_counter() - inicio
    return {"concurrencia": concurrencia, "solicitudes": n, "rps": round(n / duracion, 2),
            "p95_ms": round(_p95(latencias), 2) if latencias else None, "errores": len(errores)}


async def main():
    _guardas()
    tmp = Path("/tmp") / f"jxp-auditoria-{uuid.uuid4().hex}"
    tmp.mkdir(mode=0o700)
    pipeline_id = str(uuid.uuid4())
    print("sembrando 5.000 eventos en jax_memory_test", flush=True)
    _sembrar(pipeline_id, 5000)
    print("siembra completa; iniciando backend", flush=True)
    env = _entorno(tmp)
    proceso = None
    try:
        log = (tmp / "backend.log").open("w+")
        proceso = subprocess.Popen([sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1",
                                    "--port", str(API_PORT), "--log-level", "warning"],
                                   cwd=BACKEND, env=env, stdout=log, stderr=subprocess.STDOUT,
                                   start_new_session=True)
        for _ in range(120):
            try:
                async with httpx.AsyncClient() as client:
                    health = await client.get(f"http://127.0.0.1:{API_PORT}/api/health", timeout=1)
                    if health.status_code < 500:
                        break
            except Exception:  # fail-soft: health aún no responde; se reintenta hasta agotar el límite
                pass
            await asyncio.sleep(.5)
            if proceso.poll() is not None:
                log.flush()
                log.seek(0)
                raise RuntimeError(f"backend terminó ({proceso.returncode}): {log.read()[-2000:]}")
        else:
            log.flush()
            log.seek(0)
            raise RuntimeError(f"backend aislado no inició: {log.read()[-2000:]}")

        # Token con llave aleatoria exclusiva de este proceso y usuario semilla 1 (tenant 1).
        token = jwt.encode({"user_id": "1", "tenant_id": "1", "role": "superadmin", "tv": 0,
                            "exp": int(time.time()) + 3600, "type": "access"},
                           env["JAX_JWT_SECRET"], algorithm="HS256")
        print("tabla=jacobs_events; filas_sembradas=5000; endpoint=GET /api/admin/auditoria-descarte")
        for c in NIVELES:
            print(await _medir(token, c, min(2000, max(100, c * 20))))
    finally:
        if proceso:
            proceso.terminate()
            try:
                proceso.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proceso.kill()
        _limpiar(pipeline_id)


if __name__ == "__main__":
    _guardas()
    asyncio.run(main())
