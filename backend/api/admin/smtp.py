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
from crypto_secrets import clave_de_cifrado_utilizable
from auth.middleware import require_superadmin
from auth.models import AuthUser
from auth.rate_limit import SlidingWindowLimiter, parse_rate
from db.connection import get_pool
from validacion import EMAIL_MAX, direccion_unica_valida, tiene_caracteres_de_control

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin")

# El correo de prueba sale de verdad: sin límite, un superadmin (o un token
# robado) podría usar el servidor para inundar un buzón. 5 cada 5 minutos por
# usuario alcanza para probar, corregir y volver a probar.
SMTP_TEST_LIMITER = SlidingWindowLimiter(*parse_rate(os.getenv("JAX_SMTP_TEST_RATE", "5/300")))
# Probar conexión no envía nada, pero cada llamada ocupa un hilo del executor
# hasta TIMEOUT_S y devuelve el banner del host pedido (sirve para sondear la
# red interna). 10 cada 5 minutos por usuario (revisión final, 2026-09-13).
SMTP_CONN_LIMITER = SlidingWindowLimiter(*parse_rate(os.getenv("JAX_SMTP_CONN_RATE", "10/300")))

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
    # Destinatario por defecto del correo de prueba (2026-09-13). Ausente en
    # el body = no se toca la fila (clientes viejos); null o "" la vacían.
    # Sin max_length: el tope de 254 lo aplica normalizar_destinatario y sale
    # como smtp_destinatario_invalido, no como un 422 sin traducir (revisión).
    test_to: Optional[str] = None


class SmtpPrueba(BaseModel):
    # Sin tope propio: uno demasiado largo no pasa la validación y sale como
    # smtp_destinatario_invalido, el mismo código que cualquier otro inválido.
    to: Optional[str] = None


@router.get("/smtp")
async def ver_smtp(user: AuthUser = Depends(require_superadmin)):
    # email_sesion: para prellenar el destinatario de la prueba sin depender
    # del store de auth del frontend.
    return {**smtp_config.estado_para_pantalla(await smtp_config.leer_filas()),
            "email_sesion": await _email_de(int(user.user_id))}


def _limitar(limitador: SlidingWindowLimiter, user: AuthUser) -> None:
    retry = limitador.hit(str(user.user_id))
    if retry is not None:
        raise HTTPException(status_code=429, detail="smtp_demasiadas_pruebas",
                            headers={"Retry-After": str(max(1, math.ceil(retry)))})


def _validar_entrada(req: SmtpConexion) -> None:
    """Lo que Pydantic no ve. Un salto de línea en host/usuario/remitente
    inyecta una línea SMTP o un encabezado (y un remitente así rompía TODOS
    los correos de recuperación); smtplib codifica el AUTH (usuario y
    contraseña) en ascii, así que uno no ASCII terminaba en 500."""
    campos = [req.host, req.user]
    if isinstance(req, SmtpUpdate):
        campos += [req.from_name, req.from_email]
    if any(tiene_caracteres_de_control(c) for c in campos):
        raise HTTPException(status_code=400, detail="smtp_campo_invalido")
    if not req.user.isascii():
        # El AUTH lleva el usuario, también codificado en ascii por smtplib.
        raise HTTPException(status_code=422, detail="smtp_usuario_no_ascii")
    if smtp_config.trae_contrasena_nueva(req.model_dump()) and not req.password.isascii():
        raise HTTPException(status_code=422, detail="smtp_password_no_ascii")


@router.put("/smtp")
async def guardar_smtp(req: SmtpUpdate, user: AuthUser = Depends(require_superadmin)):
    # Antes de leer o cifrar nada: sin FERNET_KEY (o con una malformada) no se
    # puede ni verificar la guardada ni cifrar una nueva -- nunca en claro.
    if not clave_de_cifrado_utilizable():
        raise HTTPException(status_code=503, detail="smtp_sin_clave_de_cifrado")
    _validar_entrada(req)
    # Una sola dirección: el remitente va al encabezado From (revisión).
    if not direccion_unica_valida(req.from_email):
        raise HTTPException(status_code=400, detail="smtp_from_email_invalido")
    datos = req.model_dump()
    if "test_to" not in req.model_fields_set:
        del datos["test_to"]  # ausente = no se toca smtp.test_to
    actuales = await smtp_config.leer_filas()
    motivo_previo = smtp_config.motivo_de_corrupcion(actuales)
    try:
        filas = smtp_config.filas_a_guardar(actuales, datos)
    except smtp_config.SmtpDestinatarioInvalido as exc:
        raise HTTPException(status_code=400, detail=exc.codigo) from exc
    except (smtp_config.SmtpExigeContrasena, smtp_config.SmtpReescribirContrasena) as exc:
        # 422 y no 500: quien opera lo resuelve volviendo a escribir la contraseña.
        raise HTTPException(status_code=422, detail=exc.codigo) from exc
    await smtp_config.guardar_filas(filas)
    if motivo_previo is not None:
        logger.warning("SMTP reconfigurado sobre un estado corrupto (motivo anterior: %s) por user_id=%s",
                       motivo_previo, user.user_id)
    return {"ok": True}


