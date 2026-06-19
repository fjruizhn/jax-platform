from fastapi import APIRouter, HTTPException, Response, Cookie, status
from auth.models import LoginRequest, LoginResponse, RefreshResponse
from auth.jwt import create_access_token, create_refresh_token, decode_token
from db.connection import get_pool
from db.seed import verify_password

router = APIRouter(prefix="/api/auth")


@router.post("/login", response_model=LoginResponse)
async def login(req: LoginRequest, response: Response):
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT user_id, tenant_id, email, password_hash, role, status "
                "FROM jax_users WHERE email = %s",
                (req.email,),
            )
            row = await cur.fetchone()

    if not row:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Credenciales inválidas")

    user_id, tenant_id, email, password_hash, role, user_status = row

    if user_status != "active":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Usuario inactivo")

    if not await verify_password(req.password, password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Credenciales inválidas")

    access = create_access_token(str(user_id), str(tenant_id), role)
    refresh = create_refresh_token(str(user_id), str(tenant_id), role)

    response.set_cookie(
        key="refresh_token",
        value=refresh,
        httponly=True,
        max_age=7 * 24 * 3600,
        samesite="lax",
    )

    return LoginResponse(
        access_token=access,
        user_id=user_id,
        tenant_id=tenant_id,
        role=role,
        email=email,
    )


@router.post("/refresh", response_model=RefreshResponse)
async def refresh(refresh_token: str = Cookie(None)):
    if not refresh_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sin refresh token")

    payload = decode_token(refresh_token)
    if payload.get("type") != "refresh":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido")

    access = create_access_token(
        str(payload["user_id"]),
        str(payload["tenant_id"]),
        payload["role"],
    )
    return RefreshResponse(access_token=access)
