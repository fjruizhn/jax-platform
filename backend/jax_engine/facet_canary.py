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
import math
import os
import sys
import uuid
from datetime import datetime, timezone
from urllib.parse import urlsplit

import aiomysql

from api.admin.usage import calcular_costo, encolar_o_contar_perdida, record_usage
from api.chat import _invoke_facet, _load_config
from config_entorno import url_requerida
from db.connection import get_pool
from ejecutor import misiones
from facet_health import (
    record_facet_health,
    OUTCOME_PROBE_ERROR,
    SOURCE_CANARY_PERIODIC,
    SOURCE_CANARY_REBIND,
)
from facet_resolver import invalidate_facet_cache
import kill_switch
from redaccion import texto_de_error

logger = logging.getLogger(__name__)

# Mismo patron que FACET_CACHE_TTL_SECONDS en facet_resolver.py:18 -- kill
# switch sin deploy. Ronda de correccion 1 de Task 4, Hallazgo 4: un valor
# <= 0 apaga la sonda (chequeado en start_facet_canary, con warning
# explicito -- nunca en silencio).
CANARY_INTERVAL_SECONDS = int(os.getenv("CANARY_INTERVAL_SECONDS", "3600"))

# Hallazgo 2 de la misma ronda: resolve_facet() -> aiomysql.connect() no
# tiene connect_timeout, y el cur.execute() tampoco tiene timeout propio. Los
# timeouts HTTP de _invoke_facet_dispatch SI existen (gate 5s, ollama 180s,
# openai_compat/gemini 120s) -- el agujero es solo la DB. Sin un timeout ACA,
# una MariaDB que acepta la conexion y no contesta cuelga el barrido para
# siempre: el `while True` nunca llega al sleep y la sonda muere sin log.
#
# ---------------------------------------------------------------------------
# PRESUPUESTOS DE TIEMPO (recalculados el 2026-10-04, ronda 2 del arreglo de
# el_juez). Todo cuelga de UNA invariante: el lector (jacobs/facet_health.py,
# repo jax) da `unknown` si el ultimo evento de una faceta tiene mas de
# HEALTH_WINDOW_SECONDS = 7200 s = 2 x intervalo, SIN margen. Lo que importa no
# es que el barrido quepa en un tercio del intervalo (criterio viejo) sino que
# el HUECO entre dos eventos consecutivos de una faceta quede < 7200 s.
#
# 1. Tope POR FACETA (CANARY_FACET_TIMEOUT_SECONDS = 400). Peor caso legitimo de
#    UNA faceta: ollama = esperar el carril de la Mesa (hasta 180, un turno de
#    chat de jax_local) + la llamada (180) = 360; gobernadas = gate 5 + proveedor
#    120 = 125. A eso, 10 de la lectura de "mision en curso" (que vive dentro de
#    probe_facet) = 370, y 30 de margen = 400. Una faceta que lo excede se corta
#    con un WARNING y el barrido SIGUE con las demas: una lenta ya no cancela a
#    las que le siguen.
#
# 2. Tope del BARRIDO (CANARY_SWEEP_TIMEOUT_SECONDS = 1500). Conjunto actual:
#    5 gobernadas (ada, hipatia, jekyll, kimi, thot) + 2 ollama (jax_local,
#    el_juez). Peor caso legitimo: 5*(125+10) + 2*(360+10) + 10 (leer el
#    conjunto) = 675 + 740 + 10 = 1425 s. 1500 deja ~5% de margen sobre eso, y
#    el techo que admite la cuenta del punto 4 es 1539.
#    Historia: 900 (2026-09-11) -> 1080 -> 1180 (2026-09-17, SP3) -> 1560
#    (ronda 2) -> 1500 (ronda 3: con 1560 la cota del punto 4 daba 7240).
#
# 3. Reintento de las diferidas (CANARY_DEFERRED_MAX_SECONDS = 1800, cada
#    CANARY_RETRY_SECONDS = 60). Una sonda saltada por mision NO se descarta:
#    se reintenta cada 60 s mientras siga la mision, con un tope PROPIO que no
#    consume el del barrido (asyncio.timeout separado).
#
# 4. La cuenta del hueco. El ciclo es: barrido (S) + reintentos (D) + dormir
#    CANARY_INTERVAL_SECONDS (3600). Entre dos eventos consecutivos de una
#    misma faceta (a = su posicion dentro del barrido, 0 <= a <= S):
#        hueco = S_k + D_k + 3600 + a_(k+1) - a_k
#    y a_(k+1) puede ser tan tarde como S_(k+1) + D_(k+1) (la propia faceta
#    diferida), asi que la cota correcta suma LOS DOS ciclos:
#        hueco <= 3600 + S_k + D_k + S_(k+1) + D_(k+1) = 3600 + 2*S + 2*D
#    Mision de duracion normal (~200 s): el ultimo reintento cae a lo sumo
#    60 s despues de que termina, o sea D <= 200 + 60 = 260. Con S = 1500 (el
#    tope duro, mas pesimista que el legitimo de 1425):
#        hueco <= 3600 + 2*1500 + 2*260 = 7120 s  <  7200 s   (margen 80 s)
#    (Con 1560 daba 7240 > 7200: error de la ronda 2, corregido en la 3.)
#    Sin mision (D = 0): <= 3600 + 3000 = 6600 s.
#    LO QUE ESTO NO CUBRE: una mision de mas de ~1800 s agota el tope diferido
#    (WARNING) y el hueco puede pasar de 7200 s -- ahi el `unknown` del reaper
#    es legitimo: lleva mas de dos horas sin medirse. Si cambia el intervalo,
#    el conjunto de facetas, un timeout de _invoke_facet_dispatch o la ventana
#    del lector, esta cuenta se rehace. La deriva de las constantes, contra el
#    HEALTH_WINDOW_SECONDS del lector real, tests/test_facet_canary_internas.py::
#    test_los_presupuestos_cumplen_la_cuenta_del_hueco.
#
# 5. Una faceta cortada por su tope (punto 1) escribe una fila probe_error con
#    detalle "tope de faceta" (la escritura tiene su propio tope de base): el
#    reaper la ve `down` con causa, no `unknown`.
# ---------------------------------------------------------------------------
CANARY_FACET_TIMEOUT_SECONDS = 400
CANARY_SWEEP_TIMEOUT_SECONDS = 1500
CANARY_DEFERRED_MAX_SECONDS = 1800
CANARY_RETRY_SECONDS = 60

