import uuid
import logging
import smtplib
import os
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from fastapi import APIRouter, HTTPException, Request, Response, Cookie, status
from pydantic import BaseModel

from auth.models import LoginRequest, LoginResponse, RefreshResponse
from auth.jwt import create_access_token, create_refresh_token, decode_token
from db.connection import get_pool
from db.seed import verify_password, _hash

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth")

MAX_ATTEMPTS = 5
LOCKOUT_MINUTES = 15


@router.post("/login", response_model=LoginResponse)
async def login(req: LoginRequest, request: Request, response: Response):
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT user_id, tenant_id, email, password_hash, role, status, "
                "failed_attempts, locked_until "
                "FROM jax_users WHERE email = %s",
                (req.email,),
            )
            row = await cur.fetchone()

    if not row:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Usuario o contraseña incorrectos")

    user_id, tenant_id, email, password_hash, role, user_status, failed_attempts, locked_until = row

    if user_status != "active":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Usuario inactivo")

    now = datetime.utcnow()

    if locked_until and locked_until > now:
        remaining = int((locked_until - now).total_seconds() / 60) + 1
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail=f"Cuenta bloqueada. Intenta de nuevo en {remaining} minuto(s).",
        )

    if not await verify_password(req.password, password_hash):
        new_attempts = (failed_attempts or 0) + 1
        new_locked_until = None
        if new_attempts >= MAX_ATTEMPTS:
            new_locked_until = now + timedelta(minutes=LOCKOUT_MINUTES)

        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "UPDATE jax_users SET failed_attempts = %s, locked_until = %s WHERE user_id = %s",
                    (new_attempts, new_locked_until, user_id),
                )

        remaining_attempts = max(0, MAX_ATTEMPTS - new_attempts)
        if new_locked_until:
            raise HTTPException(
                status_code=status.HTTP_423_LOCKED,
                detail=f"Cuenta bloqueada por {LOCKOUT_MINUTES} minutos.",
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuario o contraseña incorrectos",
            headers={"X-Attempts-Remaining": str(remaining_attempts)},
        )

    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE jax_users SET failed_attempts = 0, locked_until = NULL, last_login = NOW() "
                "WHERE user_id = %s",
                (user_id,),
            )

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


class ForgotPasswordRequest(BaseModel):
    email: str


@router.post("/forgot-password")
async def forgot_password(req: ForgotPasswordRequest, request: Request):
    pool = await get_pool()
    client_ip = request.client.host if request.client else "unknown"

    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT user_id FROM jax_users WHERE email = %s AND status = 'active'",
                (req.email,),
            )
            row = await cur.fetchone()

    if not row:
        return {"ok": True, "message": "Si el correo existe, recibirás las instrucciones."}

    user_id = row[0]
    token = str(uuid.uuid4())
    expires_at = datetime.utcnow() + timedelta(hours=1)

    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "DELETE FROM password_reset_tokens WHERE user_id = %s AND used = FALSE",
                (user_id,),
            )
            await cur.execute(
                "INSERT INTO password_reset_tokens (user_id, token, expires_at, ip_address) "
                "VALUES (%s, %s, %s, %s)",
                (user_id, token, expires_at, client_ip),
            )

    frontend_origin = os.getenv("FRONTEND_ORIGIN", "https://axioma-ia.io")
    reset_link = f"{frontend_origin}/reset-password?token={token}"

    _send_reset_email(req.email, reset_link)

    return {"ok": True, "message": "Si el correo existe, recibirás las instrucciones."}


def _send_reset_email(to_email: str, reset_link: str):
    smtp_host = os.getenv("SMTP_HOST", "")
    if not smtp_host:
        logger.warning("SMTP_HOST no configurado: no se pudo enviar el correo de recuperación")
        return

    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER", "")
    smtp_pass = os.getenv("SMTP_PASSWORD", "")
    smtp_from = os.getenv("SMTP_FROM", smtp_user)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Recuperación de contraseña — Axioma"
    msg["From"] = smtp_from
    msg["To"] = to_email

    text = f"Para restablecer tu contraseña, accede al siguiente enlace:\n\n{reset_link}\n\nEste enlace expira en 1 hora."
    html = f"""<p>Para restablecer tu contraseña, haz clic en el siguiente enlace:</p>
<p><a href="{reset_link}">{reset_link}</a></p>
<p>Este enlace expira en 1 hora. Si no solicitaste este cambio, ignora este correo.</p>"""

    msg.attach(MIMEText(text, "plain"))
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as server:
            server.starttls()
            if smtp_user:
                server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_from, [to_email], msg.as_string())
    except Exception as exc:
        logger.error("Error enviando email de reset: %s", exc)


class ResetPasswordRequest(BaseModel):
    token: str
    password: str


@router.post("/reset-password")
async def reset_password(req: ResetPasswordRequest):
    if len(req.password) < 8:
        raise HTTPException(status_code=400, detail="La contraseña debe tener al menos 8 caracteres")

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT id, user_id, expires_at, used FROM password_reset_tokens WHERE token = %s",
                (req.token,),
            )
            row = await cur.fetchone()

    if not row:
        raise HTTPException(status_code=400, detail="Token inválido o expirado")

    token_id, user_id, expires_at, used = row

    if used:
        raise HTTPException(status_code=400, detail="Este enlace ya fue utilizado")

    if expires_at < datetime.utcnow():
        raise HTTPException(status_code=400, detail="Token expirado. Solicita uno nuevo.")

    new_hash = _hash(req.password)

    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE jax_users SET password_hash = %s, failed_attempts = 0, locked_until = NULL "
                "WHERE user_id = %s",
                (new_hash, user_id),
            )
            await cur.execute(
                "UPDATE password_reset_tokens SET used = TRUE WHERE id = %s",
                (token_id,),
            )

    return {"ok": True, "message": "Contraseña actualizada correctamente"}
