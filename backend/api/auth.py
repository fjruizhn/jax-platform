import asyncio
import html as html_lib
import uuid
import logging
import secrets
import os
from datetime import timedelta
from tiempo import utc_ahora

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response, Cookie, status
from pydantic import BaseModel, Field

from auth.models import AuthUser, LoginRequest, LoginResponse, MeResponse, RefreshResponse
from auth.jwt import REFRESH_EXPIRE_SECONDS, create_access_token, create_refresh_token, decode_token
from auth.middleware import get_current_user, verificar_sesion
from auth import rate_limit
from auth.password_rules import problema_de_password
from db.connection import get_pool
from db.seed import verify_password, _hash
from jax_engine.background import add_safe_task
import smtp_config
import user_audit
from api.admin.users import _cortar_conexiones
from db.transaccion import transaccion

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth")

MAX_ATTEMPTS = 5
LOCKOUT_MINUTES = 15

# Sin la contraseña correcta, el login responde SIEMPRE esto (2026-09-12).
# Antes delataba qué cuentas existen: email inexistente -> 401 sin bcrypt
# (0,25 ms contra ~150 ms de uno real, medido), y `403 inactivo` / `423
# bloqueada` salían antes de verificar la contraseña.
_CREDENCIALES_INVALIDAS = "Usuario o contraseña incorrectos"
# Hash de relleno para emails inexistentes: mismo costo que los reales
# (_hash usa el gensalt por defecto, 12 -- igual que los de jax_users).
# Una vez al importar, no por request.
_HASH_DE_RELLENO = _hash(secrets.token_urlsafe(18))


def _credenciales_invalidas() -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_CREDENCIALES_INVALIDAS)


def _emitir_tokens(response: Response, user_id: str, tenant_id: str, role: str, token_version: int) -> str:
    """Emite access + refresh con la versión vigente; el refresh va en la
    cookie HttpOnly. Lo usan el login y, desde la etapa 4, el cambio de
    contraseña propio (que sube la versión y tiene que dejarle a ESTA sesión
    tokens nuevos)."""
    refresh_token = create_refresh_token(user_id, tenant_id, role, token_version)
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        max_age=REFRESH_EXPIRE_SECONDS,
        samesite="lax",
    )
    return create_access_token(user_id, tenant_id, role, token_version)


@router.post("/login", response_model=LoginResponse)
async def login(req: LoginRequest, request: Request, response: Response):
    # Antes de la DB y del bcrypt: sin límite, cada intento (exista o no el
    # email) le cuesta ~155 ms de CPU al servidor. Ver auth/rate_limit.py.
    rate_limit.check_login_rate(request, req.email)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT user_id, tenant_id, email, password_hash, role, status, "
                "failed_attempts, locked_until, token_version "
                "FROM jax_users WHERE email = %s",
                (req.email,),
            )
            row = await cur.fetchone()

    if not row:
        # El mismo bcrypt que pagaría una cuenta real: sin esto, el tiempo de
        # respuesta dice si el email existe.
        await verify_password(req.password, _HASH_DE_RELLENO)
        raise _credenciales_invalidas()

    user_id, tenant_id, email, password_hash, role, user_status, failed_attempts, locked_until, token_version = row
    now = utc_ahora()
    bloqueada = bool(locked_until and locked_until > now)

    if not await verify_password(req.password, password_hash):
        # El contador y el bloqueo siguen como antes, y solo para cuentas que
        # antes llegaban a este punto (activas y sin bloqueo vigente): un
        # intento durante el bloqueo no lo extiende. Lo que cambia es la
        # respuesta -- la misma para todos, incluido el intento que bloquea.
        if user_status == "active" and not bloqueada:
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
        raise _credenciales_invalidas()

    # Contraseña correcta: quien ya probó ser el dueño sí ve el estado.
    if user_status != "active":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Usuario inactivo")

    if bloqueada:
        remaining = int((locked_until - now).total_seconds() / 60) + 1
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail=f"Cuenta bloqueada. Intenta de nuevo en {remaining} minuto(s).",
        )

    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE jax_users SET failed_attempts = 0, locked_until = NULL, last_login = NOW() "
                "WHERE user_id = %s",
                (user_id,),
            )

    access = _emitir_tokens(response, str(user_id), str(tenant_id), role, token_version)

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
    # La misma verificación que cada request (etapa 2): un refresh de un
    # usuario desactivado o con la versión vieja ya no reemite nada. El access
    # nuevo lleva el rol y la versión de la BASE, no los del refresh.
    # La cookie NO se rota: rotarla haría deslizante la sesión de 7 días, y la
    # versión ya invalida el refresh viejo cuando hace falta.
    user = await verificar_sesion(decode_token(refresh_token), "refresh")
    access = create_access_token(user.user_id, user.tenant_id, user.role, user.token_version)
    return RefreshResponse(access_token=access)