CANARY_USER_ID = "__canary__"
# NO puede parecer una pregunta de identidad de modelo: _is_model_identity_question()
# cortocircuitea antes del dispatch y devolveria una respuesta enlatada, o
# sea `ok` sin haber tocado al proveedor. Hay un test que lo verifica.
CANARY_MESSAGE = "Respondé únicamente con la palabra: listo."

# PR 8 del diseno del tablero de consumo (2026-10-06,
# ~/encargos-codex/diseno-tablero-consumo.md #12): cada sonda es una llamada
# PAGA y hasta hoy no dejaba fila en axioma_usage (~700 canary_periodic ok
# por faceta y mes mientras Costos mostraba «sin consumo»). La fila va por
# record_usage -- la MISMA via que el chat -- con este request_type propio,
# y SIN usuario: el canario no es una persona, entra con tenant/user NULL y
# no con el DEFAULT 1 de la columna, que le atribuiria el gasto de
# infraestructura a un tenant de verdad. Mismo tratamiento que las llamadas
# de sistema del repo jax (preflight_probe, jacobs/usage_writer.py).
REQUEST_TYPE_CANARIO = "canario"

# Tope de las dos lecturas de la base que hace la sonda (el conjunto de facetas
# y la misión en curso). Mismo agujero que el Hallazgo 2: aiomysql no tiene
# timeout propio de consulta, y sin este tope una MariaDB que no contesta se
# come el barrido entero antes de sondear nada.
CANARY_DB_TIMEOUT_SECONDS = 10

