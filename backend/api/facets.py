from fastapi import APIRouter, Depends, HTTPException, status as http_status
from auth.middleware import get_current_user, require_superadmin
from auth.models import AuthUser
from jax_engine.state import engine_state
from db.connection import get_pool

router = APIRouter(prefix="/api/facets")


@router.get("")
async def list_facets(user: AuthUser = Depends(get_current_user)):
    state = engine_state.get_state()
    facets = {k: v.model_dump() for k, v in state.facets.items()}
    # Bloque C: display_name/icon/color_hex desde la tabla `facet` — fuente
    # unica que reemplaza los arrays hardcodeados de frontend (LeftPanel,
    # BottomBar, PipelineModal). "jacobs" no es una faceta (no esta en la
    # tabla), se queda con lo que ya traia el estado runtime.
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT `key`, display_name, icon, color_hex FROM facet")
            rows = await cur.fetchall()
    for key, display_name, icon, color_hex in rows:
        if key in facets:
            facets[key]["display_name"] = display_name
            facets[key]["icon"] = icon
            facets[key]["color"] = color_hex or facets[key].get("color")
    return {"facets": facets}


# T6-5b (2026-09-15): cambia el estado GLOBAL de una faceta y se difunde por
# el bus a todos. Solo exigía sesión: cualquier viewer podía poner a thot en
# "offline" para todos. No tiene llamador en el frontend ni en el repo jax
# (verificado con grep): queda como herramienta de administración.
@router.post("/{facet}/status")
async def set_facet_status(
    facet: str,
    body: dict,
    user: AuthUser = Depends(require_superadmin),
):
    new_status = body.get("status")
    message = body.get("message", "")
    if new_status not in ("idle", "thinking", "error", "offline"):
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="status inválido",
        )
    await engine_state.set_facet_status(
        facet, new_status, user.tenant_id, user.user_id, message
    )
    return {"ok": True, "facet": facet, "status": new_status}
