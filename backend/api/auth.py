import asyncio
import html as html_lib
import os
import uuid
import logging
import math
import secrets
import time
from datetime import timedelta, timezone
from tiempo import utc_ahora

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response, Cookie, status
from pydantic import BaseModel, ConfigDict, Field, StrictStr

import ajustes
from auth.models import AuthUser, LoginRequest, LoginResponse, MFALoginChallengeResponse, MeResponse, RefreshResponse
from auth.jwt import (create_access_token, create_refresh_token,
                      create_refresh_token_preserving_window, decode_token)
from auth.middleware import get_current_user, get_current_user_con_cambio_pendiente, verificar_sesion
from auth import rate_limit
from auth.password_rules import problema_de_password
from db.connection import get_pool
from db.seed import verify_password, _hash
from jax_engine.background import add_safe_task
import smtp_config
import user_audit
from auth.conexiones import _cortar_conexiones
from db.transaccion import AISLAMIENTO_ADMIN, transaccion
from virtual_office.mfa import (
    MFAConfig, MFADenied, issue_login_challenge_after_password_in_cursor,
    verify_and_consume_totp_in_cursor, issue_step_up_challenge_in_cursor,
    start_first_enrollment_after_password_in_cursor, confirm_enrollment_in_cursor,
    consume_recovery_code_in_cursor, start_recovery_enrollment_in_cursor,
    regenerate_recovery_codes_after_password_in_cursor, disable_mfa_after_password_in_cursor,
    reset_mfa_by_office_root_in_cursor,
    load_mfa_config_from_environment,
    lock_office_identity_pair_in_cursor,
)
from virtual_office.authority import authorize_office_in_cursor
from virtual_office.audit import record_authorization_event
from virtual_office.capabilities import OfficeOperation

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth")

MAX_ATTEMPTS = 5
LOCKOUT_MINUTES = 15

SESION_EXPIRADA = "sesion_expirada"


async def vida_de_sesion_segundos() -> int:
    """session_timeout_min en segundos. AjusteIlegible -> 503 (main.py)."""
    return await ajustes.valor(ajustes.SESION) * 60


def sesion_vencida(payload: dict, vida_segundos: int, ahora: float) -> bool:
    """Vida ABSOLUTA desde la emisión (el refresh no se rota). Sin `iat`
    entero, el token es anterior al frente C y no hay con qué medirlo: vencido
    (una sola vez, al desplegar, cada sesión vuelve a entrar)."""
    iat = payload.get("iat")
    if not isinstance(iat, int) or isinstance(iat, bool):
        return True
    return ahora - iat > vida_segundos


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


def _cuenta_bloqueada(locked_until, ahora) -> HTTPException:
    """423 con código y segundos (A-50, 2026-09-16), igual que el 429 del
    limitador: el frontend no interpreta texto."""
    segundos = max(1, math.ceil((locked_until - ahora).total_seconds()))
    return HTTPException(
        status_code=status.HTTP_423_LOCKED,
        detail={"code": "cuenta_bloqueada", "retry_after_seconds": segundos},
        headers={"Retry-After": str(segundos)},
    )


def _emitir_tokens(response: Response, user_id: str, tenant_id: str, role: str, token_version: int,
                   vida_segundos: int, *, mfa_proof_event_id: int | None = None,
                   mfa_version: int | None = None, mfa_at: int | None = None) -> str:
    """Emite access + refresh con la versión vigente; el refresh va en la
    cookie HttpOnly y vive lo que diga session_timeout_min (frente C). Lo
    usan el login y el cambio de contraseña propio. `vida_segundos` se lee
    ANTES de escribir nada: un ajuste ilegible no puede dejar la versión
    subida y la sesión sin tokens."""
    refresh_token = create_refresh_token(
        user_id, tenant_id, role, token_version, vida_segundos=vida_segundos,
        mfa_proof_event_id=mfa_proof_event_id, mfa_version=mfa_version, mfa_at=mfa_at,
    )
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        max_age=vida_segundos,
        samesite="lax",
    )
    return create_access_token(user_id, tenant_id, role, token_version,
                               mfa_proof_event_id=mfa_proof_event_id,
                               mfa_version=mfa_version, mfa_at=mfa_at)


_mfa_config = load_mfa_config_from_environment


def _mfa_unavailable() -> HTTPException:
    return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="office_mfa_unavailable",
                         headers={"Cache-Control": "no-store"})