# Motivo de SmtpExigeContrasena -> código de la prueba de conexión.
_SIN_CONTRASENA_PARA_PROBAR = {"sin_contrasena": "smtp_sin_contrasena", "password_ilegible": "smtp_password_ilegible"}


@router.post("/smtp/test-connection")
async def probar_conexion_smtp(req: SmtpConexion, user: AuthUser = Depends(require_superadmin)):
    _limitar(SMTP_CONN_LIMITER, user)
    _validar_entrada(req)
    password = req.password or ""
    if not smtp_config.trae_contrasena_nueva(req.model_dump()):
        try:
            password = smtp_config.contrasena_para_reusar(await smtp_config.leer_filas(), req.model_dump())
        except smtp_config.SmtpExigeContrasena as exc:
            raise HTTPException(status_code=422, detail=_SIN_CONTRASENA_PARA_PROBAR[exc.motivo]) from exc
        except smtp_config.SmtpReescribirContrasena as exc:
            raise HTTPException(status_code=422, detail=exc.codigo) from exc
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
async def enviar_prueba_smtp(req: Optional[SmtpPrueba] = None,
                             user: AuthUser = Depends(require_superadmin)):
    # El límite va PRIMERO: ni un destino inválido se valida con el cupo agotado.
    _limitar(SMTP_TEST_LIMITER, user)
    filas = await smtp_config.leer_filas()
    try:
        settings = smtp_config.interpretar(filas)
    except smtp_config.SmtpNoDisponible as exc:
        raise HTTPException(status_code=503, detail=exc.codigo) from exc
    # Destino: el pedido, si no smtp.test_to guardado, si no el de la sesión
    # (la regla vive en smtp_config.destinatario_de_prueba).
    try:
        elegido = smtp_config.destinatario_de_prueba(req.to if req else None, filas)
    except smtp_config.SmtpDestinatarioInvalido as exc:
        raise HTTPException(status_code=400, detail=exc.codigo) from exc
    destinatario = elegido or await _email_de(int(user.user_id))
    try:
        mensaje = smtp_config.construir_mensaje(settings, destinatario, ASUNTO_PRUEBA, TEXTO_PRUEBA, HTML_PRUEBA)
    except ValueError as exc:
        # EmailMessage rechaza encabezados con caracteres de control. Las
        # filas nuevas no los pueden traer (_validar_entrada) y las viejas ya
        # son estado corrupto (motivo_de_corrupcion): esto es la última red.
        logger.warning("Correo de prueba SMTP: no se pudo armar el mensaje: %s", exc)
        raise HTTPException(status_code=503, detail="smtp_config_corrupta") from exc
    try:
        # La prueba se ESPERA a propósito (quien la pide quiere el veredicto),
        # pero en un hilo: smtplib no toca el event loop.
        await asyncio.to_thread(smtp_config.enviar, settings, mensaje)
    except UnicodeEncodeError as exc:
        # smtplib codifica el AUTH en ascii: una contraseña guardada no ASCII
        # (fila anterior a la validación) daba 500. Mismo formato que el 502
        # de abajo, sin respuesta del servidor porque no la hubo.
        raise HTTPException(status_code=502, detail={"code": "smtp_password_no_ascii", "server": ""}) from exc
    except (OSError, smtplib.SMTPException) as exc:
        logger.warning("Correo de prueba SMTP a %s falló: %s", destinatario, exc)
        raise HTTPException(status_code=502, detail={"code": "smtp_envio_fallido", "server": str(exc)}) from exc
    logger.info("Correo de prueba SMTP enviado a %s por user_id=%s", destinatario, user.user_id)
    return {"ok": True, "to": destinatario}
