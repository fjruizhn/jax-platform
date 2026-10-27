from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from api.virtual_office.projects import _active_profile_in_cursor
from auth.middleware import get_current_user
from auth.models import AuthUser
from db.transaccion import AISLAMIENTO_ADMIN, transaccion
from virtual_office.verticales import VERTICALES

router = APIRouter(prefix="/api/virtual-office/verticales")


@router.get("")
async def list_verticales(user: AuthUser = Depends(get_current_user)):
    """Announce the server-owned vertical registry (ids, states, order).

    The browser never decides which rooms exist or which are open; it
    renders this registry.  Project visibility itself is NOT computed
    here: it remains exclusively in the canonical projects resolver.
    """
    try:
        actor_user_id = int(user.user_id)
    except (TypeError, ValueError) as error:
        raise HTTPException(status_code=403, detail="office_access_denied") from error
    async with transaccion(AISLAMIENTO_ADMIN) as cur:
        profile = await _active_profile_in_cursor(cur, actor_user_id)
        if profile is None:
            raise HTTPException(status_code=403, detail="office_access_denied")
    return {"verticales": [dict(vertical) for vertical in VERTICALES]}
