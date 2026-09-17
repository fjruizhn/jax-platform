"""Endpoints del kill switch (2026-09-16, frente B). Sólo superadmin. El
estado para cualquier usuario va en /api/state.kill_switch_active."""
from fastapi import APIRouter, Depends, HTTPException

import kill_switch
from auth.middleware import require_superadmin
from auth.models import AuthUser

router = APIRouter(prefix="/api/admin")


@router.get("/kill-switch")
async def ver_kill_switch(user: AuthUser = Depends(require_superadmin)):
    return await kill_switch.estado()


@router.post("/kill-switch/activar")
async def activar_kill_switch(user: AuthUser = Depends(require_superadmin)):
    try:
        return await kill_switch.activar(user)
    except kill_switch.InterruptorNoEscribible as exc:
        raise HTTPException(status_code=503, detail=kill_switch.NO_ESCRIBIBLE) from exc
    except kill_switch.AuditoriaDelInterruptorFallida as exc:
        raise HTTPException(status_code=500, detail=kill_switch.AUDITORIA_FALLIDA) from exc


@router.post("/kill-switch/reanudar")
async def reanudar_kill_switch(user: AuthUser = Depends(require_superadmin)):
    try:
        return await kill_switch.reanudar(user)
    except kill_switch.InterruptorNoEscribible as exc:
        raise HTTPException(status_code=503, detail=kill_switch.NO_ESCRIBIBLE) from exc
    except kill_switch.AuditoriaDelInterruptorFallida as exc:
        raise HTTPException(status_code=500, detail=kill_switch.AUDITORIA_FALLIDA) from exc
