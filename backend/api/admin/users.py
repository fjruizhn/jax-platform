import bcrypt
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from auth.middleware import require_superadmin
from auth.models import AuthUser
from db.connection import get_pool

router = APIRouter(prefix="/api/admin")

VALID_ROLES = {"superadmin", "operator", "viewer"}


def _hash(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


@router.get("/users")
async def list_users(user: AuthUser = Depends(require_superadmin)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT user_id, email, role, status, created_at, last_login FROM jax_users ORDER BY user_id"
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
    role: Optional[str] = None
    status: Optional[str] = None
    password: Optional[str] = None


@router.put("/users/{user_id}")
async def update_user(
    user_id: int,
    req: UpdateUserRequest,
    user: AuthUser = Depends(require_superadmin),
):
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            if req.role:
                if req.role not in VALID_ROLES:
                    raise HTTPException(status_code=400, detail="Rol inválido")
                await cur.execute("UPDATE jax_users SET role = %s WHERE user_id = %s", (req.role, user_id))
            if req.status:
                await cur.execute("UPDATE jax_users SET status = %s WHERE user_id = %s", (req.status, user_id))
            if req.password:
                ph = _hash(req.password)
                await cur.execute("UPDATE jax_users SET password_hash = %s WHERE user_id = %s", (ph, user_id))
    return {"ok": True}


@router.delete("/users/{user_id}")
async def delete_user(user_id: int, user: AuthUser = Depends(require_superadmin)):
    if user_id == 1:
        raise HTTPException(status_code=403, detail="No se puede eliminar el superadmin principal")
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM jax_users WHERE user_id = %s", (user_id,))
    return {"ok": True}
