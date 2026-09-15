"""Autenticación por request (2026-09-12, admin usuarios etapa 2).

Antes solo se decodificaba el JWT: el rol salía del token y nada miraba la
base, así que desactivar, borrar o degradar a alguien no cortaba su sesión
hasta que el token vencía (spec §1, hallazgo 1). Ahora cada request lee
status, role, token_version, email y must_change_password POR CLAVE PRIMARIA
(EXPLAIN: const/PRIMARY, fijado en tests/test_sesiones_token_version.py).

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
CAMBIO_DE_PASSWORD_REQUERIDO = "cambio_de_password_requerido"
# Las ÚNICAS rutas que aceptan una sesión con must_change_password (Ruling
# U34). Además de ellas: /api/auth/refresh (llama a verificar_sesion a mano) y
# /api/auth/logout (no autentica). Todo lo demás se niega por defecto.
# tests/test_fijar_password.py fija que esta lista y las rutas que piden
# get_current_user_con_cambio_pendiente son el mismo conjunto.
RUTAS_CON_CAMBIO_PENDIENTE = frozenset({("GET", "/api/auth/me"), ("POST", "/api/auth/me/password")})
SQL_ESTADO_DE_SESION = "SELECT status, role, token_version, email, must_change_password FROM jax_users WHERE user_id = %s"


def _rechazo() -> HTTPException:
    # El mismo 401 para todos los casos (no existe, inactivo, versión vieja,
    # tipo equivocado): la respuesta no dice cuál.
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=SESION_INVALIDA)


async def verificar_sesion(payload: dict, tipo: str, *, admite_cambio_pendiente: bool = False) -> AuthUser:
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
    estado, rol, tv_base, email, cambio_pendiente = fila
    if estado != "active" or tv_token != int(tv_base):
        raise _rechazo()
    # Después de los 401: una sesión revocada sigue siendo 401 (el frontend la
    # manda al login); una válida con la marca, 403 (la manda al cambio).
    if cambio_pendiente and not admite_cambio_pendiente:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=CAMBIO_DE_PASSWORD_REQUERIDO)
    return AuthUser(
        user_id=str(user_id),
        tenant_id=str(payload.get("tenant_id", "")),
        role=rol,
        email=email,
        token_version=int(tv_base),
        must_change_password=bool(cambio_pendiente),
    )


async def reverificar_sesion(user: AuthUser) -> AuthUser:
    """Vuelve a correr verificar_sesion para una sesión ya verificada
    (2026-09-15, admin usuarios etapa 3, m1 de la revisión final). WS y SSE la
    llaman DESPUÉS de registrar la conexión: si el commit de un admin (sube
    token_version o cambia el estado) y su corte cayeron entre la primera
    verificación y el registro, el corte no encontró la conexión; acá se ve.
    `user.token_version` es la versión del token (la primera verificación exige
    que coincida con la base). Lanza lo mismo que verificar_sesion."""
    return await verificar_sesion(
        {"type": "access", "user_id": user.user_id, "tenant_id": user.tenant_id, "tv": user.token_version},
        "access",
    )


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
) -> AuthUser:
    return await verificar_sesion(decode_token(credentials.credentials), "access")


async def get_current_user_con_cambio_pendiente(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
) -> AuthUser:
    """SOLO para RUTAS_CON_CAMBIO_PENDIENTE (/me y /me/password). Cualquier
    otra ruta usa get_current_user, que niega la sesión con la marca."""
    return await verificar_sesion(decode_token(credentials.credentials), "access", admite_cambio_pendiente=True)


def require_superadmin(user: AuthUser = Depends(get_current_user)) -> AuthUser:
    if user.role != "superadmin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Solo superadmin")
    return user
