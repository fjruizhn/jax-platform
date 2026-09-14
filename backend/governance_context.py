"""
Contexto de gobernanza compartido (REFORMAS Fase 2 SP3).

Antes vivía como shadow_validation._validation_context(). Se mueve acá
porque chat.py también lo necesita (para construir el snapshot del turno,
spec §5.1) y chat.py NO puede importar shadow_validation a nivel de módulo:
shadow_validation importa `from api.chat import ContractResult` -- ciclo.

Config estática cacheada por proceso, con REVALIDACIÓN POR mtime (2026-09-03).

Por qué la revalidación, medido y no supuesto: hasta hoy este módulo cacheaba
con `lru_cache(maxsize=1)` y nada más, así que un cambio en disco solo entraba
al reiniciar el proceso. Eso tenía un modo de falla que no era "config vieja":
si `las_manos/config.toml` se volvía ILEGIBLE con el proceso ya caliente, no
pasaba absolutamente nada visible. El contexto cacheado seguía sirviendo, el
snapshot se construía con hechos que ya no estaban en disco, se inyectaba en el
prompt, y los claims que lo citaban se acreditaban como OBSERVADO. **El snapshot
mentía y ninguna de las dos capas podía notarlo, porque las dos leen este mismo
objeto.** Con la caché fría (primer turno tras un reinicio) el mismo daño era
ruidoso: sha256='ERROR', validated_at NULL y cero veredictos. La revalidación
convierte el caso caliente en el caso frío: fail-closed visible en vez de
silencio.

Cómo: un `stat()` por construcción sobre los TRES archivos que alimentan el
contexto -- medidos con audit hook sobre `open`, no supuestos:

  * `<JAX_REPO>/las_manos/config.toml`  (lo lee load_validation_context en cada
    llamada; es el único que sigue a JAX_REPO)
  * `policy/vocabulary/predicates.yaml` y `closed_vocabulary.yaml`, cuyas rutas
    resuelve `loaders` desde su propio `__file__` y NO siguen a JAX_REPO -- por
    eso se toman de las constantes del módulo, para que no se despeguen si
    alguna cambia de lugar.

El mtime va en la CLAVE de la caché: si cambió, es un miss y se reconstruye; si
no, es el mismo hit de siempre. Costo medido: 2,29 us las tres llamadas a
stat(), contra 9,77 us que cuesta hoy `_build_grounding()` por turno y 2,65 ms
la reconstrucción completa cuando hace falta. No se relee ningún archivo para
decidir. Hash en vez de mtime solo si alguna vez se mide que el mtime miente en
este entorno.

Consecuencia deliberada, y corrige lo que este docstring afirmaba antes: el
spec §9.4 daba por diferida la propiedad de dos capas porque el snapshot y el
resolver leían un objeto congelado, y de ahí que FACT_MISMATCH no pudiera
dispararse por drift. Con revalidación SÍ puede: si la config cambia entre el
turno (donde se arma el snapshot) y la background task (donde se resuelve), el
claim acreditado contra el snapshot viejo se resuelve contra el contexto nuevo
y sale FACT_MISMATCH. Eso es exactamente lo que el spec §4.3 quiere de las dos
capas: que sean independientes.

Catálogo de la DB (tanda A v2, 2026-09-14). Desde el Bloque 3 las
capabilities viven en la DB; hasta hoy este contexto las armaba del TOML
vacío. Ahora:

  * `validation_context()` es ASYNC: el catálogo sale de
    `await MotorCatalog.from_db()` (aiomysql), con capability.mode. Los
    YAML/TOML se leen en `asyncio.to_thread`: nada bloqueante en el turno.
  * El MISMO contexto alimenta el snapshot del prompt (api/chat.py) y la
    validación en sombra (shadow_validation.py): lo que se inyecta es lo que
    se verifica.
  * CLAVE DE CACHÉ = mtimes de los tres archivos + mtime del sello de
    facet_resolver (`_seal_mtime()`). INVALIDACIÓN DECLARADA: el sello, que
    estampan tras commitear las migraciones y el admin de motores/
    capabilities (y los rebinds de facets). Si la clave cambió, recarga; si
    no, el mismo objeto. La clave se toma ANTES de consultar: un sello
    estampado con la consulta en vuelo deja la clave guardada vieja y el
    próximo turno recarga (mismo criterio que LAS MANOS,
    motor_registry/routes.py::_load_catalog).
  * Sello ausente o ilegible: `_seal_mtime()` da None = "sin señal", nunca
    "invalidar" (contrato de facet_resolver). La clave queda estable y los
    cambios del catálogo entran al reiniciar. En hall9000 el sello existe
    (/srv/jax-data/facet-cache-seal, verificado 2026-09-14); el despliegue lo
    vuelve a verificar.
  * UNA recarga en vuelo, COMPARTIDA (ronda de arreglo 1): la tarea de
    recarga se guarda junto con el stamp para el que se lanzó. Un turno que
    encuentra una en vuelo para el MISMO stamp la espera y recibe su mismo
    resultado o su misma excepción: con la DB caída, N turnos a la vez son
    UN intento, no N en serie. Terminada (bien o mal) se limpia el "en
    vuelo"; si falló, `_cache` no cambia y el turno SIGUIENTE reintenta.
    Cancelación: cada turno espera con `asyncio.shield`, así que cancelar el
    turno que la lanzó no cancela la recarga de los demás.
  * LÍMITE DE TIEMPO: la recarga completa (from_db + el to_thread) va bajo
    `asyncio.wait_for(..., GOVERNANCE_RELOAD_TIMEOUT_SECONDS)`, variable de
    entorno, default 5.0 s. Por qué 5: la recarga completa en frío mide
    p95 2,7 ms (2026-09-14), así que 5 s son más de mil veces el caso normal
    y no cortan una DB lenta pero viva; y acota lo que un turno puede quedar
    colgado si la DB acepta TCP y no contesta (aiomysql.connect no trae
    connect_timeout por defecto). Vencida: TimeoutError visible (en el chat
    SnapshotError; en sombra, sin veredictos) y caché intacto. El hilo de
    `to_thread` no se puede cancelar: si ya arrancó, termina de leer los
    YAML/TOML y su resultado se descarta.
  * FALLA VISIBLE: si from_db() lanza, la excepción sube y la caché queda
    como estaba (no se sirve: la clave ya no coincide). En el chat,
    `_build_grounding` la convierte en SnapshotError
    (grounding_snapshot_sha256='ERROR'); la validación en sombra no escribe
    veredictos. Servir un catálogo vacío o viejo repetiría el falso negativo
    en silencio, o daría VALID a una capability revocada (P10).
  * Costo: un stat más por turno; la recarga solo cuando cambió algo.
    Latencia medida antes/después: DEUDA.md de jax.
  * Una recarga lanzada para un stamp que ya quedó viejo puede terminar
    después de otra más nueva y escribir `_cache` con SU stamp: no se sirve
    (la clave no coincide) y el turno siguiente recarga. Costo: una recarga
    de más, nunca un dato viejo servido.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

JAX_REPO = Path(os.getenv("JAX_REPO_PATH", os.path.expanduser("~/jax")))
if str(JAX_REPO) not in sys.path:
    sys.path.insert(0, str(JAX_REPO))
if str(JAX_REPO / "policy" / "governance") not in sys.path:
    sys.path.insert(0, str(JAX_REPO / "policy" / "governance"))

import facet_resolver  # noqa: E402  (su sello invalida también este caché)
import loaders as governance_loaders  # noqa: E402
import validator as governance_validator  # noqa: E402

# Límite de la recarga completa del contexto (from_db + to_thread). Por qué
# 5.0 s: ver el docstring del módulo. Se lee por nombre al lanzar cada
# recarga, así que un test puede reapuntarlo.
GOVERNANCE_RELOAD_TIMEOUT_SECONDS = float(os.getenv("GOVERNANCE_RELOAD_TIMEOUT_SECONDS", "5.0"))

_FileStamp = tuple[tuple[str, int | None], ...]
_Stamp = tuple[_FileStamp, float | None]


def _source_stamp() -> _FileStamp:
    """(ruta, mtime_ns) de los tres archivos fuente. Solo stat(): no abre nada.

    Un archivo que no se puede statear entra como None -- también invalida, y
    la reconstrucción falla ruidosamente, que es el comportamiento buscado
    (P10: ningún gate falla abierto). JAX_REPO se lee en cada llamada a
    propósito: los tests lo reapuntan.
    """
    rutas = (
        JAX_REPO / "las_manos" / "config.toml",
        governance_loaders.PREDICATES_FILE,
        governance_loaders.VOCABULARY_FILE,
    )
    marca: list[tuple[str, int | None]] = []
    for ruta in rutas:
        try:
            marca.append((str(ruta), os.stat(ruta).st_mtime_ns))
        except OSError:
            marca.append((str(ruta), None))
    return tuple(marca)


def _stamp() -> _Stamp:
    return (_source_stamp(), facet_resolver._seal_mtime())


_cache: tuple[_Stamp, tuple] | None = None
_inflight: tuple[_Stamp, asyncio.Future] | None = None


def _build_static(catalog):
    """La parte de disco (YAML/TOML): corre en un hilo, no en el loop."""
    vocabulary = governance_loaders.load_vocabulary()
    ctx = governance_validator.load_validation_context(JAX_REPO, vocabulary.config_paths, catalog)
    predicates = governance_loaders.load_predicates()
    return ctx, predicates, vocabulary.term_categories


async def _build():
    catalog = await governance_validator.MotorCatalog.from_db()
    return await asyncio.to_thread(_build_static, catalog)


async def _reload(stamp: _Stamp):
    """La recarga compartida: con límite de tiempo; si lanza, `_cache` no
    se toca y la excepción llega a TODOS los turnos que la esperan."""
    global _cache
    value = await asyncio.wait_for(_build(), timeout=GOVERNANCE_RELOAD_TIMEOUT_SECONDS)
    _cache = (stamp, value)
    return value


def _done(inflight: tuple[_Stamp, asyncio.Future]):
    def callback(task: asyncio.Future) -> None:
        global _inflight
        if _inflight is inflight:  # otra recarga más nueva pudo reemplazarla
            _inflight = None
        # Marca la excepción como recuperada: si todos los turnos que la
        # esperaban se cancelaron, asyncio no avisa "never retrieved". Los
        # que siguen esperando la reciben igual por su shield().
        if not task.cancelled():
            task.exception()
    return callback


async def validation_context():
    """(ValidationContext, predicates, term_categories) de gobernanza."""
    global _inflight
    stamp = _stamp()
    cached = _cache
    if cached is not None and cached[0] == stamp:
        return cached[1]
    inflight = _inflight
    if inflight is None or inflight[0] != stamp:
        inflight = (stamp, asyncio.ensure_future(_reload(stamp)))
        _inflight = inflight
        inflight[1].add_done_callback(_done(inflight))
    # shield: cancelar ESTE turno no cancela la recarga de los demás.
    return await asyncio.shield(inflight[1])


def _cache_clear() -> None:
    """Vacía la caché y olvida la recarga en vuelo (solo la usan los tests).

    Primitivo atado a un loop a nivel de módulo: `_inflight` guarda una
    tarea, que pertenece al loop donde se creó -- solo MIENTRAS está en
    vuelo; al terminar (bien, mal o cancelada al cerrarse su loop) el
    callback la limpia. Ya no hay `asyncio.Lock` de módulo (se ataba al
    loop de la primera contención). Un test que la deje en vuelo y abra
    otro loop sin llamar a esto esperaría una tarea de un loop ajeno; por
    eso los tests llaman a cache_clear() antes y después."""
    global _cache, _inflight
    _cache = None
    _inflight = None


# Compatibilidad: quien tenía `validation_context.cache_clear()` lo sigue
# teniendo (lo usan los tests para forzar una reconstrucción).
validation_context.cache_clear = _cache_clear