# Tope PROPIO y corto del registro de uso de la sonda (precio + INSERT). El
# registro corre DESPUES de cerrar el tope de salud (CANARY_FACET_TIMEOUT_SECONDS)
# y blindado con asyncio.shield: ni ese tope ni el del barrido lo cancelan.
# Una base lenta (pool agotado, metadata lock, commit lento) vence ESTE tope y
# la fila se encola con su spool_id y su costo ya calculado, sin tocar la
# salud. Configurable por entorno como CANARY_INTERVAL_SECONDS (segundos,
# default 5). Tiene que ser un numero FINITO y > 0: un valor ilegible, nan, inf
# o <= 0 es un error de arranque claro, no un tope que nunca vence.
def _leer_tope_de_uso(crudo: str) -> float:
    try:
        valor = float(crudo)
    except ValueError:
        raise ValueError(
            f"CANARY_USAGE_TIMEOUT_SECONDS debe ser un numero finito > 0, no {crudo!r}") from None
    if not (math.isfinite(valor) and valor > 0):
        raise ValueError(
            f"CANARY_USAGE_TIMEOUT_SECONDS debe ser un numero finito > 0, no {crudo!r}")
    return valor


CANARY_USAGE_TIMEOUT_SECONDS = _leer_tope_de_uso(os.getenv("CANARY_USAGE_TIMEOUT_SECONDS", "5"))

# hyde no se sondea: chat() lo corta antes del dispatch con una respuesta
# enlatada, no hay nada que medir.
_NOT_DISPATCHED = frozenset({"hyde"})

# Con el freno puesto la sonda no sale (2026-09-17, ruling del controlador
# principal sobre R19 del frente B): el freno significa que JAX no invoca
# musculos, y cada sonda es una invocacion PAGA a un proveedor. Se devuelve
# este valor en lugar de sondear. NO es un outcome de facet_health a
# proposito: un salto no es una caida, y no se escribe ninguna fila -- una
# fila de error haria que el reaper alertara facetas sanas. El rastro queda
# en el log (WARNING). Se mira el freno en CADA facet: un freno puesto a
# mitad de un barrido corta el resto.
SALTADA_POR_FRENO = "saltada_por_freno"

# Con una misión del Ejecutor en curso la sonda tampoco sale (2026-10-04): una
# sonda es una invocación al modelo y, en las facetas locales (ollama), toma el
# carril de la Mesa y compite por la GPU con el turno de la misión. Mismo
# criterio que el freno: se devuelve este valor, NO se escribe ninguna fila (un
# salto no es una caída: una fila de error haría alertar a facetas sanas) y el
# rastro queda en el log. La fuente de verdad es la que usa el propio Ejecutor,
# `misiones.turno_en_curso()` (ejecutor_turno.estado = 'en_curso'). Se mira en
# CADA facet, igual que el freno: una misión que arranca a mitad de un barrido
# corta el resto.
#
# Solo salta la sonda PERIODICA. La de rebinding (SOURCE_CANARY_REBIND) la
# dispara un admin a proposito: sondea siempre. Y lo saltado NO se pierde: ver
# _reintentar_diferidas (el lector da `unknown` a las 2 h sin eventos).
#
# CARRERA RESIDUAL, conocida y aceptada: una mision que arranca justo DESPUES
# del chequeo no se ve. Esa sonda puede tener tomado el carril de la Mesa hasta
# ~180 s (timeout de ollama) mientras el proxy del Ejecutor espera el carril con
# tope_s=90 (dato del escalon 3, no verificado en este repo): el turno de la
# mision puede agotar su espera. Cerrarla del todo exigiria que la sonda y el
# Ejecutor compartan un candado, y eso es una decision de diseno aparte.
SALTADA_POR_MISION = "saltada_por_mision"

# Facetas con un binding primary APROBADO (las dos marcas no nulas: un binding
# propuesto y sin aprobar no es de produccion) y la faceta activa (mismo filtro
# que resolve_facet(): una `disabled` no se despacha, sondearla seria medir un
# fallo que el chat nunca tiene). Es la fuente viva: una faceta nueva entra sola.
# Trae el transporte y el base_url del proveedor para el filtro de Python.
SQL_FACETAS_CON_BINDING_APROBADO = (
    "SELECT DISTINCT b.facet_key, f.transport, p.base_url "
    "FROM facet_binding b "
    "JOIN facet f ON f.`key` = b.facet_key "
    "JOIN provider p ON p.id = b.provider_id "
    "WHERE b.role = 'primary' AND b.approved_at IS NOT NULL "
    "AND b.approved_by IS NOT NULL AND f.status = 'active'"
)


