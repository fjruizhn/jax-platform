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
from datetime import datetime, timedelta
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
TENANTS_CARGA = 100
EVENTOS_POR_TENANT = 2000


def _guardas():
    if DB["database"] != "jax_memory_test" or DB["port"] in {3308, 3306}:
        raise SystemExit("GUARD: destino de DB no es el MariaDB temporal de pruebas")
    if DB["host"] != "127.0.0.1" or API_PORT in {8080, 7777}:
        raise SystemExit("GUARD: host/puerto podría alcanzar un servicio de producción")
    with socket.create_connection((DB["host"], DB["port"]), timeout=2):
        pass


def _sembrar():
    conn = pymysql.connect(**{**DB, "autocommit": False})
    pipeline_ids = []
    user_ids = []
    tenant_ids = []
    try:
        with conn.cursor() as cur:
            ahora = time.time()
            # El tenant consultado conserva una muestra pequeña; 100 tenants
            # ajenos concentran 200.000 eventos para medir el aislamiento real.
            tenants = [(f"auditoria-carga-{uuid.uuid4().hex}",) for _ in range(TENANTS_CARGA)]
            cur.executemany("INSERT INTO jax_tenants (name) VALUES (%s)", tenants)
            tenant_inicio = int(cur.lastrowid)
            tenant_ids = list(range(tenant_inicio, tenant_inicio + TENANTS_CARGA))
            for tenant_id in tenant_ids:
                email = f"auditoria-{uuid.uuid4().hex}@example.invalid"
                cur.execute(
                    "INSERT INTO jax_users (tenant_id,email,password_hash,role,status) "
                    "VALUES (%s,%s,'loadtest-only','admin','active')",
                    (tenant_id, email),
                )
                user_ids.append(int(cur.lastrowid))

            cur.execute("UPDATE jax_users SET role='admin' WHERE user_id=1 AND tenant_id=1")
            for i, (tenant_id, user_id) in enumerate([(1, 1), *zip(tenant_ids, user_ids)]):
                pid = str(uuid.uuid4())
                pipeline_ids.append(pid)
                cur.execute(
                    "INSERT INTO jacobs_pipelines "
                    "(pipeline_id,name,invoked_by,mode,status,created_at,updated_at,user_id,tenant_id,owner_ack_at) "
                    "VALUES (%s,'carga auditoría','plataforma','supervised','discarded',%s,%s,%s,%s,%s)",
                    (pid, ahora, ahora, str(user_id), str(tenant_id), ahora),
                )
                cantidad = 50 if i == 0 else EVENTOS_POR_TENANT
                for inicio in range(0, cantidad, 1000):
                    cur.executemany(
                        "INSERT INTO jacobs_events (pipeline_id,step_id,event_type,payload,ts) "
                        "VALUES (%s,NULL,%s,%s,%s)",
                        [(pid,
                          ("PIPELINE_DISCARDED", "PIPELINE_RECOVERED", "PIPELINE_HIDDEN", "PIPELINE_RESTORED")[j % 4],
                          '{"user_id":"load-test","desde":"aborted","a":"discarded"}', ahora - j)
                         for j in range(inicio, min(inicio + 1000, cantidad))],
                    )
        conn.commit()
        return pipeline_ids, user_ids, tenant_ids
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _limpiar(pipeline_ids, user_ids, tenant_ids):
    conn = pymysql.connect(**DB)
    try:
        with conn.cursor() as cur:
            if pipeline_ids:
                marcas = ",".join(["%s"] * len(pipeline_ids))
                cur.execute(f"DELETE FROM jacobs_events WHERE pipeline_id IN ({marcas})", pipeline_ids)
                cur.execute(f"DELETE FROM jacobs_pipelines WHERE pipeline_id IN ({marcas})", pipeline_ids)
            if user_ids:
                marcas = ",".join(["%s"] * len(user_ids))
                cur.execute(f"DELETE FROM jax_users WHERE user_id IN ({marcas})", user_ids)
            if tenant_ids:
                marcas = ",".join(["%s"] * len(tenant_ids))
                cur.execute(f"DELETE FROM jax_tenants WHERE tenant_id IN ({marcas})", tenant_ids)
            cur.execute("UPDATE jax_users SET role='superadmin' WHERE user_id=1 AND tenant_id=1")
    finally:
        conn.close()


def _entorno(tmp: Path):
    env = dict(os.environ)
    env.update({
        "JAX_DB_HOST": DB["host"], "JAX_DB_PORT": str(DB["port"]), "JAX_DB_USER": DB["user"],
        "JAX_DB_PASSWORD": DB["password"], "JAX_DB_NAME": DB["database"],
        "JAX_REPO_PATH": "/home/fruiz/wt/jax-auditoria-descarte-r2",
        "JAX_CONFIG_PATH": "/home/fruiz/wt/jax-auditoria-descarte-r2/config/config.toml",
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
    _guardas()
    tmp = Path("/tmp") / f"jxp-auditoria-{uuid.uuid4().hex}"
    tmp.mkdir(mode=0o700)
    pipeline_ids = user_ids = tenant_ids = []
    proceso = None
    try:
        print(f"sembrando {TENANTS_CARGA} tenants ajenos × {EVENTOS_POR_TENANT:,} eventos y 50 del tenant consultado", flush=True)
        pipeline_ids, user_ids, tenant_ids = _sembrar()
        print("siembra completa; iniciando backend", flush=True)
        env = _entorno(tmp)
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
        token = jwt.encode({"user_id": "1", "tenant_id": "1", "role": "admin", "tv": 0,
                            "exp": int(time.time()) + 3600, "type": "access"},
                           env["JAX_JWT_SECRET"], algorithm="HS256")
        hasta = datetime.now().astimezone().date()
        desde = (hasta - timedelta(days=366)).isoformat()
        hasta = hasta.isoformat()
        params = {"desde": desde, "hasta": hasta}  # ventana máxima, sin filtro de evento
        print(f"tabla=jacobs_events; filas_ajenas={TENANTS_CARGA * EVENTOS_POR_TENANT}; filas_tenant=50; tenants={TENANTS_CARGA}; desde={desde}; hasta={hasta}; filtro_evento=ninguno")
        for c in NIVELES:
            print(await _medir(token, c, min(2000, max(100, c * 20)), params))
    finally:
        if proceso:
            proceso.terminate()
            try:
                proceso.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proceso.kill()
        if pipeline_ids or user_ids or tenant_ids:
            _limpiar(pipeline_ids, user_ids, tenant_ids)


if __name__ == "__main__":
    _guardas()
    asyncio.run(main())
