"""Modo Ejecutor (SP2, 2026-09-17). Sólo superadmin. Contrato: ejecutor/misiones.py.

Errores con código (`detail` = código o `{codigo, ...datos}`); el frontend los traduce."""
from fastapi import APIRouter, Body, Depends, HTTPException, Query

from auth.middleware import require_superadmin
from auth.models import AuthUser
from ejecutor import misiones

router = APIRouter(prefix="/api/ejecutor")


def _http(exc: misiones.ErrorDelEjecutor) -> HTTPException:
    return HTTPException(status_code=exc.estado, detail=exc.detalle)


@router.get("/estado")
async def ver_estado(user: AuthUser = Depends(require_superadmin)):
    try:
        return await misiones.estado()
    except misiones.ErrorDelEjecutor as exc:
        raise _http(exc) from exc


@router.post("/pausa/poner")
async def poner_pausa(user: AuthUser = Depends(require_superadmin)):
    return await _cambiar_pausa(misiones.poner_pausa, user)


@router.post("/pausa/quitar")
async def quitar_pausa(user: AuthUser = Depends(require_superadmin)):
    return await _cambiar_pausa(misiones.quitar_pausa, user)


async def _cambiar_pausa(cambio, user: AuthUser):
    try:
        return await cambio(user.user_id)
    except misiones.ErrorDelEjecutor as exc:
        raise _http(exc) from exc
    except misiones.PausaNoEscribible as exc:
        raise HTTPException(status_code=503, detail="ejecutor_pausa_no_escribible") from exc
    except misiones.AuditoriaDePausaFallida as exc:
        raise HTTPException(status_code=500, detail="ejecutor_pausa_auditoria_fallida") from exc


@router.get("/misiones")
async def listar_misiones(limite: int = Query(20, ge=1, le=misiones.LIMITE_DE_LISTA),
                          user: AuthUser = Depends(require_superadmin)):
    return {"misiones": await misiones.listar(limite)}


@router.post("/misiones", status_code=202)
async def crear_mision(cuerpo: dict = Body(default_factory=dict), user: AuthUser = Depends(require_superadmin)):
    try:
        return await misiones.crear(user.user_id, cuerpo.get("objetivo"), cuerpo.get("maquinas"))
    except misiones.ErrorDelEjecutor as exc:
        raise _http(exc) from exc


@router.get("/misiones/{mision_id}")
async def ver_mision(mision_id: str, user: AuthUser = Depends(require_superadmin)):
    try:
        return await misiones.detalle(mision_id)
    except misiones.ErrorDelEjecutor as exc:
        raise _http(exc) from exc


@router.get("/misiones/{mision_id}/bitacora")
async def ver_bitacora(mision_id: str, desde: int = Query(0, ge=0), user: AuthUser = Depends(require_superadmin)):
    try:
        return {"eventos": await misiones.bitacora(mision_id, desde)}
    except misiones.ErrorDelEjecutor as exc:
        raise _http(exc) from exc


@router.post("/misiones/{mision_id}/turnos", status_code=202)
async def continuar_mision(mision_id: str, cuerpo: dict = Body(default_factory=dict),
                           user: AuthUser = Depends(require_superadmin)):
    try:
        return await misiones.continuar(user.user_id, mision_id, cuerpo.get("instruccion"))
    except misiones.ErrorDelEjecutor as exc:
        raise _http(exc) from exc
