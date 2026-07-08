import os
import httpx
import psutil
from datetime import datetime, date
from fastapi import APIRouter, Depends
from auth.middleware import require_superadmin
from auth.models import AuthUser
from db.connection import get_pool

router = APIRouter(prefix="/api/admin")

_PROVIDERS_KEYS = [
    "OPENAI_API_KEY",
    "DEEPSEEK_API_KEY",
    "GEMINI_API_KEY",
    "KIMI_API_KEY",
    "ZAI_API_KEY",
]


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


def _count_configured_keys() -> dict:
    env_path = "/etc/jax/.env"
    env = {}
    try:
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    env[k.strip()] = v.strip()
    except Exception:
        pass

    configured = sum(1 for k in _PROVIDERS_KEYS if env.get(k))
    return {"configured": configured, "total": len(_PROVIDERS_KEYS)}


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
    now = datetime.utcnow()

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

            await cur.execute(
                "SELECT COUNT(*) FROM jax_users WHERE status = 'active'"
            )
            (users_active,) = await cur.fetchone()

            await cur.execute(
                "SELECT COUNT(*) FROM jax_users WHERE locked_until > %s",
                (now,),
            )
            (users_locked,) = await cur.fetchone()

    api_keys = _count_configured_keys()

    mem = psutil.virtual_memory()
    ram = {
        "total_mb": round(mem.total / 1024 / 1024),
        "used_mb": round(mem.used / 1024 / 1024),
        "percent": mem.percent,
    }

    stats = {
        "messages_today": messages_today,
        "images_generated": images_today,
        "pipelines_completed": 0,
        "users_active": users_active,
        "users_locked": users_locked,
        "api_keys_configured": api_keys["configured"],
        "api_keys_total": api_keys["total"],
        "ram": ram,
    }

    return {"services": services, "stats": stats, "recent_events": []}
