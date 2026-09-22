"""Aviso por correo cuando un pipeline termina (Task 8, 2026-09-18).

Fernando lanza un pipeline, se va, y hoy no se entera cuando termina: el
poller detecta la transición (`jax_engine/state.py::_poll_one_pipeline`,
rama `updated.status in ("completed", "failed")`) pero no avisa a nadie fuera
de la sesión WS/SSE abierta. Esto es el correo. El de Telegram vive en el
otro repo (jax), que es el que tiene ese canal -- no se duplica acá.

Copia el patrón que ya funciona en recuperación de contraseña
(`api/auth.py::_procesar_recuperacion` / `_send_reset_email`): SMTP real en
`asyncio.to_thread`, porque `smtplib` es bloqueante y no puede correr en el
event loop.

DIFERENCIA con auth.py, y por qué: `_procesar_recuperacion` corre encolada
por `BackgroundTasks` de FastAPI, disponible porque el llamador es un
endpoint dentro de un request. `_poll_one_pipeline` NO es un request -- es un
tick del poller en segundo plano (`_poll_pipelines`, cada 5 s, para TODAS las
pipelines activas del proceso). No hay `BackgroundTasks` a mano y no se puede
fabricar uno: en vez de eso, `encolar_aviso_fin_pipeline` dispara un
`asyncio.create_task` propio y SUELTA el control de inmediato -- el poller
jamás espera a que el correo salga. Un SMTP lento (o caído) nunca frena el
tick que sigue ni las demás pipelines del mismo ciclo.

GARANTÍA DE UNA SOLA VEZ: la transición completed/failed puede observarse más
de una vez para el MISMO pipeline (un reinicio del servicio que reengancha
pipelines activas, un `/continue` que la hace terminar de nuevo, dos ticks
que corren solapados). La garantía es la tabla `pipeline_aviso_enviado`
(migrations.py): `_reclamar()` hace un INSERT IGNORE por `pipeline_id`
ANTES de resolver el destinatario o tocar SMTP -- gana el primero, y es un
INSERT atómico de MariaDB, no un lock en memoria de un solo proceso. Es
"a lo sumo una vez", a propósito y no "al menos una vez": si el reclamo
gana pero el envío después falla (SMTP caído, por ejemplo), NO se reintenta
-- se loguea y ahí queda, igual que `_procesar_recuperacion` no reintenta un
envío fallido. Construir una cola durable de reintento es un problema más
grande que esta tarea (ver `uso/reintento.py`, que sí lo resuelve para otra
cosa) y el pipeline ya salió de `active_pipelines`: no hay otro disparador
que vuelva a ofrecer la oportunidad.

FAIL-SOFT CON RASTRO, no silencioso: un aviso que no se puede mandar (SMTP
deshabilitado, el dueño no tiene correo activo, el envío lanza) NUNCA toca el
estado del pipeline -- ya terminó, eso no se revierte -- pero SIEMPRE deja un
`logger.error`/`logger.exception` con pipeline_id, tenant_id y user_id, para
que se pueda seguir el rastro.

SIN i18n DE TEXTO: el asunto y el cuerpo van hardcodeados en español, igual
que `ASUNTO_RECUPERACION` en auth.py. Verificado 2026-09-18 contra el
código: el backend no tiene NINGÚN mecanismo de i18n para texto (grep de
"i18n|gettext|locale" en backend/ no encuentra nada relevante; i18n vive
SOLO en el frontend, `frontend/src/i18n/{es,en}.js`, que no llega a un correo
armado en Python). Se sigue el mismo camino que el correo de recuperación de
contraseña, tal como pide el brief -- la deuda de fondo (backend sin i18n de
texto) es preexistente y no se abre ni se cierra en esta tarea.

SIN URL HARDCODEADA: el origen sale de `FRONTEND_ORIGIN` (la misma variable
que ya usa auth.py para el enlace de recuperación) y la ruta del detalle de
`PIPELINE_DETAIL_PATH`, las dos de entorno. La sección Historial que va a
mostrar ese detalle es otra tarea de este mismo plan (Task 9, sin mergear
todavía); dejar la RUTA también en una variable -- no solo el dominio --
permite apuntar el enlace al camino real que Task 9 termine eligiendo sin
tocar este código.
"""
from __future__ import annotations