def _running_under_pytest() -> bool:
    return "PYTEST_CURRENT_TEST" in os.environ or "pytest" in sys.modules


_PUERTOS_POR_DEFECTO = {"http": 80, "https": 443}

# Errores de la base o del tiempo: lo UNICO ante lo que las lecturas de la sonda
# son fail-open. Un AttributeError/TypeError es un bug nuestro y tiene que verse.
_ERRORES_DE_BASE = (TimeoutError, OSError, aiomysql.Error)


_LOOPBACK = frozenset({"localhost", "127.0.0.1", "::1"})


def _origen(url: str) -> tuple:
    """(esquema, host, puerto). localhost, 127.0.0.1 y ::1 son el mismo host."""
    partes = urlsplit(url)
    host = (partes.hostname or "").lower()
    return (partes.scheme, "loopback" if host in _LOOPBACK else host,
            partes.port or _PUERTOS_POR_DEFECTO.get(partes.scheme))


async def _facetas_con_binding_aprobado() -> list[tuple]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(SQL_FACETAS_CON_BINDING_APROBADO)
            return list(await cur.fetchall())


async def canary_facets(config: dict) -> list[str]:
    """personalities (lo que un usuario puede elegir en chat(), api/chat.py)
    MAS las facetas "internas" con binding primary aprobado y activas en la
    base, menos hyde.

    La union existe porque personalities no es todo lo que JAX invoca: el_juez
    (auditor C5, no auto-seleccionable) tiene binding aprobado y no esta en
    personalities, asi que ninguna sonda lo miraba y el reaper lo reportaba
    `unknown` cada 6 h. `_invoke_facet` resuelve el modelo por resolve_facet()
    (el binding), no por personalities: para una faceta fuera de personalities
    solo toma el system_prompt de jax_local, que la sonda no mide.

    SOLO entran las internas que la sonda mide BIEN. `_call_ollama` ignora el
    base_url del proveedor y siempre va a JAX_OLLAMA_URL: una interna con
    transporte ollama cuyo proveedor apunta a OTRO origen (caso real:
    auditor_local -> ollama_cpu en :11435) se sondearia contra el Ollama
    equivocado y su salud seria la de otro servicio. Se excluye con un WARNING
    que la nombra. Arreglar _call_ollama para que use el base_url resuelto es
    otro cambio. Se compara el ORIGEN (esquema, host, puerto), no la URL
    entera: el proveedor guarda `.../v1` y JAX_OLLAMA_URL no lleva path. Un
    base_url NULL coincide (el despacho va a JAX_OLLAMA_URL igual).

    NO se filtra por transporte a proposito: si se filtrara a "transportes
    despachables", un facet con un transporte que el chat no despacha
    quedaria fuera y su caida seria invisible por diseno -- que es justo la
    clase de falla que esta feature existe para detectar. Caso real: kimi
    tuvo transport=motor_registry hasta 2026-09-11 y fue esta sonda la que
    la mantuvo en `unsupported_transport` a la vista durante un mes.

    Si la base no contesta, se sondea igual lo de personalities y queda un
    WARNING: una lectura caida no puede apagar la sonda entera."""
    facetas = set(config["personalities"])
    try:
        async with asyncio.timeout(CANARY_DB_TIMEOUT_SECONDS):
            filas = await _facetas_con_binding_aprobado()
    except _ERRORES_DE_BASE:  # fail-soft: la sonda es un detector, no puede quedar muda porque falle UNA lectura de la base; se sondea el subconjunto de personalities y el WARNING deja el rastro. Solo errores de base/tiempo: un bug nuestro (AttributeError, TypeError) sube.
        logger.warning(
            "facet_canary: no se pudo leer facet_binding; se sondean solo "
            "las facetas de personalities", exc_info=True)
        return sorted(facetas - _NOT_DISPATCHED)

    origen_ollama = _origen(url_requerida("JAX_OLLAMA_URL"))
    for facet_key, transport, base_url in filas:
        if facet_key in facetas:
            continue
        if transport == "ollama" and base_url and _origen(base_url) != origen_ollama:
            logger.warning(
                "facet_canary: la faceta %s queda fuera de la sonda: su proveedor "
                "Ollama apunta a %s y _call_ollama siempre va a JAX_OLLAMA_URL; "
                "sondearla mediria otro servicio", facet_key, base_url)
            continue
        facetas.add(facet_key)
    return sorted(facetas - _NOT_DISPATCHED)