@router.get("/me", response_model=MeResponse)
async def me(user: AuthUser = Depends(get_current_user)):
    # get_current_user ya pasó por verificar_sesion (una SELECT por PK que
    # trae status/role/token_version/email): no hace falta un segundo SELECT
    # acá. Si el usuario no existiera, verificar_sesion ya habría cortado con
    # 401 (fail-closed) antes de llegar a este punto (code review, M-3).
    return MeResponse(
        user_id=int(user.user_id),
        tenant_id=int(user.tenant_id),
        role=user.role,
        email=user.email,
    )


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie(key="refresh_token", samesite="lax")
    return {"ok": True}


class CambioPasswordRequest(BaseModel):
    current_password: str = Field(max_length=1024)
    new_password: str = Field(max_length=1024)


@router.post("/me/password", response_model=RefreshResponse)
async def cambiar_mi_password(
    req: CambioPasswordRequest,
    request: Request,
    response: Response,
    user: AuthUser = Depends(get_current_user),
):
    """Mi cuenta (2026-09-15, admin usuarios etapa 4, spec §3.2 y §3.4). Exige
    la contraseña actual, con el mismo límite de intentos que el login (sin él,
    un token robado sirve para adivinar la contraseña a velocidad de CPU).
    Sube token_version: se cierran las OTRAS sesiones, y esta recibe tokens
    nuevos (access en la respuesta, refresh en la cookie)."""
    user_id = int(user.user_id)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT email, password_hash FROM jax_users WHERE user_id = %s", (user_id,))
            fila = await cur.fetchone()
    if fila is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="sesion_invalida")
    email, hash_verificado = fila
    rate_limit.check_login_rate(request, email)
    # 400 y no 401: un 401 dispara el refresh del frontend y, al fallar, lo desloguea.
    actual_incorrecta = HTTPException(status_code=400, detail="password_actual_incorrecta")
    if not await verify_password(req.current_password, hash_verificado):
        raise actual_incorrecta
    problema = problema_de_password(req.new_password)
    if problema:
        raise HTTPException(status_code=400, detail=f"password_{problema}")
    # bcrypt (~150 ms) fuera del event loop y fuera de la transacción.
    nuevo_hash = await asyncio.to_thread(_hash, req.new_password)
    ip = rate_limit.client_ip(request, rate_limit.TRUSTED_PROXIES)
    # Una transacción: la verificación queda atada a la escritura porque la
    # fila se relee con FOR UPDATE y el hash tiene que ser EL MISMO que se
    # verificó (si otra pestaña o un reset lo cambió en el medio, no se pisa:
    # la contraseña presentada ya no es la vigente). El bcrypt de la
    # verificación no corre con el bloqueo tomado.
    # Bloqueos: una sola fila de jax_users, por PK (y el INSERT de auditoría,
    # cuya FK apunta a esa misma fila). Las escrituras de admin toman el
    # conjunto de superadmins y luego su destino; esta transacción no tiene
    # nada tomado mientras espera su única fila, y teniéndola no pide ninguna
    # otra de jax_users -> no hay ciclo posible con ellas (no hace falta
    # _leer_para_actualizar, que serializaría todo cambio de contraseña con
    # las escrituras de admin sin necesidad).
    async with transaccion() as cur:
        await cur.execute("SELECT password_hash FROM jax_users WHERE user_id = %s FOR UPDATE", (user_id,))
        vigente = await cur.fetchone()
        if vigente is None or vigente[0] != hash_verificado:
            raise actual_incorrecta
        await cur.execute(
            "UPDATE jax_users SET password_hash = %s, token_version = token_version + 1, "
            "failed_attempts = 0, locked_until = NULL WHERE user_id = %s",
            (nuevo_hash, user_id),
        )
        await cur.execute("SELECT token_version FROM jax_users WHERE user_id = %s", (user_id,))
        (nueva_version,) = await cur.fetchone()
        await user_audit.registrar(cur, user_id, user_id, "password_changed_self", None, ip)
    # Ruling U9 (spec §3.2): después del commit, fail-soft. Se corta también
    # la pestaña que hizo el cambio: reconecta sola con el token nuevo.
    await _cortar_conexiones(user_id)
    access = _emitir_tokens(response, user.user_id, user.tenant_id, user.role, nueva_version)
    return RefreshResponse(access_token=access)


class ForgotPasswordRequest(BaseModel):
    # 254 = máximo de RFC 5321. Sin tope, el email (hasta 50 MB por nginx)
    # queda como clave del limitador hasta que el LRU lo expulsa.
    email: str = Field(max_length=254)


_MENSAJE_RECUPERACION = "Si el correo existe, recibirás las instrucciones."


