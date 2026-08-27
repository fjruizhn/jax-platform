"""Sonda activa de facets.

POR QUE ACTIVA Y NO PASIVA: del 2026-08-20 al 08-26 inclusive hubo CERO
turnos de chat, y thot quedo rebindeado a gpt-5.6-terra el 08-24 11:08:01.
Durante los tres dias que estuvo roto nadie lo llamo. Un detector derivado
del trafico real no habria detectado nada -- ver §1.3 del spec.

COSTO: cada sonda es una llamada PAGA a un proveedor real. Ningun test
puede ejecutarla; el loop no arranca bajo pytest (ver
_running_under_pytest). Precedente: 2026-08-24, correr pytest disparo 11
dispatches reales a produccion."""
import asyncio
import logging
import os
import sys

from api.chat import _invoke_facet, _load_config
from facet_health import record_facet_health, OUTCOME_PROBE_ERROR

logger = logging.getLogger(__name__)

CANARY_INTERVAL_SECONDS = 3600
CANARY_USER_ID = "__canary__"
# NO puede parecer una pregunta de identidad de modelo: _is_model_identity_question()
# cortocircuitea antes del dispatch y devolveria una respuesta enlatada, o
# sea `ok` sin haber tocado al proveedor. Hay un test que lo verifica.
CANARY_MESSAGE = "Respondé únicamente con la palabra: listo."

# hyde no se sondea: chat() lo corta antes del dispatch con una respuesta
# enlatada, no hay nada que medir.
_NOT_DISPATCHED = frozenset({"hyde"})


def _running_under_pytest() -> bool:
    return "PYTEST_CURRENT_TEST" in os.environ or "pytest" in sys.modules


def canary_facets(config: dict) -> list[str]:
    """El MISMO conjunto contra el que chat() valida req.facet
    (api/chat.py:902), o sea exactamente lo que un usuario puede elegir.

    NO se filtra por transporte a proposito: si se filtrara a "transportes
    despachables", kimi (transport=motor_registry) quedaria fuera y su
    caida seria invisible por diseno -- que es justo la clase de falla que
    esta feature existe para detectar."""
    return sorted(set(config["personalities"]) - _NOT_DISPATCHED)


async def probe_facet(facet: str, config: dict, source: str) -> str | None:
    """Sondea UN facet. Devuelve None si logro invocar, 'probe_error' si no.

    NUNCA devuelve 'ok'. El resultado real de la invocacion ya lo registro
    _invoke_facet en la tabla (Task 3); si la sonda ademas dijera 'ok' por
    su cuenta habria DOS lugares decidiendo que es sano. Y una denegacion
    del gate retorna NORMALMENTE (con el string de degradacion), asi que un
    'ok' basado en "no lanzo excepcion" reportaria verde justo sobre el
    fallo que esta ronda cierra.

    La sonda pasa por el gate igual que el chat real, porque invoca la
    MISMA funcion: los estados gate_denied y gate_unreachable solo ocurren
    DENTRO del gate. Una sonda que resolviera el facet por su cuenta y
    llamara al proveedor directo seria ciega a los dos."""
    try:
        await _invoke_facet(facet, config, CANARY_USER_ID, CANARY_MESSAGE,
                            source=source)
        return None
    except Exception as e:
        # La sonda no llego a completar la invocacion. Un detector que falla
        # produce un evento, no un silencio.
        await record_facet_health(
            facet, OUTCOME_PROBE_ERROR, source, f"{type(e).__name__}: {e}")
        return OUTCOME_PROBE_ERROR


async def probe_all(source: str = "canary_periodic") -> list[str | None]:
    config = _load_config()
    return [await probe_facet(f, config, source) for f in canary_facets(config)]


async def start_facet_canary() -> None:
    if _running_under_pytest():
        logger.warning("facet_canary: no arranca bajo pytest (llamadas pagas)")
        return
    while True:
        try:
            await probe_all("canary_periodic")
        except Exception:  # fail-soft: loop en background, mismo patron que owner_cleanup.py -- nunca debe tumbar el proceso, el proximo ciclo reintenta
            logger.warning("facet_canary: barrido fallo", exc_info=True)
        await asyncio.sleep(CANARY_INTERVAL_SECONDS)