import asyncio
import html as html_lib
import logging
import os

import smtp_config
from db.connection import get_pool

logger = logging.getLogger(__name__)

#: Referencias fuertes a las tareas en vuelo. asyncio SOLO mantiene una
#: referencia débil a una Task creada con `create_task`: sin esto, el GC
#: puede recolectarla a mitad de camino (está documentado en
#: asyncio.create_task) y un correo que iba a salir, no sale, sin ningún
#: error que lo explique. Se limpia sola en `add_done_callback`.
_TAREAS_EN_VUELO: set[asyncio.Task] = set()


def encolar_aviso_fin_pipeline(pid: str, tenant_id: str, user_id: str, status: str, nombre: str) -> None:
    """Se llama desde `_poll_one_pipeline` al detectar completed/failed/
    disputed/expired. SÍNCRONA y no bloqueante a propósito: dispara la Task
    y devuelve el control al poller en el mismo tick -- no hay `await` acá
    adentro.

    NUNCA se llama con status `discarded`/`hidden` (Task 4, spec
    descartar-pipelines, fix round 1 Ruling 12, 2026-09-22): el propio
    `_poll_one_pipeline` filtra esos dos ANTES de invocar esta función,
    porque a diferencia de completed/failed/disputed/expired -- que el
    pipeline alcanza SOLO -- son un pedido EXPLÍCITO de un usuario o del
    superadmin; avisarle por correo que "terminó" es ruido, no información.
    Por eso `_asunto`/`_enviar_aviso` de abajo no tienen (y no necesitan)
    una rama para esos dos estados."""
    tarea = asyncio.create_task(_procesar_aviso(pid, tenant_id, user_id, status, nombre))
    _TAREAS_EN_VUELO.add(tarea)
    tarea.add_done_callback(_TAREAS_EN_VUELO.discard)


async def _procesar_aviso(pid: str, tenant_id: str, user_id: str, status: str, nombre: str) -> None:
    """Todo el trabajo real, fuera del tick del poller. Nunca lanza: es el
    cuerpo de una Task fire-and-forget y nadie espera su resultado."""
    try:
        if not await _reclamar(pid):
            return  # ya se avisó (u otro tick/proceso ganó la carrera)

        email = await _correo_del_dueno(user_id)
        if not email:
            logger.warning(
                "aviso_pipeline: pipeline=%s tenant=%s user=%s sin correo activo -- no se avisa",
                pid, tenant_id, user_id,
            )
            return

        try:
            settings = await smtp_config.cargar_settings()
        except smtp_config.SmtpNoDisponible as exc:
            logger.error(
                "aviso_pipeline: pipeline=%s tenant=%s user=%s correo deshabilitado "
                "(%s, motivo: %s) -- no se avisó",
                pid, tenant_id, user_id, exc.codigo, getattr(exc, "motivo", "-"),
            )
            return

        try:
            # smtplib es bloqueante: a un hilo, nunca en el event loop del poller.
            await asyncio.to_thread(_enviar_aviso, settings, email, pid, status, nombre)
        except Exception:  # fail-soft: el pipeline ya terminó, esto no lo cambia; queda logueado con traceback (CON RASTRO, no silencioso)
            logger.exception(
                "aviso_pipeline: pipeline=%s tenant=%s user=%s status=%s -- falló el envío del aviso",
                pid, tenant_id, user_id, status,
            )
    except Exception:  # fail-soft: red de seguridad final de una Task que nadie espera
        logger.exception("aviso_pipeline: fallo inesperado procesando pipeline=%s", pid)