@router.post("/forgot-password")
async def forgot_password(req: ForgotPasswordRequest, request: Request, background_tasks: BackgroundTasks):
    # 2026-09-12. La respuesta NO puede depender de si la cuenta existe: antes,
    # con una real se hacía DELETE + INSERT + SMTP síncrono (hasta 10 s, y
    # bloqueando el event loop) y con una inexistente se respondía al
    # instante -- el tiempo delataba la cuenta, lo mismo que se cerró en el
    # login (#57). Ahora el request no consulta nada: la búsqueda, el token y
    # el correo van en segundo plano, y la respuesta es siempre la misma.
    # Tampoco tenía límite (se podía inundar de correos a una víctima): comparte
    # el del login, por IP y por email.
    email = req.email.strip()
    rate_limit.check_login_rate(request, email)
    ip = rate_limit.client_ip(request, rate_limit.TRUSTED_PROXIES)
    add_safe_task(background_tasks, _procesar_recuperacion, email, ip)
    return {"ok": True, "message": _MENSAJE_RECUPERACION}


async def _procesar_recuperacion(email: str, client_ip: str) -> None:
    """Crea el token y manda el correo, fuera del request. El correo va al
    email GUARDADO, no al que mandó el cliente."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT user_id, email FROM jax_users WHERE email = %s AND status = 'active'",
                    (email,),
                )
                row = await cur.fetchone()
        if not row:
            return

        user_id, email_guardado = row

        # Sin correo configurado (o con la configuración rota) no se crea un
        # token que nadie va a recibir: queda en el log con el código. La
        # respuesta pública ya salió, neutra, antes de esto.
        try:
            settings = await smtp_config.cargar_settings()
        except smtp_config.SmtpNoDisponible as exc:
            # El motivo es un código estable (p. ej. "password_ilegible"), sin secretos.
            logger.error("Recuperación de contraseña: correo deshabilitado (%s, motivo: %s); no se creó el token",
                         exc.codigo, getattr(exc, "motivo", "-"))
            return

        token = str(uuid.uuid4())
        expires_at = utc_ahora() + timedelta(hours=1)

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
        # smtplib es bloqueante: a un hilo, nunca en el event loop.
        await asyncio.to_thread(_send_reset_email, settings, email_guardado, reset_link)
    except Exception:  # fail-soft: corre después de responder; no hay a quién devolverle el error, queda en el log
        logger.exception("Recuperación de contraseña: falló el procesamiento en segundo plano")


ASUNTO_RECUPERACION = "Recuperación de contraseña — Axioma"


def _send_reset_email(settings: smtp_config.SmtpSettings, to_email: str, reset_link: str) -> None:
    """Bloqueante (smtplib): se llama dentro de asyncio.to_thread. LANZA si el
    servidor falla -- el reset público lo registra en _procesar_recuperacion;
    el reset por admin (etapa 4) se lo muestra al admin. Antes leía SMTP_* del
    entorno, que nunca estuvieron definidas: "¿Olvidaste tu contraseña?" no
    envió un solo correo (spec §1, hallazgo 8)."""
    texto = (
        f"Para restablecer tu contraseña, accede al siguiente enlace:\n\n{reset_link}\n\n"
        "Este enlace expira en 1 hora."
    )
    # El enlace va escapado en el HTML (href y texto): FRONTEND_ORIGIN o el
    # token podrían traer comillas o "<". El texto plano va tal cual.
    enlace = html_lib.escape(reset_link, quote=True)
    html = (
        "<p>Para restablecer tu contraseña, haz clic en el siguiente enlace:</p>"
        f'<p><a href="{enlace}">{enlace}</a></p>'
        "<p>Este enlace expira en 1 hora. Si no solicitaste este cambio, ignora este correo.</p>"
    )
    mensaje = smtp_config.construir_mensaje(settings, to_email, ASUNTO_RECUPERACION, texto, html)
    smtp_config.enviar(settings, mensaje)


class ResetPasswordRequest(BaseModel):
    token: str
    password: str


@router.post("/reset-password")
async def reset_password(req: ResetPasswordRequest):
    # Códigos estables (2026-09-12): el frontend los traduce con i18n. Antes
    # mostraba este `detail` tal cual, en español aunque la UI estuviera en inglés.
    # La regla es la única del sistema (etapa 4, auth/password_rules.py); acá
    # se conservan los códigos reset_password_* que ya usa ResetPassword.jsx.
    # bcrypt 5 lanza ValueError con más de 72 bytes: era un 500.
    problema = problema_de_password(req.password)
    if problema:
        raise HTTPException(status_code=400, detail=f"reset_password_{problema}")

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT id, user_id, expires_at, used FROM password_reset_tokens WHERE token = %s",
                (req.token,),
            )
            row = await cur.fetchone()

    if not row:
        raise HTTPException(status_code=400, detail="reset_token_invalido")

    token_id, user_id, expires_at, used = row

    if used:
        raise HTTPException(status_code=400, detail="reset_token_usado")

    if expires_at < utc_ahora():
        raise HTTPException(status_code=400, detail="reset_token_expirado")

    # bcrypt de costo 12 (~150 ms de CPU): en un hilo, no en el event loop.
    new_hash = await asyncio.to_thread(_hash, req.password)

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
