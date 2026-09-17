"""ProcessPoolExecutor acotado para pypdf (RD1, 2026-09-17; ronda de
corrección 1 el mismo día, sobre 1834fa3).

pypdf es CPU-bound y retiene el GIL incluso corriendo en un hilo
(asyncio.to_thread no alcanza: medido en Task 11 -- health p95 244 ms con
subidas de PDF a c=25, contra 0,3 ms sin carga). Un proceso aparte no le
disputa el GIL al proceso web: ni un PDF normal ni uno patológico bloquean
jamas el event loop que atiende /api/health o cualquier otra request.

El pool lo crea y cierra el lifespan de main.py (JAX_ADJUNTO_PDF_PROCESOS,
fail-closed: sin ese entero 1..LIMITE_PROCESOS_DE_PDF el servicio no
arranca). También se crea solo, de forma perezosa, si algo llama a
`extraer_texto_en_pool` sin que el lifespan haya corrido (los tests unitarios
de este módulo, por ejemplo) -- las dos formas son válidas, ver el brief.

QUÉ PUEDE ROMPER UN POOL Y CÓMO SE MAPEA (todo al mismo código estable
`pdf_ilegible`, sin agregar uno nuevo):

1. TIMEOUT (JAX_ADJUNTO_PDF_TIMEOUT_SEGUNDOS): `asyncio.wait_for` corta la
   espera. concurrent.futures.ProcessPoolExecutor NO tiene forma de
   interrumpir una tarea que ya empezó a correr en un worker
   (`Future.cancel()` no hace nada si ya está RUNNING) -- así que no alcanza
   con "dejarlo correr en el fondo": hay que matar el pool entero (`_matar_pool`)
   y levantar uno nuevo (`_reciclar`).
2. WORKER MUERTO SIN AVISAR (OOM, segfault de pypdf, `os._exit`): el
   ProcessPoolExecutor lo detecta solo y marca TODO el pool `BrokenProcessPool`
   -- cualquier submit posterior, y cualquier future que estuviera corriendo
   o en cola en ese momento, sale con esa excepción. Se mapea igual que un
   timeout (mismo `_reciclar`).
3. CANCELACIÓN QUE SE ORIGINA EN EL POOL, NO EN EL REQUEST: cuando UNA
   extracción recicla el pool (por 1 o 2), CUALQUIER OTRA que estuviera en
   cola en ese mismo pool (todavía no despachada a un worker) puede salir
   con `CancelledError` en vez de `BrokenProcessPool` (variable según timing;
   ver el fix report). Point importante: una cancelación REAL de este
   request -- alguien llamó `.cancel()` sobre ESTA tarea, p. ej. el cliente
   se desconectó -- tiene que seguir siendo `CancelledError` de verdad, no
   convertirse en `pdf_ilegible`. Se distinguen con `Task.cancelling()`
   (Python 3.11+): >0 sólo cuando ALGUIEN pidió cancelar ESTA tarea
   puntualmente, no cuando lo que se canceló es el future que esta tarea
   esperaba por otra razón (ver `extraer_texto_en_pool`).

MATAR UN POOL, SIN EL ATRIBUTO PRIVADO `_processes` (ronda de corrección 1,
item 3): Python 3.14 agregó `ProcessPoolExecutor.terminate_workers()`
(SIGTERM a todos) y `.kill_workers()` (SIGKILL a los que sigan vivos), las
dos API pública. `_matar_pool` llama las DOS siempre, sin sondear ni esperar
entre una y otra: si `terminate_workers()` ya los mató, `kill_workers()` no
encuentra a nadie vivo y no hace nada (así lo dice su propio docstring); si
alguno ignora SIGTERM, lo remata en el mismo tick. Nunca
`shutdown(wait=True)`: eso bloquearía detrás de un worker que ignora señales
-- con las dos llamadas de arriba ya no hay nadie vivo detrás de quien
esperar, así que el `shutdown` final es `wait=False`.

`mp_context="spawn"` a propósito, no el `fork` por default de Linux: el pool
se crea ANTES de abrir la base y el cliente HTTP (ver main.py), así que hoy
no habría fds ni conexiones raras que un fork heredaría de todos modos --
pero spawn no depende de que eso siga siendo cierto: un worker nuevo arranca
un intérprete de Python limpio que solo importa lo que su función necesita
(adjuntos.pdf, es decir pypdf), nunca el estado de la app (sin DB, sin
FastAPI, sin loop) -- ver la nota del brief sobre el worker.

PRECALENTADO (ronda de corrección 1, item 5): `crear_pool()` no arranca
procesos reales -- ProcessPoolExecutor los arranca perezosamente recién
cuando le llega el primer trabajo. Sin precalentar, el PRIMER timeout real
de cada worker incluiría el costo fijo de spawn (arrancar un intérprete de
Python) + importar pypdf, no solo el trabajo de pypdf. `precalentar_pool()`
manda una tarea trivial por cada worker configurado y la espera con un
límite acotado (best-effort, no fail-closed: si tarda o falla, el arranque
del servicio sigue igual -- es una optimización, no un contrato)."""
import asyncio
import logging
import multiprocessing
import weakref
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool

