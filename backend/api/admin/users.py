import bcrypt
from tiempo import utc_ahora
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict

import user_audit
from api.events import close_user_streams
from auth import rate_limit
from auth.middleware import require_superadmin
from auth.models import AuthUser
from db.connection import get_pool
from db.transaccion import transaccion
from jax_engine.websocket_hub import ws_hub

router = APIRouter(prefix="/api/admin")

VALID_ROLES = {"superadmin", "operator", "viewer"}
# `deleted` NO se pone por acá: solo la baja (etapa 5).
ESTADOS_EDITABLES = {"active", "inactive"}


def _hash(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def _ip(request: Request) -> str:
    return rate_limit.client_ip(request, rate_limit.TRUSTED_PROXIES)


# --------------------------------------------------------------- guardas
# (2026-09-12, admin usuarios etapa 3, spec §3.3). Reemplazan al literal
# `user_id == 1` que tenía delete_user: lo que se protege no es una fila, es
# que el sistema tenga siempre al menos un superadmin activo.

def guarda_auto_accion(actor_id: int, target_id: int) -> None:
    """Nadie se degrada, se desactiva ni se da de baja a sí mismo; su
    contraseña la cambia en "Mi cuenta"."""
    if actor_id == target_id:
        raise HTTPException(status_code=403, detail="auto_accion_prohibida")


def pierde_superadmin_activo(rol_actual: str, estado_actual: str, nuevo_rol: str, nuevo_estado: str) -> bool:
    return rol_actual == "superadmin" and estado_actual == "active" and (
        nuevo_rol != "superadmin" or nuevo_estado != "active")


# FOR UPDATE, dentro de la transacción del cambio: dos admins que se degradan
# mutuamente a la vez se serializan acá, y el segundo ve el resultado del
# primero. Filtra por idx_jax_users_role_status; verificado con EXPLAIN en
# tests/test_user_audit.py::test_conteo_de_superadmins_usa_el_indice_role_status.
SQL_OTROS_SUPERADMINS_ACTIVOS = (
    "SELECT user_id FROM jax_users WHERE role = 'superadmin' AND status = 'active' "
    "AND user_id <> %s FOR UPDATE"
)


async def otros_superadmins_activos(cur, excluido: int) -> int:
    await cur.execute(SQL_OTROS_SUPERADMINS_ACTIVOS, (excluido,))
    return len(await cur.fetchall())


async def exigir_invariante(cur, target_id: int, rol_actual: str, estado_actual: str,
                            nuevo_rol: str, nuevo_estado: str) -> None:
    if pierde_superadmin_activo(rol_actual, estado_actual, nuevo_rol, nuevo_estado) \
            and await otros_superadmins_activos(cur, target_id) == 0:
        raise HTTPException(status_code=409, detail="ultimo_superadmin")


async def _leer_para_actualizar(cur, user_id: int):
    await cur.execute("SELECT role, status FROM jax_users WHERE user_id = %s FOR UPDATE", (user_id,))
    return await cur.fetchone()


async def _cortar_conexiones(user_id: int) -> None:
    """Step 4b (Ruling U5): cierra el WS (4001) y los streams SSE ya abiertos
    del usuario. Se llama DESPUÉS del commit, nunca dentro de la transacción:
    un rollback no debe haber cortado sesiones."""
    await ws_hub.close_user(str(user_id))
    await close_user_streams(str(user_id))


@router.get("/users")
async def list_users(user: AuthUser = Depends(require_superadmin)):
    pool = await get_pool()
    now = utc_ahora()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT user_id, email, role, status, created_at, last_login, "
                "failed_attempts, locked_until "
                "FROM jax_users ORDER BY user_id"
            )
            rows = await cur.fetchall()
    return {
        "users": [
            {
                "user_id": r[0],
                "email": r[1],
                "role": r[2],
                "status": r[3],
                "created_at": r[4].isoformat() if r[4] else None,
                "last_login": r[5].isoformat() if r[5] else None,
                "failed_attempts": r[6] or 0,
                "locked_until": r[7].isoformat() if r[7] else None,
                "is_locked": bool(r[7] and r[7] > now),
            }
            for r in rows
        ]
    }


class CreateUserRequest(BaseModel):
    email: str
    role: str
    password: str


