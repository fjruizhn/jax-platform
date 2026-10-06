"""Cierre de conversaciones web por inactividad (decisión de Fernando, 2026-10-05).

El worker de memoria solo destila hechos de conversaciones CERRADAS
(`conversations.ended_at`). Antes, la plataforma solo cerraba al apagar la app
o por LRU, y se acumularon conversaciones abiertas por más de 7 días: la
memoria nunca extraía. Una conversación web se cierra tras N minutos sin
actividad.

Umbral: variable de entorno `JAX_CONVERSACION_INACTIVIDAD_MIN` (minutos,
entero > 0; por defecto 30). Un valor ilegible NO tumba el chat: avisa en
WARNING y usa el valor por defecto.

Aquí viven la configuración y el cierre al arrancar (SQL). El estado en
proceso (último uso por conversación, cierre perezoso en `_get_conv_uuid` y el
barrido periódico) vive en `api/chat.py`, junto a `_conv_uuids`.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

VARIABLE_UMBRAL = "JAX_CONVERSACION_INACTIVIDAD_MIN"
UMBRAL_POR_DEFECTO_MIN = 30
# `conversations.source` que escribe `_get_conv_uuid` (api/chat.py). El REPL
# (`terminal`) y cualquier otro origen NO se tocan.
ORIGENES_WEB = ("axioma-web", "axioma-web-proyecto")


def umbral_inactividad_minutos() -> int:
    crudo = os.environ.get(VARIABLE_UMBRAL)
    if crudo is None or crudo.strip() == "":
        return UMBRAL_POR_DEFECTO_MIN
    try:
        valor = int(crudo.strip())
        if valor <= 0:
            raise ValueError("debe ser mayor que 0")
    except ValueError as e:
        logger.warning("%s=%r no es un entero de minutos mayor que 0 (%s): se usa el valor por defecto %d",
                       VARIABLE_UMBRAL, crudo, e, UMBRAL_POR_DEFECTO_MIN)
        return UMBRAL_POR_DEFECTO_MIN
    return valor


# Última actividad = el último mensaje, o el inicio si no tiene mensajes.
# Plan: `idx_conversations_open (ended_at, started_at)` resuelve
# `ended_at IS NULL AND started_at < ...` por rango; la subconsulta correlada
# va por `idx_messages_conversation_turn (conversation_id, ...)` y solo corre
# sobre las abiertas y viejas.
SQL_CERRAR_HUERFANAS = (
    "UPDATE conversations c "
    "SET c.ended_at = NOW(), c.memory_processed = FALSE "
    "WHERE c.ended_at IS NULL "
    "AND c.source IN (" + ", ".join(["%s"] * len(ORIGENES_WEB)) + ") "
    "AND c.started_at < NOW() - INTERVAL %s MINUTE "
    "AND COALESCE((SELECT MAX(m.created_at) FROM messages m "
    "WHERE m.conversation_id = c.id), c.started_at) < NOW() - INTERVAL %s MINUTE"
)


async def cerrar_huerfanas_al_arrancar(pool) -> int:
    """Cierra en la base las conversaciones web abiertas cuya última actividad
    es anterior al umbral (las que un reinicio dejó sin rastrear en proceso).
    Best-effort: nunca lanza; devuelve cuántas cerró."""
    try:
        minutos = umbral_inactividad_minutos()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(SQL_CERRAR_HUERFANAS, (*ORIGENES_WEB, minutos, minutos))
                cerradas = cur.rowcount or 0
        if cerradas:
            logger.info("Conversaciones web huérfanas cerradas al arrancar: %d (umbral %d min)",
                        cerradas, minutos)
        return cerradas
    except Exception:  # fail-soft: best-effort al arrancar; una base caída no debe tumbar el servicio, queda en WARNING
        logger.warning("No se pudieron cerrar las conversaciones web huérfanas al arrancar", exc_info=True)
        return 0
