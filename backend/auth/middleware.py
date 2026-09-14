"""Autenticación por request (2026-09-12, admin usuarios etapa 2).

Antes solo se decodificaba el JWT: el rol salía del token y nada miraba la
base, así que desactivar, borrar o degradar a alguien no cortaba su sesión
hasta que el token vencía (spec §1, hallazgo 1). Ahora cada request lee
status, role y token_version POR CLAVE PRIMARIA (EXPLAIN: const/PRIMARY,
fijado en tests/test_sesiones_token_version.py).

Sin caché a propósito (LAS CUATRO §2: sin medición no hay caché). Si algún día
hiciera falta uno, su invalidación es la propia token_version.

`tenant_id` sigue saliendo del token (el spec pide rol, estado y versión).
Un token sin `tv` (emitido antes de esta etapa) vale como tv=0.
"""
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from db.connection import get_pool

from .jwt import decode_token
from .models import AuthUser

bearer = HTTPBearer(auto_error=True)

SESION_INVALIDA = "sesion_invalida"
SQL_ESTADO_DE_SESION = "SELECT status, role, token_version, email FROM jax_users WHERE user_id = %s"


def _rechazo() -> HTTPException:
    # El mismo 401 para todos los casos (no existe, inactivo, versión vieja,
    # tipo equivocado): la respuesta no dice cuál.
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=SESION_INVALIDA)


async def verificar_sesion(payload: dict, tipo: str) -> AuthUser:
    if payload.get("type") != tipo:
        raise _rechazo()
    try:
        user_id = int(payload["user_id"])
        tv_token = int(payload.get("tv", 0))
    except (KeyError, TypeError, ValueError):
        raise _rechazo() from None
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(SQL_ESTADO_DE_SESION, (user_id,))
            fila = await cur.fetchone()
    if fila is None:
        raise _rechazo()
    estado, rol, tv_base, email = fila
    if estado != "active" or tv_token != int(tv_base):
        raise _rechazo()
    return AuthUser(
        user_id=str(user_id),
        tenant_id=str(payload.get("tenant_id", "")),
        role=rol,
        email=email,
        token_version=int(tv_base),
    )


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
) -> AuthUser:
    return await verificar_sesion(decode_token(credentials.credentials), "access")


def require_superadmin(user: AuthUser = Depends(get_current_user)) -> AuthUser:
    if user.role != "superadmin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Solo superadmin")
    return user