from adjuntos import limites
from adjuntos.pdf import PdfIlegible, extraer_texto

logger = logging.getLogger(__name__)

_MP_CONTEXT = multiprocessing.get_context("spawn")

_pool: ProcessPoolExecutor | None = None

# Un asyncio.Lock por event loop (no uno solo a nivel de módulo): desde
# Python 3.10 el Lock no exige un loop corriendo al crearse, pero SÍ se ata
# al primer loop donde se usa y explota si un loop DISTINTO lo vuelve a usar
# después -- exactamente lo que pasa acá si dos tests llaman asyncio.run()
# por separado (cada uno abre un loop nuevo) contra este mismo módulo
# (mismo patrón que adjuntos/turno.py, mismo motivo).
_LocksPorLoop = "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock]"
_locks_por_loop: _LocksPorLoop = weakref.WeakKeyDictionary()


def _lock_de_reciclado() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    lock = _locks_por_loop.get(loop)
    if lock is None:
        lock = _locks_por_loop[loop] = asyncio.Lock()
    return lock


def _precalentar() -> None:
    """Tarea trivial de precalentado (item 5): fuerza, en un worker recién
    arrancado, el import de `adjuntos.pdf` (que a su vez importa pypdf a
    nivel de módulo) antes de que llegue la primera extracción real."""
    import adjuntos.pdf  # noqa: F401


def crear_pool() -> ProcessPoolExecutor:
    """Crea (o reemplaza) el pool global. Lo llama el lifespan al arrancar
    (después de validar JAX_ADJUNTO_PDF_PROCESOS) y `_reciclar` tras matar un
    pool colgado/roto. Síncrona y sin bloquear: ProcessPoolExecutor no
    arranca procesos reales en el constructor, los arranca de a uno según
    hace falta cuando se le manda trabajo (submit/run_in_executor) -- por
    eso `precalentar_pool()` existe aparte."""
    global _pool
    tamano = limites.cargar_procesos_de_pdf()
    _pool = ProcessPoolExecutor(max_workers=tamano, mp_context=_MP_CONTEXT)
    return _pool


async def precalentar_pool() -> None:
    """Manda una tarea trivial por cada worker configurado para pagar de
    antemano el costo fijo de un worker (spawn del intérprete + import de
    pypdf), así el timeout de la primera extracción real no lo incluye.
    Sin pool creado, no hace nada. Acotada (10 s) y best-effort: a
    diferencia de los límites fail-closed de arriba, un precalentado que
    tarda o falla no tumba el arranque del servicio -- la primera extracción
    real simplemente paga lo que esto no llegó a adelantar, y CUALQUIER
    fallo real de un worker se va a notar ahí, con el mapeo a pdf_ilegible
    de siempre."""
    pool = _pool
    if pool is None:
        return
    tamano = limites.cargar_procesos_de_pdf()
    loop = asyncio.get_running_loop()
    futuros = [loop.run_in_executor(pool, _precalentar) for _ in range(tamano)]
    try:
        await asyncio.wait_for(asyncio.gather(*futuros), timeout=10)
    except TimeoutError:
        logger.warning(
            "precalentar_pool: %s worker(s) no terminaron de precalentar en 10 s, "
            "el arranque sigue igual", tamano)
    except Exception:  # fail-soft: precalentar es una optimización (ver docstring), no un contrato -- un fallo acá no debe tumbar el arranque del servicio; un worker realmente roto se va a notar en la primera extraccion real, con el mapeo a pdf_ilegible de siempre
        logger.warning("precalentar_pool: fallo inesperado, el arranque sigue igual", exc_info=True)


async def _matar_pool(pool_muerto: ProcessPoolExecutor) -> None:
    """Termina TODOS los workers de `pool_muerto` con las API públicas de
    Python 3.14 (ronda de corrección 1, item 3 -- ya no se toca el atributo
    privado `_processes`). Ver el docstring del módulo para el por qué de
    llamar las dos siempre y de terminar con `shutdown(wait=False)`."""
    def _sincrono():
        pool_muerto.terminate_workers()
        pool_muerto.kill_workers()
        pool_muerto.shutdown(wait=False, cancel_futures=True)
    await asyncio.to_thread(_sincrono)


