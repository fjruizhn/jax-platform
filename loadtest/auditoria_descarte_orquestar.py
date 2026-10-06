"""Carga HTTP local del feed global, aislada en jax_memory_test.

Ejecutar con JAX_REPO_PATH, JAX_LOADTEST_DB_PASSWORD y las demás variables
JAX_LOADTEST_DB_* apuntando al contenedor temporal de 127.0.0.1:33316.
El arnés solo usa jax_memory_test, el puerto API local 18081 y superadmin 1.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import secrets
import socket
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pymysql
from jose import jwt

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
DB = dict(
    host=os.environ.get("JAX_LOADTEST_DB_HOST", "127.0.0.1"),
    port=int(os.environ.get("JAX_LOADTEST_DB_PORT", "33316")),
    user=os.environ.get("JAX_LOADTEST_DB_USER", "jax_test"),
    password=os.environ.get("JAX_LOADTEST_DB_PASSWORD", ""),
    database=os.environ.get("JAX_LOADTEST_DB_NAME", "jax_memory_test"),
    charset="utf8mb4",
    autocommit=True,
)
API_PORT = int(os.environ.get("JAX_LOADTEST_API_PORT", "18081"))
URL = f"http://127.0.0.1:{API_PORT}/api/admin/auditoria-descarte"
NIVELES = (1, 5, 10, 25, 50, 100)
PIPELINES_CARGA = 100
EVENTOS_POR_PIPELINE = 2000


def _guardas():
    if (DB["database"] != "jax_memory_test" or DB["port"] != 33316
            or DB["host"] != "127.0.0.1" or not DB["password"]):
        raise SystemExit("GUARD: destino no es el MariaDB temporal loopback de 127.0.0.1:33316")
    if API_PORT != 18081:
        raise SystemExit("GUARD: API de carga debe usar el puerto loopback temporal 18081")
    with socket.create_connection((DB["host"], DB["port"]), timeout=2):
        pass


def _sembrar():
    conn = pymysql.connect(**{**DB, "autocommit": False})
    pipeline_ids = []
    try:
        with conn.cursor() as cur:
            ahora = time.time()
            for _ in range(PIPELINES_CARGA):
                pid = str(uuid.uuid4())
                pipeline_ids.append(pid)
                cur.execute(
                    "INSERT INTO jacobs_pipelines "
                    "(pipeline_id,name,invoked_by,mode,status,created_at,updated_at,user_id,tenant_id,owner_ack_at) "
                    "VALUES (%s,'carga auditoría','plataforma','supervised','discarded',%s,%s,'1','1',%s)",
                    (pid, ahora, ahora, ahora),
                )
                for inicio in range(0, EVENTOS_POR_PIPELINE, 1000):
                    cur.executemany(
                        "INSERT INTO jacobs_events (pipeline_id,step_id,event_type,payload,ts) "
                        "VALUES (%s,NULL,%s,%s,%s)",
                        [(pid,
                          ("PIPELINE_DISCARDED", "PIPELINE_RECOVERED", "PIPELINE_HIDDEN", "PIPELINE_RESTORED")[j % 4],
                          '{"user_id":"load-test","desde":"aborted","a":"discarded"}', ahora - j)
                         for j in range(inicio, min(inicio + 1000, EVENTOS_POR_PIPELINE))],
                    )
        conn.commit()
        return pipeline_ids
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _limpiar(pipeline_ids):
    conn = pymysql.connect(**DB)
    try:
        with conn.cursor() as cur:
            if pipeline_ids:
                marcas = ",".join(["%s"] * len(pipeline_ids))
                cur.execute(f"DELETE FROM jacobs_events WHERE pipeline_id IN ({marcas})", pipeline_ids)
                cur.execute(f"DELETE FROM jacobs_pipelines WHERE pipeline_id IN ({marcas})", pipeline_ids)
    finally:
        conn.close()


def _entorno(tmp: Path, jax_repo_path: Path):
    env = dict(os.environ)
    env.update({
        "JAX_DB_HOST": DB["host"], "JAX_DB_PORT": str(DB["port"]), "JAX_DB_USER": DB["user"],
        "JAX_DB_PASSWORD": DB["password"], "JAX_DB_NAME": DB["database"],
        "JAX_REPO_PATH": str(jax_repo_path),
        "JAX_CONFIG_PATH": str(jax_repo_path / "config" / "config.toml"),
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


async def _medir(token: str, concurrencia: int, n: int, params: dict):
    sem = asyncio.Semaphore(concurrencia)
    latencias, errores = [], []
    async with httpx.AsyncClient(timeout=20, limits=httpx.Limits(max_connections=concurrencia + 2)) as client:
        async def una():
            async with sem:
                inicio = time.perf_counter()
                try:
                    response = await client.get(URL, params=params,
                                                headers={"Authorization": f"Bearer {token}"})
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
    parser = argparse.ArgumentParser(description="Mide el feed global con 200.000 eventos en DB desechable.")
    parser.add_argument("--jax-repo", type=Path, default=os.environ.get("JAX_REPO_PATH"),
                        help="checkout compatible de Jax (o variable JAX_REPO_PATH)")
    args = parser.parse_args()
    jax_repo_path = Path(args.jax_repo) if args.jax_repo is not None else None
    if jax_repo_path is None or not (jax_repo_path / "config" / "config.toml").is_file():
        raise SystemExit("Indica --jax-repo o JAX_REPO_PATH a un checkout compatible de Jax")
    jax_sha = subprocess.check_output(
        ["git", "-C", str(jax_repo_path), "rev-parse", "HEAD"], text=True,
    ).strip()
    _guardas()
    tmp = Path("/tmp") / f"jxp-auditoria-{uuid.uuid4().hex}"
    tmp.mkdir(mode=0o700)
    pipeline_ids = []
    proceso = None
    try:
        print(f"sembrando {PIPELINES_CARGA} pipelines × {EVENTOS_POR_PIPELINE:,} eventos = "
              f"{PIPELINES_CARGA * EVENTOS_POR_PIPELINE:,}; Jax SHA={jax_sha}", flush=True)
        pipeline_ids = _sembrar()
        print("siembra completa; iniciando backend", flush=True)
        env = _entorno(tmp, jax_repo_path.resolve())
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

        # Token con llave aleatoria exclusiva de este proceso y superadmin semilla.
        token = jwt.encode({"user_id": "1", "tenant_id": "1", "role": "superadmin", "tv": 0,
                            "exp": int(time.time()) + 3600, "type": "access"},
                           env["JAX_JWT_SECRET"], algorithm="HS256")
        hasta = datetime.now().astimezone().date()
        desde = (hasta - timedelta(days=366)).isoformat()
        hasta = hasta.isoformat()
        params = {"desde": desde, "hasta": hasta}  # ventana máxima, sin filtro de evento
        print(f"tabla=jacobs_events; filas={PIPELINES_CARGA * EVENTOS_POR_PIPELINE}; "
              f"tenants=1; desde={desde}; hasta={hasta}; filtro_evento=ninguno; pagina=50")
        for c in NIVELES:
            print(await _medir(token, c, min(2000, max(100, c * 20)), params))
    finally:
        if proceso:
            proceso.terminate()
            try:
                proceso.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proceso.kill()
        if pipeline_ids:
            _limpiar(pipeline_ids)


if __name__ == "__main__":
    asyncio.run(main())
