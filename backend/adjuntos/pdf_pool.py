"""ProcessPoolExecutor acotado para pypdf (RD1, 2026-09-17).

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

TIMEOUT Y RECICLADO: cada extracción tiene un techo
(JAX_ADJUNTO_PDF_TIMEOUT_SEGUNDOS, fail-closed igual que el tamaño del pool).
concurrent.futures.ProcessPoolExecutor NO tiene forma de interrumpir una
tarea que ya empezó a correr en un worker: cancelar el Future de
`asyncio.wait_for` cuando se agota el timeout solo cancela tareas que
TODAVÍA no arrancaron (concurrent.futures.Future.cancel() devuelve False si
ya está RUNNING, y no hace nada). Por eso, ante un timeout, no alcanza con
"dejarlo correr en el fondo": se mata TODO el pool viejo (terminate() de cada
proceso, vía el atributo privado `_processes` -- no hay API pública para
matar un worker individual; ver el aviso en `_reciclar_tras_timeout`) y se
arranca uno nuevo en su lugar. Cualquier otra extracción que estuviera en
vuelo en el pool viejo en ese instante también se pierde (BrokenProcessPool
o CancelledError) -- aceptable: un timeout es un PDF patológico, no el
camino normal, y el pool nuevo queda disponible enseguida para lo que venga
después.

`mp_context="spawn"` a propósito, no el `fork` por default de Linux: el pool
se crea ANTES de abrir la base y el cliente HTTP (ver main.py), así que hoy
no habría fds ni conexiones raras que un fork heredaría de todos modos --
pero spawn no depende de que eso siga siendo cierto: un worker nuevo arranca
un intérprete de Python limpio que solo importa lo que su función necesita
(adjuntos.pdf, es decir pypdf), nunca el estado de la app (sin DB, sin
FastAPI, sin loop) -- ver la nota del brief sobre el worker."""
import asyncio
import logging
import multiprocessing
import weakref
from concurrent.futures import ProcessPoolExecutor

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


def crear_pool() -> ProcessPoolExecutor:
    """Crea (o reemplaza) el pool global. Lo llama el lifespan al arrancar
    (después de validar JAX_ADJUNTO_PDF_PROCESOS) y `_reciclar_tras_timeout`
    tras matar un pool colgado. Síncrona y sin bloquear: ProcessPoolExecutor
    no arranca procesos reales en el constructor, los arranca de a uno según
    hace falta cuando se le manda trabajo (submit/run_in_executor)."""
    global _pool
    tamano = limites.cargar_procesos_de_pdf()
    _pool = ProcessPoolExecutor(max_workers=tamano, mp_context=_MP_CONTEXT)
    return _pool


async def cerrar_pool() -> None:
    """Apagado del lifespan (main.py, shutdown). Sin pool creado (un proceso
    que nunca recibió un PDF, o un test que no llamó crear_pool), no hace
    nada -- no es fail-open: no hay nada que cerrar."""
    global _pool
    pool, _pool = _pool, None
    if pool is not None:
        await asyncio.to_thread(pool.shutdown, wait=True, cancel_futures=True)


async def _pool_actual() -> ProcessPoolExecutor:
    """Creación perezosa, protegida contra la carrera con un reciclado que
    esté a mitad de camino (ver `_reciclar_tras_timeout`): sin el lock, una
    request nueva que llega mientras `_pool` está en None (durante el
    apagado del pool colgado) podría crear SU PROPIO pool nuevo, que el
    reciclado pisaría sin apagar al terminar -- un ProcessPoolExecutor
    perdido (fuga, aunque acotada: sin submits nunca llegó a levantar
    procesos reales)."""
    if _pool is not None:
        return _pool
    async with _lock_de_reciclado():
        if _pool is None:
            crear_pool()
        return _pool


async def _reciclar_tras_timeout(pool_colgado: ProcessPoolExecutor) -> None:
    """Mata TODOS los procesos del pool que tuvo un timeout y levanta uno
    nuevo en su lugar (ver el docstring del módulo -- no hay forma de matar
    solo el worker que se colgó).

    `pool_colgado._processes` es un atributo PRIVADO de
    concurrent.futures.process (dict[pid, multiprocessing.Process]); no
    existe una API pública para terminar un worker puntual de un
    ProcessPoolExecutor. Documentado a proposito: si una version futura de
    Python le cambia el nombre o la forma, este reciclado deja de matar
    procesos en silencio (el `.terminate()` de una lista vacia no falla) y
    hay que revisar esta funcion primero -- por eso el pool CREADO despues
    (uno nuevo, limpio) es la garantia real de que la proxima extraccion
    tiene un lugar libre, no que el viejo haya muerto de verdad.

    La guarda `_pool is not pool_colgado` (dentro del lock) evita que dos
    timeouts casi simultáneos reciclen dos veces: el primero en tomar el
    lock gana, pone `_pool` en el pool nuevo, y el segundo, al entrar, ve que
    el pool global ya no es el que él vio colgado y no hace nada (ver
    test_dos_timeouts_simultaneos_no_duplican_el_reciclado)."""
    async with _lock_de_reciclado():
        if _pool is not pool_colgado:
            return  # otro timeout ya reciclo por este mismo pool

        def _matar_y_cerrar():
            for proceso in list(pool_colgado._processes.values()):
                proceso.terminate()
            pool_colgado.shutdown(wait=True, cancel_futures=True)

        await asyncio.to_thread(_matar_y_cerrar)
        crear_pool()


async def extraer_texto_en_pool(
    datos: bytes, max_paginas: int, max_chars: int
) -> tuple[str, bool]:
    """Corre `adjuntos.pdf.extraer_texto` (pypdf) en el ProcessPoolExecutor,
    con timeout (JAX_ADJUNTO_PDF_TIMEOUT_SEGUNDOS). El límite de páginas y de
    caracteres los sigue aplicando `extraer_texto` DENTRO del worker -- este
    módulo solo decide dónde corre y cuánto tiempo se le da.

    Un timeout se mapea al mismo PdfIlegible que ya usa el endpoint para
    "PDF ilegible" (código estable `pdf_ilegible`, sin agregar un código
    nuevo) y dispara el reciclado del pool antes de propagar el error."""
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
        await _reciclar_tras_timeout(pool)
        raise PdfIlegible("timeout") from None
