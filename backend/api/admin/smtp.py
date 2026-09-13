"""Pantalla "Correo (SMTP)" de Admin (2026-09-12, etapa 1). Copia de
SmtpController de AteneaERP (show/update/testConnection/test) sobre
smtp_config. Solo superadmin."""
import asyncio
import logging
import math
import os
import smtplib
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import smtp_config
from auth.middleware import require_superadmin
from auth.models import AuthUser
from auth.rate_limit import SlidingWindowLimiter, parse_rate
from db.connection import get_pool
from validacion import EMAIL_MAX, email_valido

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin")

# El correo de prueba sale de verdad: sin límite, un superadmin (o un token
# robado) podría usar el servidor para inundar un buzón. 5 cada 5 minutos por
# usuario alcanza para probar, corregir y volver a probar.
SMTP_TEST_LIMITER = SlidingWindowLimiter(*parse_rate(os.getenv("JAX_SMTP_TEST_RATE", "5/300")))

ASUNTO_PRUEBA = "Axioma — Prueba de SMTP"
TEXTO_PRUEBA = (
    "Este es un correo de prueba enviado desde Axioma para verificar que la "
    "configuración SMTP funciona.\n\nSi lo recibiste, el correo saliente está bien configurado."
)
HTML_PRUEBA = (
    "<p>Este es un <strong>correo de prueba</strong> enviado desde <strong>Axioma</strong> "
    "para verificar que la configuración SMTP funciona.</p>"
    "<p>Si lo recibiste, el correo saliente está bien configurado.</p>"
)


class SmtpConexion(BaseModel):
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(ge=1, le=65535)
    encryption: Literal["tls", "ssl", "none"]
    user: str = Field(min_length=1, max_length=255)
    password: Optional[str] = Field(default=None, max_length=255)


class SmtpUpdate(SmtpConexion):
    from_name: str = Field(min_length=1, max_length=255)
    from_email: str = Field(min_length=3, max_length=EMAIL_MAX)


@router.get("/smtp")
async def ver_smtp(user: AuthUser = Depends(require_superadmin)):
    return smtp_config.estado_para_pantalla(await smtp_config.leer_filas())


@router.put("/smtp")
async def guardar_smtp(req: SmtpUpdate, user: AuthUser = Depends(require_superadmin)):
    if not email_valido(req.from_email):
        raise HTTPException(status_code=400, detail="smtp_from_email_invalido")
    actuales = await smtp_config.leer_filas()
    motivo_previo = smtp_config.motivo_de_corrupcion(actuales)
    try:
        filas = smtp_config.filas_a_guardar(actuales, req.model_dump())
    except smtp_config.SmtpExigeContrasena as exc:
        # 422 y no 500: quien opera lo resuelve volviendo a escribir la contraseña.
        raise HTTPException(status_code=422, detail=exc.codigo) from exc
    except RuntimeError as exc:
        # encrypt_secret sin FERNET_KEY: nunca se guarda en claro.
        raise HTTPException(status_code=503, detail="smtp_sin_clave_de_cifrado") from exc
    await smtp_config.guardar_filas(filas)
    if motivo_previo is not None:
        logger.warning("SMTP reconfigurado sobre un estado corrupto (motivo anterior: %s) por user_id=%s",
                       motivo_previo, user.user_id)
    return {"ok": True}


@router.post("/smtp/test-connection")
async def probar_conexion_smtp(req: SmtpConexion, user: AuthUser = Depends(require_superadmin)):
    password = req.password or ""
    if password in ("", smtp_config.MASCARA):
        guardada = (await smtp_config.leer_filas()).get(smtp_config.CLAVE_SECRETA, "")
        password = smtp_config.decrypt_db_secret(guardada) if guardada else ""
        if guardada and not password:
            raise HTTPException(status_code=422, detail="smtp_password_ilegible")
        if not password:
            raise HTTPException(status_code=422, detail="smtp_sin_contrasena")
    try:
        await asyncio.to_thread(smtp_config.probar_conexion, req.host, req.port, req.encryption, req.user, password)
    except smtp_config.SmtpPasoFallido as exc:
        raise HTTPException(status_code=422, detail={"code": exc.codigo, "server": exc.servidor}) from exc
    return {"ok": True}


async def _email_de(user_id: int) -> str:
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT email FROM jax_users WHERE user_id = %s", (user_id,))
            fila = await cur.fetchone()
    if fila is None:
        raise HTTPException(status_code=401, detail="sesion_invalida")
    return fila[0]


@router.post("/smtp/test")
async def enviar_prueba_smtp(user: AuthUser = Depends(require_superadmin)):
    retry = SMTP_TEST_LIMITER.hit(str(user.user_id))
    if retry is not None:
        raise HTTPException(status_code=429, detail="smtp_demasiadas_pruebas",
                            headers={"Retry-After": str(max(1, math.ceil(retry)))})
    try:
        settings = await smtp_config.cargar_settings()
    except smtp_config.SmtpNoDisponible as exc:
        raise HTTPException(status_code=503, detail=exc.codigo) from exc
    destinatario = await _email_de(int(user.user_id))
    mensaje = smtp_config.construir_mensaje(settings, destinatario, ASUNTO_PRUEBA, TEXTO_PRUEBA, HTML_PRUEBA)
    try:
        # La prueba se ESPERA a propósito (quien la pide quiere el veredicto),
        # pero en un hilo: smtplib no toca el event loop.
        await asyncio.to_thread(smtp_config.enviar, settings, mensaje)
    except (OSError, smtplib.SMTPException) as exc:
        logger.warning("Correo de prueba SMTP a %s falló: %s", destinatario, exc)
        raise HTTPException(status_code=502, detail={"code": "smtp_envio_fallido", "server": str(exc)}) from exc
    logger.info("Correo de prueba SMTP enviado a %s por user_id=%s", destinatario, user.user_id)
    return {"ok": True, "to": destinatario}