@router.post("/login", response_model=LoginResponse | MFALoginChallengeResponse)
async def login(req: LoginRequest, request: Request, response: Response):
    # Antes de la DB y del bcrypt: sin límite, cada intento (exista o no el
    # email) le cuesta ~155 ms de CPU al servidor. Ver auth/rate_limit.py.
    rate_limit.check_login_rate(request, req.email)
    # Antes de la DB, del bcrypt y del bump de token_version (frente C).
    vida = await vida_de_sesion_segundos()
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT user_id, tenant_id, email, password_hash, role, status, "
                "failed_attempts, locked_until, token_version, must_change_password, account_class "
                "FROM jax_users WHERE email = %s",
                (req.email,),
            )
            row = await cur.fetchone()

    if not row:
        # El mismo bcrypt que pagaría una cuenta real: sin esto, el tiempo de
        # respuesta dice si el email existe.
        await verify_password(req.password, _HASH_DE_RELLENO)
        raise _credenciales_invalidas()

    (user_id, tenant_id, email, password_hash, role, user_status, failed_attempts, locked_until, token_version,
     must_change_password, account_class) = row
    if account_class not in {"INTERNAL", "OFFICE_INTERNAL", "EXTERNAL_CLIENT"}:
        raise _credenciales_invalidas()
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
        raise _cuenta_bloqueada(locked_until, now)

    # A factor changes the password-success contract: it creates only a
    # short-lived opaque capability.  In particular no cookie or JWT is set
    # before the TOTP verifier atomically produces its durable receipt.
    async with transaccion(AISLAMIENTO_ADMIN) as cur:
        await cur.execute(
            "SELECT password_hash,status FROM jax_users WHERE user_id=%s FOR UPDATE",
            (user_id,),
        )
        current = await cur.fetchone()
        if current is None or current[0] != password_hash:
            raise _credenciales_invalidas()
        if current[1] != "active":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Usuario inactivo")
        await cur.execute("SELECT state FROM office_mfa_credential WHERE user_id=%s FOR UPDATE", (user_id,))
        credential = await cur.fetchone()
        if credential is not None and credential[0] == "ACTIVE":
            config = _mfa_config()
            if config is None:
                raise _mfa_unavailable()
            challenge = await issue_login_challenge_after_password_in_cursor(
                cur, preauthenticated_user_id=int(user_id), config=config,
            )
            if isinstance(challenge, MFADenied):
                raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=challenge.code)
            response.headers["Cache-Control"] = "no-store"
            return MFALoginChallengeResponse(
                challenge_token=challenge.token,
                expires_at=challenge.expires_at.isoformat(),
            )

    # Sesión única (2026-09-15, Task 3b, Ruling F2): el login EXITOSO -- y
    # sólo él; ningún camino de error de arriba escribe token_version -- sube
    # la versión, así que toda sesión anterior de este usuario muere. Sube y
    # RELEE en la misma transacción: la fila queda bloqueada hasta el commit,
    # así que con dos logins simultáneos el segundo UPDATE espera, incrementa
    # sobre el primero y cada uno emite la versión que él escribió (gana el
    # último). Nunca "versión leída arriba + 1": esa lectura no bloquea, y dos
    # logins emitirían la misma versión vieja -> ninguna sesión viva.
    # `status = 'active'` en el WHERE: si en la ventana del bcrypt un admin lo
    # desactivó o lo dio de baja, 0 filas -> la misma respuesta que un inactivo.
    # Bloqueos: una sola fila de jax_users, por PK, sin nada tomado antes ni
    # pedido después (el mismo argumento que Mi cuenta: sin ciclo con las
    # escrituras de admin). READ COMMITTED (U33). bcrypt quedó arriba, fuera.
    # Fix ronda 1 (2026-09-15, F1): `password_hash = %s` ata el bump al hash
    # que verificó el bcrypt. Si en esa ventana confirmó un cambio de
    # contraseña (fijar por admin, reset, Mi cuenta), la contraseña probada ya
    # no es la vigente: 0 filas, sin tokens, sin cookie, sin corte -- si no, el
    # bump se apilaba sobre la versión nueva y emitía tokens VÁLIDOS con la
    # marca vieja. (Revocar sesiones y desbloquear no cambian el hash: un login
    # que llega después de ellos sigue ganando, a propósito.)
    async with transaccion(AISLAMIENTO_ADMIN) as cur:
        await cur.execute(
            "UPDATE jax_users SET failed_attempts = 0, locked_until = NULL, last_login = NOW(), "
            "token_version = token_version + 1 "
            "WHERE user_id = %s AND status = 'active' AND password_hash = %s",
            (user_id, password_hash),
        )
        if cur.rowcount != 1:
            # Por PK, dentro de la misma transacción: decide sólo la respuesta.
            await cur.execute("SELECT status FROM jax_users WHERE user_id = %s", (user_id,))
            vigente = await cur.fetchone()
            if vigente is None or vigente[0] != "active":
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Usuario inactivo")
            raise _credenciales_invalidas()
        await cur.execute("SELECT token_version FROM jax_users WHERE user_id = %s", (user_id,))
        (token_version,) = await cur.fetchone()

    # Ruling U9: después del commit, fail-soft. Cierra el WS/SSE de la sesión
    # vieja ya mismo (si no, seguirían abiertos hasta su próxima verificación).
    await _cortar_conexiones(user_id)
    access = _emitir_tokens(response, str(user_id), str(tenant_id), role, token_version, vida)

    return LoginResponse(
        access_token=access,
        user_id=user_id,
        tenant_id=tenant_id,
        role=role,
        email=email,
        must_change_password=bool(must_change_password),
        account_class=account_class,
    )


