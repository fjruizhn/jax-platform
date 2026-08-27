"""Escritor de facet_health_event -- unico lugar donde se registra el
resultado de una invocacion de facet. La SALUD se calcula en otro lado
(jax/jacobs/facet_health.py, en el reaper): aca solo se escriben hechos.
Ver docs/superpowers/specs/2026-08-27-alertas-facets-caidos-design.md."""
import logging
import time

from db.connection import get_pool

logger = logging.getLogger(__name__)

OUTCOME_OK = "ok"
OUTCOME_PROVIDER_ERROR = "provider_error"
OUTCOME_CONFIG_ERROR = "config_error"
OUTCOME_GATE_DENIED = "gate_denied"
OUTCOME_GATE_UNREACHABLE = "gate_unreachable"
OUTCOME_UNBOUND = "unbound"
OUTCOME_UNSUPPORTED_TRANSPORT = "unsupported_transport"
OUTCOME_PROBE_ERROR = "probe_error"

OUTCOMES = frozenset({
    OUTCOME_OK, OUTCOME_PROVIDER_ERROR, OUTCOME_CONFIG_ERROR,
    OUTCOME_GATE_DENIED, OUTCOME_GATE_UNREACHABLE, OUTCOME_UNBOUND,
    OUTCOME_UNSUPPORTED_TRANSPORT, OUTCOME_PROBE_ERROR,
})
SOURCES = frozenset({"chat", "canary_periodic", "canary_rebind"})

_DETAIL_MAX = 255

# Rastro observable del fallo del ESCRITOR. En memoria y no en la DB a
# proposito: la DB es precisamente lo que puede estar caido. Un escritor
# que pierde filas en silencio hace que la salud se calcule sobre datos
# incompletos, y eso se ve identico a "no paso nada" -- la misma forma del
# agujero `unknown` vs `ok`, un nivel mas abajo.
# Por proceso: se reinicia con el servicio. Mide fallos desde el ultimo
# arranque, no un historico. En v1 no alerta, solo es observable.
_write_failures = 0
_last_write_error: str | None = None


def write_failure_stats() -> dict:
    """Lo publica GET /api/health. Ese endpoint informa la salud del
    SERVICIO, y un escritor que no escribe es exactamente eso."""
    return {"write_failures": _write_failures, "last_error": _last_write_error}


def reset_write_failures() -> None:
    """Solo para tests -- que cada test parta de cero sin depender del orden."""
    global _write_failures, _last_write_error
    _write_failures = 0
    _last_write_error = None


async def record_facet_health(
    facet: str, outcome: str, source: str, detail: str | None = None
) -> bool:
    """Escribe UNA fila. Devuelve True si escribio, False si fallo.

    Fail-soft ante error de DB a proposito: un fallo registrando salud no
    puede tumbar un turno de chat que ya respondio. Pero NUNCA silencioso
    -- loguea, y la ausencia de la fila se convierte rio abajo en
    `unknown`, jamas en `ok` (ver el lector en jax/jacobs/facet_health.py).

    Un `outcome` o `source` invalido SI lanza: es un bug del llamador, no
    una condicion de runtime, y enmascararlo dejaria filas que el ENUM
    rechazaria en silencio."""
    if outcome not in OUTCOMES:
        raise ValueError(f"outcome invalido: {outcome!r}")
    if source not in SOURCES:
        raise ValueError(f"source invalido: {source!r}")

    if detail is not None:
        detail = detail[:_DETAIL_MAX]

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO facet_health_event "
                    "(facet, outcome, source, detail, ts) VALUES (%s,%s,%s,%s,%s)",
                    (facet, outcome, source, detail, time.time()),
                )
            await conn.commit()
        return True
    except Exception as e:  # fail-soft: registrar salud no puede tumbar un turno de chat ya respondido; la ausencia de fila se lee como `unknown` rio abajo, nunca como `ok`
        global _write_failures, _last_write_error
        _write_failures += 1
        _last_write_error = f"{type(e).__name__}: {e}"[:_DETAIL_MAX]
        # Prefijo estable: se cuenta desde journalctl sin depender del endpoint.
        logger.warning(
            "facet_health_write_failed facet=%s outcome=%s source=%s total=%d",
            facet, outcome, source, _write_failures, exc_info=True,
        )
        return False