async def cerrar_pool() -> None:
    """Apagado del lifespan (main.py, shutdown). Toma el MISMO lock que
    `_reciclar` (item 4): un shutdown que compite con un reciclado no puede
    dejar un executor huérfano sin apagar ni esperar sin cota detrás de un
    worker colgado -- o este shutdown mata el pool viejo ENTERO antes de que
    el reciclado llegue a crear uno nuevo (y no queda nada corriendo), o el
    reciclado termina primero y este shutdown mata el pool YA reciclado.
    Sin pool creado, no hace nada -- no es fail-open: no hay nada que
    cerrar."""
    global _pool
    async with _lock_de_reciclado():
        pool, _pool = _pool, None
        if pool is not None:
            await _matar_pool(pool)


async def _pool_actual() -> ProcessPoolExecutor:
    """Creación perezosa, protegida contra la carrera con un reciclado que
    esté a mitad de camino (ver `_reciclar`): sin el lock, una request nueva
    que llega mientras `_pool` está en None (durante el apagado del pool
    colgado) podría crear SU PROPIO pool nuevo, que el reciclado pisaría sin
    apagar al terminar -- un ProcessPoolExecutor perdido (fuga, aunque
    acotada: sin submits nunca llegó a levantar procesos reales)."""
    if _pool is not None:
        return _pool
    async with _lock_de_reciclado():
        if _pool is None:
            crear_pool()
        return _pool


async def _reciclar(pool_sospechoso: ProcessPoolExecutor) -> None:
    """Descarta `pool_sospechoso` y levanta un pool nuevo en su lugar --
    ante un timeout, un BrokenProcessPool (un worker murió de verdad) o una
    cancelación que se originó en el pool mismo (ver el docstring del
    módulo). La guarda evita que dos llamadas casi simultáneas reciclen dos
    veces: la primera en tomar el lock gana, pone `_pool` en el pool nuevo,
    y la segunda, al entrar, ve que el pool global ya no es el que ella vio
    sospechoso y no hace nada (ver
    test_dos_timeouts_simultaneos_no_duplican_el_reciclado)."""
    async with _lock_de_reciclado():
        if _pool is not pool_sospechoso:
            return  # otra llamada ya reciclo por este mismo pool
        await _matar_pool(pool_sospechoso)
        crear_pool()


async def extraer_texto_en_pool(
    datos: bytes, max_paginas: int, max_chars: int
) -> tuple[str, bool]:
    """Corre `adjuntos.pdf.extraer_texto` (pypdf) en el ProcessPoolExecutor,
    con timeout (JAX_ADJUNTO_PDF_TIMEOUT_SEGUNDOS). El límite de páginas y de
    caracteres los sigue aplicando `extraer_texto` DENTRO del worker -- este
    módulo solo decide dónde corre y cuánto tiempo se le da.

    Timeout, worker muerto (BrokenProcessPool) y cancelación inducida por el
    pool se mapean los tres al mismo PdfIlegible que ya usa el endpoint para
    "PDF ilegible" (código estable `pdf_ilegible`, sin agregar uno nuevo) y
    disparan el reciclado del pool. Una cancelación REAL de este request
    (alguien llamó `.cancel()` sobre esta tarea puntualmente) se relanza tal
    cual -- no se recicla el pool por eso: no hay que castigar a las demás
    extracciones en vuelo por un client que se desconectó (ver el fix
    report, concern del worker que queda colgado en ese caso)."""
    timeout = limites.cargar_timeout_de_pdf()
    loop = asyncio.get_running_loop()
    pool = await _pool_actual()
    future = loop.run_in_executor(pool, extraer_texto, datos, max_paginas, max_chars)
    try:
        return await asyncio.wait_for(future, timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning(
            "extraccion de pdf: timeout a los %s s, reciclando el ProcessPoolExecutor",
            timeout,
        )
        await _reciclar(pool)
        raise PdfIlegible("timeout") from None
    except BrokenProcessPool:
        logger.warning(
            "extraccion de pdf: pool roto (un worker murió sin avisar), reciclando")
        await _reciclar(pool)
        raise PdfIlegible("pool_roto") from None
    except asyncio.CancelledError:
        tarea = asyncio.current_task()
        if tarea is not None and tarea.cancelling() > 0:
            raise  # cancelacion real de ESTE request -- no tocar el pool
        logger.warning(
            "extraccion de pdf: cancelada por un reciclado ajeno (pool roto o "
            "recién reciclado), mapeando igual")
        await _reciclar(pool)
        raise PdfIlegible("pool_reciclado") from None
