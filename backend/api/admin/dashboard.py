import os
import httpx
from datetime import datetime, date
from fastapi import APIRouter, Depends
from auth.middleware import require_superadmin
from auth.models import AuthUser
from db.connection import get_pool

router = APIRouter(prefix="/api/admin")


async def _check_http(url: str) -> dict:
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            t0 = datetime.utcnow()
            r = await client.get(url)
            ms = int((datetime.utcnow() - t0).total_seconds() * 1000)
            return {"status": "alive" if r.status_code < 500 else "down", "latency_ms": ms}
    except Exception:
        return {"status": "down", "latency_ms": None}


async def _check_db() -> dict:
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT 1")
        return {"status": "connected"}
    except Exception:
        return {"status": "error"}


@router.get("/dashboard")
async def get_dashboard(user: AuthUser = Depends(require_superadmin)):
    las_manos = await _check_http("http://127.0.0.1:7777/health")
    jax_engine = await _check_http("http://127.0.0.1:8080/health")
    db_status = await _check_db()

    services = [
        {"name": "LAS MANOS", "port": 7777, **las_manos},
        {"name": "JAX Engine", "port": 8080, **jax_engine},
        {"name": "MariaDB", "port": 3306, **db_status},
    ]

    pool = await get_pool()
    today = date.today().isoformat()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT COUNT(*) FROM axioma_usage WHERE DATE(created_at) = %s",
                (today,),
            )
            (messages_today,) = await cur.fetchone()

            await cur.execute(
                "SELECT COUNT(*) FROM axioma_usage WHERE request_type='imagen' AND DATE(created_at) = %s",
                (today,),
            )
            (images_today,) = await cur.fetchone()

    stats = {
        "messages_today": messages_today,
        "images_generated": images_today,
        "pipelines_completed": 0,
    }

    return {"services": services, "stats": stats, "recent_events": []}