async def _mision_en_curso() -> bool:
    """True si el Ejecutor tiene un turno en curso. Si la base no contesta,
    False (con WARNING): no saber no puede apagar el detector; el peor caso es
    una sonda mas durante una mision, no una faceta sin vigilar."""
    try:
        async with asyncio.timeout(CANARY_DB_TIMEOUT_SECONDS):
            return await misiones.turno_en_curso() is not None
    except _ERRORES_DE_BASE:  # fail-soft: ver docstring -- ante la duda se sondea. Solo errores de base/tiempo; un AttributeError/TypeError sube.
        logger.warning(
            "facet_canary: no se pudo leer si hay mision en curso; se sondea",
            exc_info=True)
        return False


async def _sondear(facet: str, config: dict, source: str) -> tuple:
    """Sondea UN facet y devuelve `(resultado, usage)`: el resultado es None si
    logro invocar, 'probe_error' si no (o SALTADA_*); `usage` es el UsageInfo de
    un dispatch REAL (None si no hubo gasto). NO registra el uso: lo hace quien
    llama, DESPUES de cerrar el tope de salud (ver _registrar_blindado).

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
    la DB.

    Con el freno puesto no invoca ni escribe: devuelve SALTADA_POR_FRENO y
    lo deja en el log. Con una mision del Ejecutor en curso, igual, pero SOLO
    la sonda periodica: devuelve SALTADA_POR_MISION (el rebind, que es una
    accion explicita de un admin, sondea siempre).

    Si la invocacion tuvo exito y fue un dispatch REAL (usage no None), lo
    devuelve para que se registre la fila de uso como 'canario'
    (REQUEST_TYPE_CANARIO) FUERA del tope de salud: la salud la decide
    _invoke_facet, el costo es OTRO registro -- un problema de contabilidad
    no puede volver probe_error a una sonda ya invocada y sana.

    LIMITE CONOCIDO (auditoria de #212, 2026-10-07; viene del chat, no se
    arregla aqui): una sonda que FALLA DESPUES DE PAGAR no deja fila. Las
    funciones `_call_*` de api/chat.py (p. ej. la lectura de la respuesta
    tras `raise_for_status()`, ~983-985 y ~1017-1021) lanzan si el cuerpo
    llega malformado o el proveedor corta tras facturar, y el UsageInfo solo
    existe cuando la llamada vuelve completa: la sonda es probe_error y el
    gasto de esa llamada no se registra. El subregistro es solo de las
    sondas que fallan despues de pagar; las que vuelven se registran todas.
    Pendiente fechado 2026-10-09 (PENDIENTES.md)."""
    if kill_switch.activo():
        logger.warning(
            "facet_canary: sonda de %s (%s) saltada: el freno esta puesto, "
            "JAX no invoca proveedores", facet, source)
        return SALTADA_POR_FRENO, None
    if source == SOURCE_CANARY_PERIODIC and await _mision_en_curso():
        logger.warning(
            "facet_canary: sonda de %s (%s) saltada: hay una mision del "
            "Ejecutor en curso", facet, source)
        return SALTADA_POR_MISION, None
    try:
        _, usage = await _invoke_facet(facet, config, CANARY_USER_ID, CANARY_MESSAGE,
                                       source=source)
    except Exception:  # fail-soft: _invoke_facet ya registró el evento clasificado antes de relanzar; se devuelve 'probe_error' explícito, sin segunda fila
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
        return OUTCOME_PROBE_ERROR, None
    return None, usage


async def probe_facet(facet: str, config: dict, source: str) -> str | None:
    """Sondea un facet y registra su uso. Devuelve lo que `_sondear` (None si
    logro invocar, 'probe_error', SALTADA_*). Para quien sondea FUERA de un
    tope (el rebind y las pruebas); el barrido usa `_sondear` dentro de su tope
    y registra despues (_sondear_con_tope)."""
    resultado, usage = await _sondear(facet, config, source)
    if usage is not None:
        await _registrar_blindado(facet, source, usage)
    return resultado