@router.post("/users")
async def create_user(req: CreateUserRequest, user: AuthUser = Depends(require_superadmin)):
    if req.role not in VALID_ROLES:
        raise HTTPException(status_code=400, detail=f"Rol inválido: {req.role}")

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT COUNT(*) FROM jax_users WHERE email = %s", (req.email,))
            (count,) = await cur.fetchone()
            if count > 0:
                raise HTTPException(status_code=409, detail="Email ya existe")

            ph = _hash(req.password)
            await cur.execute(
                "INSERT INTO jax_users (tenant_id, email, password_hash, role, status) VALUES (1, %s, %s, %s, 'active')",
                (req.email, ph, req.role),
            )
            new_id = cur.lastrowid
    return {"user_id": new_id, "email": req.email, "role": req.role, "status": "active"}


class UpdateUserRequest(BaseModel):
    # extra="forbid": un campo que ya no existe (password) responde 422 en vez
    # de ignorarse en silencio. La contraseña la cambia el dueño en Mi cuenta
    # o por enlace de recuperación (etapa 4).
    model_config = ConfigDict(extra="forbid")
    role: Optional[str] = None
    status: Optional[str] = None


@router.put("/users/{user_id}")
async def update_user(
    user_id: int,
    req: UpdateUserRequest,
    request: Request,
    user: AuthUser = Depends(require_superadmin),
):
    if req.role is not None and req.role not in VALID_ROLES:
        raise HTTPException(status_code=400, detail="rol_invalido")
    if req.status is not None and req.status not in ESTADOS_EDITABLES:
        raise HTTPException(status_code=400, detail="estado_invalido")
    actor_id = int(user.user_id)
    async with transaccion() as cur:
        actual = await _leer_para_actualizar(cur, user_id)
        if actual is None:
            raise HTTPException(status_code=404, detail="usuario_no_encontrado")
        rol_actual, estado_actual = actual
        nuevo_rol = req.role if req.role is not None else rol_actual
        nuevo_estado = req.status if req.status is not None else estado_actual
        cambia_rol, cambia_estado = nuevo_rol != rol_actual, nuevo_estado != estado_actual
        if not (cambia_rol or cambia_estado):
            return {"ok": True}
        guarda_auto_accion(actor_id, user_id)
        await exigir_invariante(cur, user_id, rol_actual, estado_actual, nuevo_rol, nuevo_estado)
        # Rol o estado nuevos: todas las sesiones del usuario se cortan en el
        # request siguiente (spec §3.2).
        await cur.execute(
            "UPDATE jax_users SET role = %s, status = %s, token_version = token_version + 1 WHERE user_id = %s",
            (nuevo_rol, nuevo_estado, user_id),
        )
        ip = _ip(request)
        if cambia_rol:
            await user_audit.registrar(cur, actor_id, user_id, "update_role", {"from": rol_actual, "to": nuevo_rol}, ip)
        if cambia_estado:
            await user_audit.registrar(cur, actor_id, user_id, "update_status",
                                       {"from": estado_actual, "to": nuevo_estado}, ip)
    # ...y las conexiones ya abiertas, ahora (Step 4b), tras el commit.
    await _cortar_conexiones(user_id)
    return {"ok": True}


@router.post("/users/{user_id}/unlock")
async def unlock_user(user_id: int, user: AuthUser = Depends(require_superadmin)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE jax_users SET failed_attempts = 0, locked_until = NULL WHERE user_id = %s",
                (user_id,),
            )
    return {"ok": True}


@router.delete("/users/{user_id}")
async def delete_user(user_id: int, user: AuthUser = Depends(require_superadmin)):
    # Sigue existiendo hasta la etapa 5 (que lo reemplaza por la baja). El
    # literal `user_id == 1` ya no está: lo reemplazan las guardas.
    guarda_auto_accion(int(user.user_id), user_id)
    async with transaccion() as cur:
        actual = await _leer_para_actualizar(cur, user_id)
        if actual is None:
            raise HTTPException(status_code=404, detail="usuario_no_encontrado")
        rol_actual, estado_actual = actual
        await exigir_invariante(cur, user_id, rol_actual, estado_actual, rol_actual, "deleted")
        await cur.execute("DELETE FROM jax_users WHERE user_id = %s", (user_id,))
    await _cortar_conexiones(user_id)
    return {"ok": True}
