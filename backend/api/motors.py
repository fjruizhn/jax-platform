"""R4 -- expone capability/allowed_motors (tablas motor/capability/
capability_motor, ver db/migrations.py) para que el frontend arme el
picker de motor en Pipeline. Solo lectura -- el alta de motores nuevos es
Task 9 (admin)."""
from fastapi import APIRouter, Depends
from auth.middleware import get_current_user
from auth.models import AuthUser
from db.connection import get_pool

router = APIRouter(prefix="/api/motors")


@router.get("/capabilities")
async def list_capabilities(user: AuthUser = Depends(get_current_user)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT `key` FROM capability ORDER BY `key`")
            keys = [r[0] for r in await cur.fetchall()]
            await cur.execute(
                "SELECT capability_key, motor_key FROM capability_motor ORDER BY capability_key, priority ASC"
            )
            by_cap: dict[str, list[str]] = {}
            for capability_key, motor_key in await cur.fetchall():
                by_cap.setdefault(capability_key, []).append(motor_key)
    return {"capabilities": [{"key": k, "allowed_motors": by_cap.get(k, [])} for k in keys]}
