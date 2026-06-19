from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import List
from auth.middleware import require_superadmin
from auth.models import AuthUser
from db.connection import get_pool

router = APIRouter(prefix="/api/admin")

DEFAULT_CONFIG = {
    "lang_default": "es",
    "theme_default": "dark",
    "session_timeout_min": "60",
    "max_pipelines": "1",
    "web_task_retention_days": "7",
    "ws_notifications": "true",
    "system_name": "Axioma",
}


async def _ensure_defaults(conn):
    async with conn.cursor() as cur:
        for k, v in DEFAULT_CONFIG.items():
            await cur.execute(
                "INSERT IGNORE INTO axioma_config (config_key, config_value) VALUES (%s, %s)",
                (k, v),
            )


@router.get("/config")
async def get_config(user: AuthUser = Depends(require_superadmin)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _ensure_defaults(conn)
        async with conn.cursor() as cur:
            await cur.execute("SELECT config_key, config_value FROM axioma_config ORDER BY config_key")
            rows = await cur.fetchall()
    return {"config": [{"key": r[0], "value": r[1]} for r in rows]}


class ConfigItem(BaseModel):
    key: str
    value: str


@router.put("/config")
async def update_config(items: List[ConfigItem], user: AuthUser = Depends(require_superadmin)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            for item in items:
                await cur.execute(
                    "INSERT INTO axioma_config (config_key, config_value) VALUES (%s, %s) "
                    "ON DUPLICATE KEY UPDATE config_value = %s",
                    (item.key, item.value, item.value),
                )
    return {"ok": True}