class _MFARequest(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", hide_input_in_errors=True)


class MFALoginVerifyRequest(_MFARequest):
    challenge_token: StrictStr = Field(min_length=43, max_length=43, pattern=r"^[A-Za-z0-9_-]{43}$")
    otp: StrictStr = Field(min_length=6, max_length=6, pattern=r"^[0-9]{6}$")


class MFAStepUpVerifyRequest(_MFARequest):
    challenge_token: StrictStr = Field(min_length=43, max_length=43, pattern=r"^[A-Za-z0-9_-]{43}$")
    otp: StrictStr = Field(min_length=6, max_length=6, pattern=r"^[0-9]{6}$")


class MFAPasswordRequest(_MFARequest):
    password: StrictStr = Field(min_length=1, max_length=1024)


class MFAEnrollmentConfirmRequest(_MFARequest):
    enrollment_token: StrictStr = Field(min_length=43, max_length=43, pattern=r"^[A-Za-z0-9_-]{43}$")
    otp: StrictStr = Field(min_length=6, max_length=6, pattern=r"^[0-9]{6}$")


class MFARecoveryRequest(_MFARequest):
    recovery_code: StrictStr = Field(min_length=32, max_length=32, pattern=r"^[A-Za-z0-9_-]{32}$")


class MFARecoveryEnrollmentRequest(_MFARequest):
    recovery_token: StrictStr = Field(min_length=43, max_length=43, pattern=r"^[A-Za-z0-9_-]{43}$")


class MFARootResetRequest(_MFARequest):
    target_user_id: int = Field(gt=0, strict=True)


async def _verify_current_password_in_cursor(cur, user_id: int, password: str) -> bool:
    await cur.execute("SELECT password_hash,status FROM jax_users WHERE user_id=%s FOR UPDATE", (user_id,))
    row = await cur.fetchone()
    return bool(row and row[1] == "active" and await verify_password(password, row[0]))


def _mfa_denial(denial: MFADenied) -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=denial.code,
                         headers={"Cache-Control": "no-store"})


def _check_mfa_password_rate(request: Request, user: AuthUser) -> None:
    """Use the canonical bounded login limiter before any MFA bcrypt check."""
    rate_limit.check_login_rate(request, user.email or "")


@router.post("/mfa/login/verify", response_model=LoginResponse)
async def verify_mfa_login(req: MFALoginVerifyRequest, response: Response):
    """Consume a password-bound LOGIN capability and only then mint a session."""
    config = _mfa_config()
    if config is None:
        raise _mfa_unavailable()
    vida = await vida_de_sesion_segundos()
    denial: MFADenied | None = None
    async with transaccion(AISLAMIENTO_ADMIN) as cur:
        receipt = await verify_and_consume_totp_in_cursor(
            cur, challenge_token=req.challenge_token, otp=req.otp,
            server_correlation_id=uuid.uuid4().bytes,
        )
        if isinstance(receipt, MFADenied):
            # A denial commits its truthful attempt state; it never gets a
            # cookie or a server-minted proof substitute.
            denial = receipt
        elif receipt.purpose != "LOGIN":
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="challenge_purpose",
                                headers={"Cache-Control": "no-store"})
        else:
            await cur.execute(
                "SELECT tenant_id,email,role,must_change_password,account_class,status "
                "FROM jax_users WHERE user_id=%s FOR UPDATE", (receipt.subject_user_id,),
            )
            user = await cur.fetchone()
            if user is None or user[5] != "active":
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="sesion_invalida",
                                    headers={"Cache-Control": "no-store"})
    if denial is not None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=denial.code)
    tenant_id, email, role, must_change_password, account_class, _ = user
    # The JWT represents the exact UTC receipt instant.  Do not relabel the
    # database DATETIME as local server time.
    mfa_at = int(receipt.occurred_at.astimezone(timezone.utc).timestamp())
    await _cortar_conexiones(receipt.subject_user_id)
    response.headers["Cache-Control"] = "no-store"
    access = _emitir_tokens(
        response, str(receipt.subject_user_id), str(tenant_id), role,
        receipt.token_version, vida, mfa_proof_event_id=receipt.event_id,
        mfa_version=receipt.mfa_version, mfa_at=mfa_at,
    )
    return LoginResponse(
        access_token=access, user_id=receipt.subject_user_id, tenant_id=tenant_id,
        role=role, email=email, must_change_password=bool(must_change_password),
        account_class=account_class,
    )


