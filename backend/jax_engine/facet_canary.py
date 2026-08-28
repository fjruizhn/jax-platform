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
from facet_health import (
    record_facet_health,
    OUTCOME_PROBE_ERROR,
    SOURCE_CANARY_PERIODIC,
    SOURCE_CANARY_REBIND,
)
from facet_resolver import invalidate_facet_cache

logger = logging.getLogger(__name__)

# Mismo patron que FACET_CACHE_TTL_SECONDS en facet_resolver.py:18 -- kill
# switch sin deploy. Ronda de correccion 1 de Task 4, Hallazgo 4: un valor
# <= 0 apaga la sonda (chequeado en start_facet_canary, con warning
# explicito -- nunca en silencio).
CANARY_INTERVAL_SECONDS = int(os.getenv("CANARY_INTERVAL_SECONDS", "3600"))

# Hallazgo 2 de la misma ronda: resolve_facet() -> aiomysql.connect() no
# tiene connect_timeout, y el cur.execute() tampoco tiene timeout propio.
# Los timeouts HTTP de _invoke_facet_dispatch SI existen (gate 5s, ollama
# 180s, openai_compat/gemini 120s) -- el agujero es solo la DB. Sin un
# timeout ACA, una MariaDB que acepta la conexion y no contesta cuelga
# probe_all() para siempre: el `while True` nunca llega al sleep, y la
# sonda muere sin log, sin fila y sin evento.
#
# N=900 (15 min), elegido con este calculo: el peor caso LEGITIMO de un
# barrido completo (canary_facets ordena alfabetico: ada, hipatia,
# jax_local, jekyll, kimi, thot) es 4 facets gobernados (ada/hipatia/
# jekyll/thot, cada uno gate 5s + hasta 120s de proveedor = 125s) + 180s
# de jax_local (ollama, no gobernado, timeout mas alto del repo) + kimi
# (unsupported_transport, retorno inmediato sin red) = 4*125 + 180 = 680s.
# 900s deja ~32% de margen sobre ese peor caso legitimo y sigue siendo un
# cuarto del intervalo por defecto (3600s), asi que un barrido colgado no
# se come el proximo ciclo.
CANARY_SWEEP_TIMEOUT_SECONDS = 900

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
    llamara al proveedor directo seria ciega a los dos.

    Cuando la invocacion falla, NO escribe: la capa de abajo ya registro el
    evento clasificado. Devuelve 'probe_error' igual, como valor de
    retorno, para que probe_all pueda contar sondeos fallidos sin consultar
    la DB."""
    try:
        await _invoke_facet(facet, config, CANARY_USER_ID, CANARY_MESSAGE,
                            source=source)
        return None
    except Exception:
        # NO se registra aca -- decision de diseno, no un olvido.
        #
        # `_invoke_facet` es un envoltorio TOTAL: cuando lanza, ya escribio
        # el evento clasificado (provider_error / config_error / ...) antes
        # de re-lanzar. Un `probe_error` aca seria una SEGUNDA fila para la
        # misma causa, ~800us mas nueva; el lector (jacobs/facet_health.py,
        # repo jax) toma MAX(ts) por facet, asi que ganaria la generica y la
        # alerta diria "la sonda fallo" en vez de nombrar la causa
        # accionable. Evidencia real del 2026-08-27:
        #
        #   ada  probe_error   canary_rebind  ModelDispatchConfigError: ...  18:02:45.443634
        #   ada  config_error  canary_rebind  ModelDispatchConfigError: ...  18:02:45.442861
        #
        # La propiedad de la que esto depende no es una suposicion: la
        # protege tests/test_policy_invoke_facet_envoltorio.py, que corre en
        # CI y se verifico rompiendolo.
        #
        # Esto NO es fail-open: el evento existe, lo escribio la capa de
        # abajo con mas informacion. El caso en que NADIE escribio --
        # fallar antes de llegar a _invoke_facet -- lo cubre el `except` de
        # probe_after_rebind, que sigue registrando.
        #
        # Ver docs/superpowers/specs/2026-08-28-alerta-capa-equivocada-design.md
        return OUTCOME_PROBE_ERROR


async def probe_all(source: str = SOURCE_CANARY_PERIODIC) -> list[str | None]:
    config = _load_config()
    return [await probe_facet(f, config, source) for f in canary_facets(config)]


async def probe_after_rebind(facet_key: str) -> str | None:
    """Sonda disparada por un cambio de binding.

    Se encola con BackgroundTasks DESPUES del conn.commit() del escritor:
    FastAPI corre las background tasks despues de emitir la respuesta, asi
    que "primero aprobado, despues sondeado" queda garantizado por
    construccion, no por convencion.

    ANTES de sondear, no despues, y SIEMPRE (incluso con el kill switch
    abajo apagado): invalida la entrada cacheada del facet. resolve_facet()
    cachea por FACET_CACHE_TTL_SECONDS (30s default) y ningun escritor de
    facet_binding invalidaba esa cache -- esta sonda dispara DENTRO de esa
    ventana, justo despues de aprobar. Sin este invalidate, resolveria el
    modelo VIEJO, lo llamaria, recibiria 200 y reportaria `ok` sobre el
    rebinding que la sonda existe para vigilar -- el escenario del
    2026-08-24, reproducido por la herramienta que viene a cerrarlo. La
    invalidacion NO es parte de la sonda -- es una correccion de frescura
    para el proximo turno de chat -- asi que el kill switch de abajo no la
    apaga: apagar la sonda no debe dejar la cache sucia despues de un
    rebind.

    Su resultado se alerta en el barrido siguiente del reaper (<=300s), no
    en la corrida horaria: por eso el lector evalua en CADA barrido.

    TODO el cuerpo va adentro del try, invalidate_facet_cache incluido:
    esto corre dentro de una BackgroundTask, y verificado empiricamente
    contra el FastAPI de este venv (TestClient real, no supuesto) una
    excepcion ACA no la traga Starlette -- propaga. Bajo uvicorn la
    respuesta ya se emitio (el admin ve 200), asi que el traceback
    quedaria SOLO en el journal -- y el journal no es donde mira el
    reaper, que lee la tabla `facet_health_event`. Un fallo que solo deja
    rastro en journalctl es exactamente el modo de falla que esta ronda
    vino a eliminar, agravado porque el camino por rebinding es el que
    baja la deteccion de tres dias a minutos. Ademas -- mismo experimento
    -- una BackgroundTask que lanza aborta las que esten encoladas
    DESPUES en la misma request (verificado: una segunda tarea encolada a
    continuacion nunca corrio). Hoy cada escritor encola una sola tarea,
    asi que no hay victima colateral todavia, pero es una propiedad real
    del mecanismo de BackgroundTasks de Starlette/FastAPI, no un detalle
    de esta funcion -- relevante si algun dia se encola una segunda tarea
    despues de esta (api/chat.py ya encola run_shadow_validation con el
    mismo mecanismo, para otro endpoint)."""
    try:
        invalidate_facet_cache(facet_key)

        if CANARY_INTERVAL_SECONDS <= 0:
            # Mismo kill switch que start_facet_canary (Hallazgo 4, ronda
            # de correccion 1 de Task 4): start_facet_canary lo consulta
            # antes de arrancar el loop, pero esta sonda no pasa por ese
            # loop -- dispara directo desde un escritor. Sin este
            # chequeo, un operador que apaga la sonda para cortar
            # llamadas pagas seguiria pagando una por cada approve y por
            # cada PUT a facet-bindings.
            logger.warning(
                "facet_canary: sonda por rebinding deshabilitada "
                "(CANARY_INTERVAL_SECONDS=%s <= 0), facet=%s",
                CANARY_INTERVAL_SECONDS, facet_key)
            return None

        config = _load_config()
        return await probe_facet(facet_key, config, SOURCE_CANARY_REBIND)
    except Exception as e:  # fail-soft: corre en una BackgroundTask ya con la respuesta emitida -- re-lanzar solo dejaria rastro en journalctl (el reaper lee facet_health_event, no el journal) y ademas abortaria cualquier BackgroundTask encolada despues de esta en la misma request. El evento en la tabla, no la excepcion, es el detector real.
        await record_facet_health(
            facet_key, OUTCOME_PROBE_ERROR, SOURCE_CANARY_REBIND,
            f"{type(e).__name__}: {e}")
        return OUTCOME_PROBE_ERROR


async def start_facet_canary() -> None:
    if _running_under_pytest():
        logger.warning("facet_canary: no arranca bajo pytest (llamadas pagas)")
        return
    if CANARY_INTERVAL_SECONDS <= 0:
        # Kill switch (Hallazgo 4): apagado explicito y RUIDOSO, no un loop
        # que nunca arranca en silencio -- un operador que mire logs tiene
        # que poder confirmar que la sonda esta apagada a proposito.
        logger.warning(
            "facet_canary: deshabilitada (CANARY_INTERVAL_SECONDS=%s <= 0)",
            CANARY_INTERVAL_SECONDS)
        return
    while True:
        try:
            # Hallazgo 2: sin este timeout, un colgado en resolve_facet()
            # (aiomysql sin connect_timeout) mata el barrido en silencio --
            # el while True nunca llega al sleep, y thot (ultimo en el orden
            # alfabetico de canary_facets) es el mas expuesto a quedar sin
            # sondear si algo anterior en la lista cuelga.
            async with asyncio.timeout(CANARY_SWEEP_TIMEOUT_SECONDS):
                await probe_all(SOURCE_CANARY_PERIODIC)
        except Exception:  # fail-soft: loop en background, mismo patron que owner_cleanup.py -- nunca debe tumbar el proceso, el proximo ciclo reintenta. Incluye TimeoutError del asyncio.timeout de arriba.
            logger.warning("facet_canary: barrido fallo", exc_info=True)
        await asyncio.sleep(CANARY_INTERVAL_SECONDS)
