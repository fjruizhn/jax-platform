from fastapi import APIRouter, Depends, HTTPException, status as http_status
from auth.middleware import get_current_user
from auth.models import AuthUser
from jax_engine.state import engine_state

router = APIRouter(prefix="/api/facets")


@router.get("")
async def list_facets(user: AuthUser = Depends(get_current_user)):
    state = engine_state.get_state()
    return {"facets": {k: v.model_dump() for k, v in state.facets.items()}}


@router.post("/{facet}/status")
async def set_facet_status(
    facet: str,
    body: dict,
    user: AuthUser = Depends(get_current_user),
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