@router.get("/mfa/status")
async def mfa_status(response: Response, user: AuthUser = Depends(get_current_user)):
    """Expose state only; no secret, capability, ciphertext or code escapes."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT state FROM office_mfa_credential WHERE user_id=%s", (int(user.user_id),))
            row = await cur.fetchone()
    response.headers["Cache-Control"] = "no-store"
    return {"enabled": bool(row and row[0] == "ACTIVE"), "state": row[0] if row else "NONE"}


@router.post("/mfa/enrollment/start")
async def mfa_enrollment_start(req: MFAPasswordRequest, request: Request, response: Response, user: AuthUser = Depends(get_current_user)):
    _check_mfa_password_rate(request, user)
    config = _mfa_config()
    if config is None: raise _mfa_unavailable()
    async with transaccion(AISLAMIENTO_ADMIN) as cur:
        if not await _verify_current_password_in_cursor(cur, int(user.user_id), req.password):
            raise _credenciales_invalidas()
        result = await start_first_enrollment_after_password_in_cursor(
            cur, preauthenticated_user_id=int(user.user_id), config=config,
        )
        if isinstance(result, MFADenied): raise _mfa_denial(result)
    response.headers["Cache-Control"] = "no-store"
    return {"enrollment_token": result.enrollment_id, "secret": result.secret,
            "expires_at": result.expires_at.isoformat()}


@router.post("/mfa/enrollment/confirm")
async def mfa_enrollment_confirm(req: MFAEnrollmentConfirmRequest, response: Response):
    config = _mfa_config()
    if config is None: raise _mfa_unavailable()
    denial = None
    subject_user_id = None
    async with transaccion(AISLAMIENTO_ADMIN) as cur:
        result = await confirm_enrollment_in_cursor(
            cur, enrollment_capability_token=req.enrollment_token, otp=req.otp,
            config=config, server_correlation_id=uuid.uuid4().bytes,
        )
        if isinstance(result, MFADenied): denial = result
        else:
            await cur.execute("SELECT subject_user_id FROM office_mfa_security_event WHERE event_id=%s", (result.state_event_id,))
            subject_user_id = int((await cur.fetchone())[0])
    if denial is not None: raise _mfa_denial(denial)
    await _cortar_conexiones(subject_user_id)
    response.headers["Cache-Control"] = "no-store"
    return {"recovery_codes": list(result.recovery_codes), "token_version": result.token_version,
            "mfa_version": result.mfa_version}


@router.post("/mfa/step-up")
async def mfa_step_up(response: Response, user: AuthUser = Depends(get_current_user)):
    config = _mfa_config()
    if config is None: raise _mfa_unavailable()
    async with transaccion(AISLAMIENTO_ADMIN) as cur:
        result = await issue_step_up_challenge_in_cursor(cur, actor_user_id=int(user.user_id), config=config)
        if isinstance(result, MFADenied): raise _mfa_denial(result)
    response.headers["Cache-Control"] = "no-store"
    return {"challenge_token": result.token, "expires_at": result.expires_at.isoformat()}


@router.post("/mfa/step-up/verify", response_model=RefreshResponse)
async def verify_mfa_step_up(
    req: MFAStepUpVerifyRequest,
    response: Response,
    user: AuthUser = Depends(get_current_user),
    refresh_token: str = Cookie(None),
):
    """Replace transport MFA evidence after a same-subject STEP_UP proof.

    STEP_UP is intentionally version-neutral.  The refresh is re-signed only
    to carry the new durable proof and retains the exact iat/exp from the
    already-valid refresh cookie, so it cannot refresh the session lifetime.
    """
    config = _mfa_config()
    if config is None:
        raise _mfa_unavailable()
    if not refresh_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=SESION_EXPIRADA,
                            headers={"Cache-Control": "no-store"})
    refresh_payload = decode_token(refresh_token)
    refresh_user = await verificar_sesion(refresh_payload, "refresh")
    if refresh_user.user_id != user.user_id or refresh_user.token_version != user.token_version:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="sesion_invalida",
                            headers={"Cache-Control": "no-store"})
    issued_at, expires_at = refresh_payload.get("iat"), refresh_payload.get("exp")
    if (type(issued_at) is not int or type(expires_at) is not int
            or issued_at < 0 or expires_at <= issued_at or expires_at <= int(time.time())):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=SESION_EXPIRADA,
                            headers={"Cache-Control": "no-store"})

    denial: MFADenied | None = None
    async with transaccion(AISLAMIENTO_ADMIN) as cur:
        # This non-authorizing lookup stops a stolen capability for another
        # subject before the verifier can consume its one-time TOTP budget.
        await cur.execute(
            "SELECT user_id FROM office_mfa_challenge "
            "WHERE token_hash=UNHEX(SHA2(%s,256)) FOR UPDATE", (req.challenge_token,),
        )
        challenge_owner = await cur.fetchone()
        if challenge_owner is None or int(challenge_owner[0]) != int(user.user_id):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="challenge_invalid",
                                headers={"Cache-Control": "no-store"})
        receipt = await verify_and_consume_totp_in_cursor(
            cur, challenge_token=req.challenge_token, otp=req.otp,
            server_correlation_id=uuid.uuid4().bytes,
        )
        if isinstance(receipt, MFADenied):
            denial = receipt
        elif (receipt.purpose != "STEP_UP" or receipt.subject_user_id != int(user.user_id)
              or receipt.token_version != user.token_version):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="challenge_purpose",
                                headers={"Cache-Control": "no-store"})
    if denial is not None:
        raise _mfa_denial(denial)
    mfa_at = int(receipt.occurred_at.astimezone(timezone.utc).timestamp())
    access = create_access_token(
        user.user_id, user.tenant_id, user.role, user.token_version,
        mfa_proof_event_id=receipt.event_id, mfa_version=receipt.mfa_version, mfa_at=mfa_at,
    )
    refreshed = create_refresh_token_preserving_window(
        user.user_id, user.tenant_id, user.role, user.token_version,
        issued_at=issued_at, expires_at=expires_at,
        mfa_proof_event_id=receipt.event_id, mfa_version=receipt.mfa_version, mfa_at=mfa_at,
    )
    response.set_cookie(
        key="refresh_token", value=refreshed, httponly=True,
        max_age=max(0, expires_at - int(time.time())), samesite="lax",
    )
    response.headers["Cache-Control"] = "no-store"
    return RefreshResponse(access_token=access)


@router.post("/mfa/recover")
async def mfa_recover(req: MFARecoveryRequest, response: Response):
    config = _mfa_config()
    if config is None: raise _mfa_unavailable()
    denial = None
    subject_user_id = None
    async with transaccion(AISLAMIENTO_ADMIN) as cur:
        result = await consume_recovery_code_in_cursor(
            cur, recovery_code=req.recovery_code, config=config, server_correlation_id=uuid.uuid4().bytes,
        )
        if isinstance(result, MFADenied): denial = result
        else:
            # The recovery bearer is the sole subject selector.  Re-derive
            # its newly-created capability owner inside the same transaction;
            # do not accept a user id from the request merely to cut sockets.
            await cur.execute(
                "SELECT user_id FROM office_mfa_challenge "
                "WHERE token_hash=UNHEX(SHA2(%s,256)) FOR UPDATE", (result.token,),
            )
            row = await cur.fetchone()
            if row is None:
                raise RuntimeError("recovery capability missing after consume")
            subject_user_id = int(row[0])
    if denial is not None: raise _mfa_denial(denial)
    await _cortar_conexiones(subject_user_id)
    response.headers["Cache-Control"] = "no-store"
    return {"recovery_token": result.token, "expires_at": result.expires_at.isoformat()}


@router.post("/mfa/recovery/enrollment/start")
async def mfa_recovery_enrollment_start(req: MFARecoveryEnrollmentRequest, response: Response):
    config = _mfa_config()
    if config is None: raise _mfa_unavailable()
    denial = None
    async with transaccion(AISLAMIENTO_ADMIN) as cur:
        result = await start_recovery_enrollment_in_cursor(
            cur, recovery_capability_token=req.recovery_token, config=config,
        )
        if isinstance(result, MFADenied): denial = result
    if denial is not None: raise _mfa_denial(denial)
    response.headers["Cache-Control"] = "no-store"
    return {"enrollment_token": result.enrollment_id, "secret": result.secret,
            "expires_at": result.expires_at.isoformat()}


@router.post("/mfa/recovery-codes/regenerate")
async def mfa_regenerate_codes(req: MFAPasswordRequest, request: Request, response: Response, user: AuthUser = Depends(get_current_user)):
    _check_mfa_password_rate(request, user)
    config = _mfa_config()
    if config is None: raise _mfa_unavailable()
    if user.mfa_proof_event_id is None: raise HTTPException(status_code=403, detail="office_mfa_required")
    async with transaccion(AISLAMIENTO_ADMIN) as cur:
        if not await _verify_current_password_in_cursor(cur, int(user.user_id), req.password): raise _credenciales_invalidas()
        result = await regenerate_recovery_codes_after_password_in_cursor(
            cur, actor_user_id=int(user.user_id), proof_event_id=user.mfa_proof_event_id,
            config=config, server_correlation_id=uuid.uuid4().bytes,
        )
        if isinstance(result, MFADenied): raise _mfa_denial(result)
    await _cortar_conexiones(int(user.user_id))
    response.headers["Cache-Control"] = "no-store"
    return {"recovery_codes": list(result.recovery_codes), "event_id": result.event_id,
            "token_version": result.token_version, "mfa_version": result.mfa_version}


@router.post("/mfa/disable")
async def mfa_disable(req: MFAPasswordRequest, request: Request, response: Response, user: AuthUser = Depends(get_current_user)):
    _check_mfa_password_rate(request, user)
    config = _mfa_config()
    if config is None: raise _mfa_unavailable()
    if user.mfa_proof_event_id is None: raise HTTPException(status_code=403, detail="office_mfa_required")
    async with transaccion(AISLAMIENTO_ADMIN) as cur:
        if not await _verify_current_password_in_cursor(cur, int(user.user_id), req.password): raise _credenciales_invalidas()
        result = await disable_mfa_after_password_in_cursor(
            cur, actor_user_id=int(user.user_id), proof_event_id=user.mfa_proof_event_id,
            config=config, server_correlation_id=uuid.uuid4().bytes,
        )
        if isinstance(result, MFADenied): raise _mfa_denial(result)
    await _cortar_conexiones(int(user.user_id))
    response.headers["Cache-Control"] = "no-store"
    return {"event_id": result.event_id, "token_version": result.token_version, "mfa_version": result.mfa_version}


@router.post("/mfa/root-reset")
async def mfa_root_reset(req: MFARootResetRequest, response: Response, user: AuthUser = Depends(get_current_user)):
    """Dedicated Office-root reset; body selects only the target."""
    config = _mfa_config()
    if config is None:
        raise _mfa_unavailable()
    if user.mfa_proof_event_id is None:
        raise HTTPException(status_code=403, detail="office_mfa_required")
    async with transaccion(AISLAMIENTO_ADMIN) as cur:
        if not await lock_office_identity_pair_in_cursor(
                cur, int(user.user_id), req.target_user_id):
            raise HTTPException(status_code=403, detail="mfa_reset_denied")
        decision = await authorize_office_in_cursor(
            cur, int(user.user_id), OfficeOperation.ADMINISTER_OFFICE_IDENTITY_SECURITY,
            require_recent_mfa=True, mfa_proof_event_id=user.mfa_proof_event_id,
            mfa_config=config,
        )
        if not decision.allowed:
            raise HTTPException(status_code=403, detail="office_identity_security_required")
        authorization_event_id = await record_authorization_event(
            cur, decision, correlation_id=str(uuid.uuid4()),
        )
        result = await reset_mfa_by_office_root_in_cursor(
            cur, actor_user_id=int(user.user_id), target_user_id=req.target_user_id,
            proof_event_id=user.mfa_proof_event_id,
            authorization_event_id=authorization_event_id, config=config,
            server_correlation_id=uuid.uuid4().bytes,
        )
        if isinstance(result, MFADenied):
            raise HTTPException(status_code=403, detail=result.code)
    await _cortar_conexiones(result.subject_user_id)
    response.headers["Cache-Control"] = "no-store"
    return {"event_id": result.event_id, "target_user_id": result.subject_user_id,
            "token_version": result.token_version, "mfa_version": result.mfa_version}


@router.post("/refresh", response_model=RefreshResponse)
async def refresh(refresh_token: str = Cookie(None)):
    if not refresh_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sin refresh token")
    # La misma verificación que cada request (etapa 2): un refresh de un
    # usuario desactivado o con la versión vieja ya no reemite nada. El access
    # nuevo lleva el rol y la versión de la BASE, no los del refresh.
    # La cookie NO se rota: rotarla haría deslizante la sesión, y la versión
    # ya invalida el refresh viejo cuando hace falta.
    # U34: la renovación se admite con la marca; sin ella, el access de 15 min
    # vence en medio del cambio obligatorio. El access nuevo no lleva la marca:
    # la lee verificar_sesion de la base en cada request, así que se vuelve a
    # hacer cumplir en el siguiente.
    payload = decode_token(refresh_token)
    user = await verificar_sesion(payload, "refresh", admite_cambio_pendiente=True)
    # Frente C: la vida se mide contra el ajuste VIGENTE, no contra el exp con
    # que se emitió -- acortar session_timeout_min alcanza a esta sesión.
    if sesion_vencida(payload, await vida_de_sesion_segundos(), time.time()):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=SESION_EXPIRADA)
    access = create_access_token(
        user.user_id, user.tenant_id, user.role, user.token_version,
        mfa_proof_event_id=user.mfa_proof_event_id,
        mfa_version=user.mfa_version if user.mfa_proof_event_id is not None else None,
        mfa_at=user.mfa_at if user.mfa_proof_event_id is not None else None,
    )
    return RefreshResponse(access_token=access)


@router.get("/me", response_model=MeResponse)
async def me(user: AuthUser = Depends(get_current_user_con_cambio_pendiente)):
    # get_current_user_con_cambio_pendiente ya pasó por verificar_sesion (una SELECT por PK que
    # trae status/role/token_version/email): no hace falta un segundo SELECT
    # acá. Si el usuario no existiera, verificar_sesion ya habría cortado con
    # 401 (fail-closed) antes de llegar a este punto (code review, M-3).
    return MeResponse(
        user_id=int(user.user_id),
        tenant_id=int(user.tenant_id),
        role=user.role,
        email=user.email,
        must_change_password=user.must_change_password,
        account_class=user.account_class,
    )


@router.post("/logout")
async def logout(response: Response, refresh_token: str = Cookie(None)):
    """Sesión única (2026-09-15, Task 3b, Ruling F2): salir mata la sesión EN
    EL SERVIDOR, no sólo la cookie del navegador. La sesión se identifica por
    la cookie de refresh (como /refresh) y se mata subiendo token_version; el
    access de 15 min y cualquier copia del refresh quedan con la versión vieja.

    Nunca responde 401 (el frontend no puede quedar en un bucle al salir): sin
    cookie, con un token inválido o vencido, con la versión vieja o con un
    usuario inactivo o dado de baja no hay sesión viva que matar -> sólo se
    borra la cookie, sin escribir. Sin fila de auditoría: salir es una acción
    rutinaria del propio usuario.

    Admite la marca de cambio obligatorio (U34): quien la tiene también tiene
    que poder salir. Es el tercer llamador con el opt-in, declarado en
    tests/test_fijar_password.py::test_nadie_mas_admite_la_marca."""
    if refresh_token:
        try:
            # decode_token convierte el JWTError en HTTPException(401) y
            # verificar_sesion rechaza con HTTPException: los dos significan
            # "no hay sesión viva". Sólo eso se atrapa; un error de la base en
            # la escritura de abajo NO (sería informar un éxito que no ocurrió).
            user = await verificar_sesion(decode_token(refresh_token), "refresh", admite_cambio_pendiente=True)
        except HTTPException:
            user = None
        if user is not None:
            user_id = int(user.user_id)
            # Fix ronda 1 (2026-09-15, F2): sólo mata SU versión. Si entre la
            # verificación y este UPDATE confirmó el login de otro dispositivo,
            # esta sesión ya estaba muerta: 0 filas, sin corte (cortar echaría
            # a la sesión nueva), y la respuesta es la misma.
            async with transaccion(AISLAMIENTO_ADMIN) as cur:
                await cur.execute(
                    "UPDATE jax_users SET token_version = token_version + 1 "
                    "WHERE user_id = %s AND token_version = %s",
                    (user_id, user.token_version),
                )
                matada = cur.rowcount == 1
            # Ruling U9: después del commit, fail-soft (las otras pestañas de
            # este navegador comparten la cookie: salen también, a propósito).
            if matada:
                await _cortar_conexiones(user_id)
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
    user: AuthUser = Depends(get_current_user_con_cambio_pendiente),
):
    """Mi cuenta (2026-09-15, admin usuarios etapa 4, spec §3.2 y §3.4). Exige
    la contraseña actual, con el mismo límite de intentos que el login (sin él,
    un token robado sirve para adivinar la contraseña a velocidad de CPU).
    Sube token_version: se cierran las OTRAS sesiones, y esta recibe tokens
    nuevos (access en la respuesta, refresh en la cookie)."""
    vida = await vida_de_sesion_segundos()  # antes de la transacción que sube la versión (frente C)
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
    # Cambio obligatorio (U34, P1): con la marca, la nueva no puede ser la que
    # fijó el admin -- si no, el cambio no cambia nada. Comparación de strings:
    # la actual ya se verificó contra el hash.
    if user.must_change_password and req.new_password == req.current_password:
        raise HTTPException(status_code=400, detail="password_igual_a_la_actual")
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
    # Fix ronda 1 (2026-09-15): se relee también la sesión. Si en la ventana
    # de bcrypt el admin revocó las sesiones (token_version), desactivó la
    # cuenta o la borró, esta sesión ya no vale: 401 sin escribir, sin
    # auditar, sin cortar y sin tokens -- si no, la revocación quedaba
    # deshecha por los tokens nuevos de tv+1.
    async with transaccion() as cur:
        await cur.execute(
            "SELECT password_hash, token_version, status FROM jax_users WHERE user_id = %s FOR UPDATE",
            (user_id,),
        )
        vigente = await cur.fetchone()
        if vigente is None or vigente[1] != user.token_version or vigente[2] != "active":
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="sesion_invalida")
        if vigente[0] != hash_verificado:
            raise actual_incorrecta
        # Ruling U16: una cuenta bloqueada pero activa, con sesión viva, que
        # prueba la actual puede cambiarla, y eso limpia el bloqueo como un
        # login exitoso (el límite del login ya se aplicó arriba).
        await cur.execute(
            "UPDATE jax_users SET password_hash = %s, token_version = token_version + 1, "
            "failed_attempts = 0, locked_until = NULL, must_change_password = FALSE WHERE user_id = %s",
            (nuevo_hash, user_id),
        )
        await cur.execute("SELECT token_version FROM jax_users WHERE user_id = %s", (user_id,))
        (nueva_version,) = await cur.fetchone()
        await user_audit.registrar(cur, user_id, user_id, "password_changed_self", None, ip)
    # Ruling U9 (spec §3.2): después del commit, fail-soft. Se corta también
    # la pestaña que hizo el cambio: reconecta sola con el token nuevo.
    await _cortar_conexiones(user_id)
    access = _emitir_tokens(response, user.user_id, user.tenant_id, user.role, nueva_version, vida)
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


async def _crear_enlace_de_recuperacion(cur, user_id: int, client_ip: str) -> tuple[str, str]:
    """Invalida los tokens pendientes del usuario, crea uno nuevo (1 hora) y
    devuelve (token, enlace). Lo comparten el forgot-password público
    (_procesar_recuperacion) y el reset por admin (etapa 4,
    api/admin/users.py::send_reset_link). Devolver también el token (fix
    ronda 1, 2026-09-15, U17): si el envío falla, quien llama borra ESE token
    por valor exacto -- no por user_id, que también borraría un token de un
    forgot-password concurrente del mismo usuario.

    Ruling U31 (etapa 5, Task 3 fix ronda 1, 2026-09-15): corre en el cursor
    de QUIEN LLAMA, dentro de su transacción y con la fila del usuario YA
    bloqueada por PK (FOR UPDATE). No abre conexión propia. Antes abría otra
    conexión en autocommit: entre la lectura del estado y este INSERT, una
    baja podía confirmar y el token quedaba vivo para un dado de baja. Con la
    fila bloqueada, la baja espera y, al entrar, borra este token pendiente.
    Orden usuario -> token: el mismo de la baja y de /reset-password (U21)."""
    token = str(uuid.uuid4())
    expires_at = utc_ahora() + timedelta(hours=1)
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
    return token, f"{frontend_origin}/reset-password?token={token}"


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

        # U31 (etapa 5, Task 3 fix ronda 1, 2026-09-15): la búsqueda de arriba
        # no bloquea, y una baja pudo confirmar desde entonces (por ejemplo,
        # mientras se cargaban los ajustes SMTP). Se RE-BLOQUEA la fila POR PK
        # y se crea el token en la MISMA transacción; el correo sale después
        # del commit, al email releído bajo el bloqueo. Si ya no está activa,
        # se termina en silencio (la respuesta pública ya salió, neutra).
        #
        # Por PK y NUNCA por el índice de email: la baja bloquea la PK y
        # DESPUÉS reescribe la entrada de email en el índice secundario (el
        # renombre del correo). Un FOR UPDATE por email tomaría primero esa
        # entrada y luego esperaría la PK -- orden opuesto al de la baja ->
        # ciclo -> InnoDB 1213.
        #
        # READ COMMITTED (Ruling U33, fix ronda 2, 2026-09-15), NO el
        # REPEATABLE READ por defecto. El bloqueo por PK es de registro en los
        # dos niveles, pero el DELETE de _crear_enlace_de_recuperacion va por
        # el índice NO único de la FK user_id: en REPEATABLE READ toma
        # bloqueos de hueco, y el INSERT que sigue pide un insert-intention en
        # ese mismo hueco. Dos forgot-password de usuarios DISTINTOS que
        # comparten hueco (p. ej. los dos más nuevos que todo token existente)
        # hacían DELETE, DELETE, INSERT, INSERT -> 1213, que el except
        # fail-soft de abajo tragaba: uno de los dos se quedaba sin correo
        # (medido: test_dos_forgot_password_de_usuarios_distintos_...). En
        # READ COMMITTED no hay bloqueos de hueco. El reset por admin
        # (send_reset_link) ya corría en este mismo nivel.
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            await cur.execute(
                "SELECT email FROM jax_users WHERE user_id = %s AND status = 'active' FOR UPDATE",
                (user_id,),
            )
            vigente = await cur.fetchone()
            if vigente is None:
                return
            (email_guardado,) = vigente
            _token, reset_link = await _crear_enlace_de_recuperacion(cur, user_id, client_ip)
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


# El token por su índice único y el usuario por PRIMARY (eq_ref): una sola
# consulta trae lo necesario para P1 (Ruling F5). EXPLAIN en
# tests/test_fijar_password.py::test_la_consulta_del_token_de_recuperacion_va_por_indices.
SQL_TOKEN_DE_RECUPERACION = (
    "SELECT t.id, t.user_id, t.expires_at, t.used, u.password_hash, u.must_change_password "
    "FROM password_reset_tokens t JOIN jax_users u ON u.user_id = t.user_id WHERE t.token = %s"
)


@router.post("/reset-password")
async def reset_password(req: ResetPasswordRequest, request: Request):
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
            await cur.execute(SQL_TOKEN_DE_RECUPERACION, (req.token,))
            row = await cur.fetchone()

    if not row:
        raise HTTPException(status_code=400, detail="reset_token_invalido")

    token_id, user_id, expires_at, used, hash_leido, marca_leida = row

    if used:
        raise HTTPException(status_code=400, detail="reset_token_usado")

    if expires_at < utc_ahora():
        raise HTTPException(status_code=400, detail="reset_token_expirado")

    # P1 también por el enlace (Ruling F5, 2026-09-15): mientras el cambio sea
    # obligatorio, la nueva no puede ser la vigente (la que fijó el admin) --
    # si no, completar un enlace creado DESPUÉS de fijarla esquivaba P1. Va
    # después de los chequeos del token (un token inválido no cuesta bcrypt)
    # y NO escribe nada: el token queda sin consumir para reintentar con otra.
    # Sin la marca, se acepta la misma (como Mi cuenta).
    if marca_leida and await verify_password(req.password, hash_leido):
        raise HTTPException(status_code=400, detail="password_igual_a_la_actual")

    # bcrypt de costo 12 (~150 ms de CPU): en un hilo, no en el event loop.
    new_hash = await asyncio.to_thread(_hash, req.password)
    ip = rate_limit.client_ip(request, rate_limit.TRUSTED_PROXIES)

    async with transaccion() as cur:
        # Orden de bloqueo (Ruling U21, fix ronda 1 2026-09-15): el USUARIO se
        # bloquea PRIMERO, antes que el token. `password_reset_tokens.user_id`
        # tiene FK ON DELETE CASCADE hacia jax_users: cuando se borraba al
        # usuario (el viejo delete_user; desde la etapa 5 la baja hace lo mismo
        # a mano: bloquea la fila y DESPUÉS borra sus tokens pendientes),
        # InnoDB tomaba la fila de jax_users y DESDE AHÍ cascadaba a sus
        # tokens -- en ese orden (usuario, después token). Si esta
        # transacción tomara el token primero y el usuario después, un DELETE
        # concurrente que ya tiene al usuario y espera el token forma un ciclo
        # con esta (que tendría el token y esperaría al usuario) -> 1213. Con
        # el usuario primero en las dos, el orden es el mismo y no hay ciclo.
        # También cierra el hueco de un reset a un usuario borrado/desactivado
        # DESPUÉS de crear el token: sin esto, el UPDATE de abajo escribiría
        # sobre una fila que ya no debería aceptar contraseñas nuevas.
        await cur.execute(
            "SELECT status, password_hash, must_change_password FROM jax_users WHERE user_id = %s FOR UPDATE",
            (user_id,),
        )
        fila_usuario = await cur.fetchone()
        if fila_usuario is None or fila_usuario[0] != "active":
            # Mismo código que un token que nunca existió: no se distingue
            # "usuario borrado/inactivo" de "token inválido" en la respuesta.
            raise HTTPException(status_code=400, detail="reset_token_invalido")
        # Defensa en profundidad de P1 (F5): si con la marca puesta la
        # contraseña cambió desde la lectura de arriba (o la marca se prendió
        # después), la verificación de P1 se hizo contra otro hash. Se revierte
        # ANTES de reclamar el token, comparando strings (sin bcrypt bajo el
        # bloqueo, como Mi cuenta). Hoy el único que prende la marca (fijar)
        # borra los enlaces pendientes, así que este token ya estaría muerto:
        # mismo código que un token inválido.
        if fila_usuario[2] and fila_usuario[1] != hash_leido:
            raise HTTPException(status_code=400, detail="reset_token_invalido")
        # Reclamar el token es lo SEGUNDO y es atómico: con dos envíos
        # simultáneos del mismo enlace, solo uno cambia la fila (el otro ve
        # 0 filas y revierte). Un UPDATE que cambia FALSE -> TRUE siempre
        # reporta 1 fila afectada; si el WHERE lo excluye, 0.
        await cur.execute(
            "UPDATE password_reset_tokens SET used = TRUE WHERE id = %s AND used = FALSE",
            (token_id,),
        )
        if cur.rowcount != 1:
            raise HTTPException(status_code=400, detail="reset_token_usado")
        # Contraseña nueva por enlace: todas las sesiones viejas se cortan (spec §3.2).
        # La persona eligió su propia contraseña por el enlace: la marca del
        # cambio obligatorio (U34) queda cumplida y se limpia.
        await cur.execute(
            "UPDATE jax_users SET password_hash = %s, failed_attempts = 0, locked_until = NULL, "
            "token_version = token_version + 1, must_change_password = FALSE WHERE user_id = %s",
            (new_hash, user_id),
        )
        await user_audit.registrar(cur, user_id, user_id, "password_reset_completed", None, ip)

    # Ruling U9 (spec §3.2): después del commit, fail-soft. Nunca dentro de la
    # transacción ni en los caminos de error de arriba (token inválido, usado
    # o expirado no corta nada).
    await _cortar_conexiones(user_id)
    return {"ok": True, "message": "Contraseña actualizada correctamente"}