async def _reclamar(pid: str) -> bool:
    """INSERT IGNORE contra la PK de pipeline_aviso_enviado: atómico en
    MariaDB, así que dos ticks (o dos procesos) que observan la misma
    transición solo hacen ganar a uno. `cur.rowcount` es 1 si esta llamada
    insertó la fila, 0 si ya existía -- mismo criterio que
    `uso/reintento.py` con su UNIQUE de spool_id."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT IGNORE INTO pipeline_aviso_enviado (pipeline_id) VALUES (%s)", (pid,))
            gano = cur.rowcount == 1
        await conn.commit()
    return gano


async def _correo_del_dueno(user_id: str) -> str | None:
    """El destinatario es el dueño del pipeline, leído de jax_users por
    user_id -- nunca una dirección fija. `None` cubre TODO lo que hace que
    no haya a quién mandarle: el id no es numérico (placeholders de test o
    un dato corrupto), la fila no existe, o el usuario no está activo (de
    baja: email_de_baja lo reescribe a algo inutilizable, api/admin/users.py).
    Quien llama decide qué hacer con `None` -- acá solo se resuelve."""
    try:
        uid = int(user_id)
    except (TypeError, ValueError):
        return None

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT email FROM jax_users WHERE user_id = %s AND status = 'active'", (uid,))
            fila = await cur.fetchone()
    return fila[0] if fila else None


def _asunto(status: str) -> str:
    # "disputed" (ronda feat/estado-disputed, 2026-09-18): el árbitro agotó
    # el tope de devoluciones con una objeción SIN RESOLVER -- ni aprobado ni
    # fallido, pide la decisión de Fernando. Un asunto genérico ("terminó")
    # lo hace indistinguible de un pipeline que salió bien; el pedido
    # explícito es que el correo diga que hay una objeción sin resolver.
    if status == "disputed":
        return "Tu pipeline tiene una objeción sin resolver — Axioma"
    if status == "failed":
        return "Tu pipeline falló — Axioma"
    return "Tu pipeline terminó — Axioma"


def _enlace_detalle(pid: str) -> str:
    """Sin hardcoding de dominio NI de ruta: las dos salen de entorno, nunca
    de una constante en el código (ver el docstring del módulo)."""
    origen = os.getenv("FRONTEND_ORIGIN", "https://axioma-ia.io")
    ruta = os.getenv("PIPELINE_DETAIL_PATH", "/historial/{pipeline_id}")
    return origen + ruta.format(pipeline_id=pid)


def _enviar_aviso(settings: smtp_config.SmtpSettings, to_email: str, pid: str, status: str, nombre: str) -> None:
    """Bloqueante (smtplib): se llama dentro de asyncio.to_thread. LANZA si
    el servidor falla -- `_procesar_aviso` lo registra y no reintenta."""
    enlace = _enlace_detalle(pid)
    # "disputed": mismo criterio que _asunto -- el pedido explícito es que
    # el correo diga que hay una objeción sin resolver, no un genérico
    # "terminó"/"falló" que lo confunda con un pipeline aprobado o con un
    # error cualquiera.
    if status == "disputed":
        estado_legible = "terminó con una objeción del árbitro sin resolver — necesita tu decisión"
    elif status == "failed":
        estado_legible = "falló"
    else:
        estado_legible = "terminó"
    texto = (
        f'Tu pipeline "{nombre}" {estado_legible}.\n\n'
        f"Podés ver el detalle acá:\n{enlace}\n"
    )
    enlace_html = html_lib.escape(enlace, quote=True)
    nombre_html = html_lib.escape(nombre)
    html = (
        f"<p>Tu pipeline <strong>{nombre_html}</strong> {estado_legible}.</p>"
        f'<p><a href="{enlace_html}">{enlace_html}</a></p>'
    )
    mensaje = smtp_config.construir_mensaje(settings, to_email, _asunto(status), texto, html)
    smtp_config.enviar(settings, mensaje)