# Tareas de registro en vuelo: asyncio solo guarda referencia debil de las
# tareas, y una tarea blindada que nadie mas referencia podria recogerla el
# recolector a mitad de camino. Se sueltan al terminar.
_registros_en_vuelo: set = set()


async def _registrar_blindado(facet: str, source: str, usage) -> None:
    """Corre el registro de uso en una tarea PROPIA y blindada (asyncio.shield):
    ni el tope de faceta ni el del barrido ni un cierre pueden cancelarlo a la
    mitad. Quien espera SI puede ser cancelado (la cancelacion de afuera se
    propaga, nunca se traga), pero la tarea sigue y termina por su cuenta con
    su propio tope (CANARY_USAGE_TIMEOUT_SECONDS) y su encolado."""
    tarea = asyncio.ensure_future(_registrar_uso_de_la_sonda(facet, source, usage))
    _registros_en_vuelo.add(tarea)
    tarea.add_done_callback(_registros_en_vuelo.discard)
    await asyncio.shield(tarea)


async def _registrar_uso_de_la_sonda(facet: str, source: str, usage) -> None:
    """Registra el uso pagado de una sonda ya invocada, sin tocar su salud.

    Tope propio (CANARY_USAGE_TIMEOUT_SECONDS) sobre precio + INSERT. Cada
    sonda lleva su `spool_id` (uuid4): entra en la fila de axioma_usage y, si
    el tope vence o la base falla, la fila se ENCOLA con ese mismo id y con el
    costo ya calculado si alcanzo a calcularse (None = «sin precio», nunca un
    numero inventado). Si el INSERT SI llego a confirmarse antes del corte
    (commit lento), el drenaje (`INSERT IGNORE` contra el UNIQUE del spool_id)
    no la duplica. Solo si tampoco se pudo encolar cuenta en registros_perdidos
    (encolar_o_contar_perdida). Un fallo de la base (_ERRORES_DE_BASE) o el tope
    NO cambian la salud; un bug nuestro (AttributeError, TypeError) sube. La
    cancelacion de afuera no se traga: asyncio.timeout solo convierte SU
    vencimiento en TimeoutError, y esta funcion corre blindada (ver
    _registrar_blindado), asi que ni el tope de faceta ni el del barrido la
    cancelan."""
    spool_id = str(uuid.uuid4())
    costo = None
    try:
        async with asyncio.timeout(CANARY_USAGE_TIMEOUT_SECONDS):
            costo = await calcular_costo(
                usage.provider_id, usage.model, usage.tokens_in, usage.tokens_out)
            await record_usage(None, None, facet, usage.provider_id,
                               usage.model, usage.tokens_in,
                               usage.tokens_out, REQUEST_TYPE_CANARIO,
                               cost_usd_override=costo, spool_id=spool_id)
    except _ERRORES_DE_BASE as e:  # fail-soft: la contabilidad no es la salud; el tope o la base caida no convierten una sonda sana en fallida, y la fila pagada se encola (o se cuenta perdida) en vez de desaparecer
        logger.warning(
            "facet_canary: el registro de uso de la sonda de %s (%s) no "
            "termino (%s): se encola en el respaldo durable", facet, source,
            type(e).__name__, exc_info=True)
        await encolar_o_contar_perdida(
            None, None, facet, usage.model, usage.tokens_in, usage.tokens_out,
            costo, REQUEST_TYPE_CANARIO,
            f"{type(e).__name__}: el registro de uso de la sonda no termino",
            spool_id)


