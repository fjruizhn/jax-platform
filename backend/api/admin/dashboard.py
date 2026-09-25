import os
from datetime import date, datetime, time, timedelta
from urllib.parse import urlsplit

import psutil
from fastapi import APIRouter, Depends

from auth.middleware import require_superadmin
from auth.models import AuthUser
from db.connection import get_pool
from http_client import get_http_client
from jax_engine.state import LAS_MANOS_URL
from tiempo import utc_ahora

router = APIRouter(prefix="/api/admin")

# A-37 (2026-09-16): una consulta, rango sargable sobre idx_axioma_usage_periodo.
# SUM sin filas es NULL: COALESCE. EXPLAIN no distingue DATE(col) de un rango
# en MariaDB >= 11.1; tests/test_tablero.py fija el texto del WHERE.
SQL_USO_DEL_DIA = (
    "SELECT COUNT(*), COALESCE(SUM(request_type = 'imagen'), 0) FROM axioma_usage "
    "WHERE created_at >= %s AND created_at < %s"
)
# A-35: la verdad de las llaves es `credential` (credential_resolver), no el
# .env. Total = proveedores activos que usan api_key; configurados = los que
# tienen al menos una credencial activa (idx_provider_state).
SQL_LLAVES = (
    "SELECT COUNT(*), COALESCE(SUM(EXISTS (SELECT 1 FROM credential c "
    "WHERE c.provider_id = p.id AND c.state = 'active')), 0) "
    "FROM provider p WHERE p.auth_type = 'api_key' AND p.status = 'active'"
)
# A-49: total de completados (spec: status='completed', sin ventana). Índice
# con prefijo status (hoy idx_pipelines_status / idx_pipelines_ocultos),
# creados por jax/jacobs/store.py::init_tables().
SQL_PIPELINES_COMPLETADOS = "SELECT COUNT(*) FROM jacobs_pipelines WHERE status = 'completed'"
# Task 15 R12(c) (2026-09-16): la carga G midio `ALL` sobre jax_users.
# Rango sobre idx_jax_users_locked_until (db/migrations.py::
# _indice_de_cuentas_bloqueadas, DDL acotado; no esta en _INDEXES).
SQL_CUENTAS_BLOQUEADAS = "SELECT COUNT(*) FROM jax_users WHERE locked_until > %s"
# Restricción dura (2026-09-20, pedido de Fernando): mismo filtro EXACTO que
# la pantalla de Memoria (api/admin/memoria.py::SQL_CONTAR, con
# verificado=False, incluir_superados=False, incluir_vencidos=False) -- un
# `is_verified = 0` a secas cuenta también lo fundido (superseded_by no
# nulo: superado a propósito, no es pendiente) y lo vencido. Medido en
# producción: el filtro flojo da 37, el pendiente real es 0.
# tests/test_tablero.py ata el filtro con un test que siembra los tres casos.
# Índice idx_facts_revision (is_verified, expires_at, created_at) -- mismo
# que usa SQL_CONTAR (jax_memory_schema.sql / jax/memory/migrations.py).
SQL_HECHOS_SIN_VERIFICAR = (
    "SELECT COUNT(*) FROM facts WHERE is_verified = 0 AND superseded_by IS NULL "
    "AND (expires_at IS NULL OR expires_at > NOW())"
)


def _rango_del_dia(dia: date) -> tuple[datetime, datetime]:
    """Los dos límites salen de la MISMA fecha: [00:00 del día, 00:00 del siguiente)."""
    inicio = datetime.combine(dia, time.min)
    return inicio, inicio + timedelta(days=1)


async def _check_http(url: str) -> dict:
    try:
        client = await get_http_client()
        t0 = utc_ahora()
        r = await client.get(url, timeout=3.0)
        ms = int((utc_ahora() - t0).total_seconds() * 1000)
        # A-36: solo un 200 es vivo (igual que el poller de jax_engine/state.py).
        return {"status": "alive" if r.status_code == 200 else "down", "latency_ms": ms}
    except Exception:  # fail-soft: cualquier error del ping ES 'down' en el tablero
        return {"status": "down", "latency_ms": None}


async def _servicio(nombre: str, base_url: str | None, ruta: str) -> dict:
    """Sin base configurada no se inventa un estado: `sin_configurar`. Una base
    mal formada (puerto no numerico o fuera de rango, IPv6 sin cerrar) tampoco
    es una base: antes el ValueError de urlsplit tiraba el tablero entero (500)."""
    sin_configurar = {"name": nombre, "port": None, "status": "sin_configurar", "latency_ms": None}
    if not base_url:
        return sin_configurar
    try:
        puerto = urlsplit(base_url).port
    except ValueError:
        return sin_configurar
    return {"name": nombre, "port": puerto, **await _check_http(f"{base_url}{ruta}")}


async def _check_db() -> dict:
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT 1")
        return {"status": "connected"}
    except Exception:  # fail-soft: cualquier error ES status 'error' en el tablero
        return {"status": "error"}


@router.get("/dashboard")
async def get_dashboard(user: AuthUser = Depends(require_superadmin)):
    services = [
        await _servicio("LAS MANOS", LAS_MANOS_URL, "/health"),
        await _servicio("JAX Engine", os.environ.get("JAX_PLATFORM_URL"), "/api/health"),
        # Sin default: este panel MUESTRA el puerto, no conecta. Un "3306"
        # inventado no rompe nada -- miente, y en un tablero de estado eso es
        # peor: la instancia :3306 esta muerta y la real es :3308.
        {"name": "MariaDB", "port": os.environ.get("JAX_DB_PORT"), **await _check_db()},
    ]

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(SQL_USO_DEL_DIA, _rango_del_dia(date.today()))
            messages_today, images_today = await cur.fetchone()
            await cur.execute("SELECT COUNT(*) FROM jax_users WHERE status = 'active'")
            (users_active,) = await cur.fetchone()
            await cur.execute(SQL_CUENTAS_BLOQUEADAS, (utc_ahora(),))
            (users_locked,) = await cur.fetchone()
            await cur.execute(SQL_LLAVES)
            keys_total, keys_configured = await cur.fetchone()
            await cur.execute(SQL_PIPELINES_COMPLETADOS)
            (pipelines_completed,) = await cur.fetchone()
            await cur.execute(SQL_HECHOS_SIN_VERIFICAR)
            (facts_unverified,) = await cur.fetchone()

    mem = psutil.virtual_memory()
    return {
        "services": services,
        "stats": {
            "messages_today": messages_today,
            "images_generated": int(images_today),
            "facts_unverified": facts_unverified,
            "pipelines_completed": pipelines_completed,
            "users_active": users_active,
            "users_locked": users_locked,
            "api_keys_configured": int(keys_configured),
            "api_keys_total": keys_total,
            "ram": {
                "total_mb": round(mem.total / 1024 / 1024),
                "used_mb": round(mem.used / 1024 / 1024),
                "percent": mem.percent,
            },
        },
    }