async def _sondear_con_tope(facet: str, config: dict, source: str) -> str | None:
    """_sondear con el tope por faceta (el registro de uso va DESPUES, fuera del tope). Si lo excede: WARNING y OUTCOME_PROBE_ERROR
    como valor de retorno, y escribe UNA fila probe_error "tope de faceta" (la
    cancelacion no pasa por el `except Exception` de _invoke_facet, asi que si
    no la escribe esta funcion no la escribe nadie). El barrido sigue con las
    demas."""
    try:
        async with asyncio.timeout(CANARY_FACET_TIMEOUT_SECONDS):
            resultado, usage = await _sondear(facet, config, source)
    except TimeoutError:
        logger.warning(
            "facet_canary: la sonda de %s (%s) excedio su tope de %ss y se "
            "corto; el barrido sigue con las demas", facet, source,
            CANARY_FACET_TIMEOUT_SECONDS)
        # La cancelacion no pasa por el `except Exception` de _invoke_facet, asi
        # que nadie escribio: sin esta fila el reaper veria `unknown` (sin causa)
        # y no `down`. Igual que probe_after_rebind. Con tope de base propio:
        # una MariaDB colgada no puede colgar el barrido por la puerta de atras.
        try:
            async with asyncio.timeout(CANARY_DB_TIMEOUT_SECONDS):
                await record_facet_health(
                    facet, OUTCOME_PROBE_ERROR, source, "tope de faceta")
        except TimeoutError:
            logger.warning(
                "facet_canary: no se pudo registrar el tope de faceta de %s "
                "(la base no contesto en %ss)", facet, CANARY_DB_TIMEOUT_SECONDS)
        return OUTCOME_PROBE_ERROR
    # DESPUES de cerrar el tope de salud, a proposito: el registro de uso no es
    # parte de la salud y no puede consumir ni heredar su tope (MAJOR-1 de la
    # auditoria de #212). Blindado: tampoco lo corta el tope del barrido.
    if usage is not None:
        await _registrar_blindado(facet, source, usage)
    return resultado


async def _reintentar_diferidas(config: dict, facets: list[str],
                                resultados: list, source: str) -> None:
    """Reintenta, cada CANARY_RETRY_SECONDS y mientras siga la mision, las
    facetas que el barrido saltó por mision en curso. Actualiza `resultados`.

    Diferir y no descartar: el lector da `unknown` a las 2 h sin eventos y no
    hay margen; una sonda saltada que no se recupera deja un unknown falso (la
    cuenta del hueco esta junto a las constantes). Tope propio
    CANARY_DEFERRED_MAX_SECONDS, separado del del barrido. Si se agota con la
    mision aun en curso: WARNING y para."""
    pendientes = [i for i, r in enumerate(resultados) if r == SALTADA_POR_MISION]
    if source != SOURCE_CANARY_PERIODIC or not pendientes:
        return
    desde = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        async with asyncio.timeout(CANARY_DEFERRED_MAX_SECONDS):
            while pendientes:
                await asyncio.sleep(CANARY_RETRY_SECONDS)
                if await _mision_en_curso():
                    continue
                for i in list(pendientes):
                    resultados[i] = await _sondear_con_tope(facets[i], config, source)
                    if resultados[i] != SALTADA_POR_MISION:
                        pendientes.remove(i)
    except TimeoutError:
        logger.warning(
            "facet_canary: sonda diferida agotada: mision en curso desde %s; "
            "sin sondear tras %ss: %s", desde, CANARY_DEFERRED_MAX_SECONDS,
            ", ".join(facets[i] for i in pendientes))


async def probe_all(source: str = SOURCE_CANARY_PERIODIC) -> list[str | None]:
    config = _load_config()
    async with asyncio.timeout(CANARY_SWEEP_TIMEOUT_SECONDS):
        facets = await canary_facets(config)
        resultados = []
        for f in facets:
            resultados.append(await _sondear_con_tope(f, config, source))
    await _reintentar_diferidas(config, facets, resultados, source)
    return resultados


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
            texto_de_error(e))
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
            # Hallazgo 2: los topes (por faceta y del barrido) viven en
            # probe_all: un colgado en resolve_facet() (aiomysql sin
            # connect_timeout) ya no mata el barrido en silencio.
            await probe_all(SOURCE_CANARY_PERIODIC)
        except Exception:  # fail-soft: loop en background, mismo patron que los demas loops de fondo -- nunca debe tumbar el proceso, el proximo ciclo reintenta. Incluye el TimeoutError del tope del barrido.
            logger.warning("facet_canary: barrido fallo", exc_info=True)
        await asyncio.sleep(CANARY_INTERVAL_SECONDS)
