"""ProcessPoolExecutor acotado para pypdf (RD1, 2026-09-17; ronda de
corrección 1 el mismo día sobre 1834fa3; ronda de corrección 2 sobre
1404ba7).

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

0. TURNO (Final fix wave #2, I1): `adjuntos.turno.turno_de_pdf()`, un
   semáforo del tamaño del pool, se toma ANTES del submit. Nunca hay más
   extracciones enviadas que workers, así que ninguna espera en la cola
   interna del executor y el timeout de abajo mide solo la corrida. Medido
   por el revisor antes del arreglo: 1 worker, timeout 2 s, dos extracciones
   válidas de 1,5 s a la vez -> la segunda salía `pdf_ilegible:timeout` y el
   reciclado mataba a la primera. Lo que sigue pudiendo pasar: que el timeout
   de una extracción patológica recicle el pool y alcance a otras EN VUELO en
   el mismo pool (punto 5); y que la primera extracción tras un reciclado
   espere el spawn que el precalentado de fondo no llegó a adelantar.
   Ruling R34: la espera de ese semáforo tiene plazo, el mismo
   JAX_ADJUNTO_PDF_TIMEOUT_SEGUNDOS (a lo sumo una corrida entera); vencido,
   `TurnoDePdfSinLugar` = 503 `adjuntos_reintentar`, sin tomar lugar.
1. TIMEOUT (JAX_ADJUNTO_PDF_TIMEOUT_SEGUNDOS): `asyncio.wait_for` corta la
   espera. concurrent.futures.ProcessPoolExecutor NO tiene forma de
   interrumpir una tarea que ya empezó a correr en un worker
   (`Future.cancel()` no hace nada si ya está RUNNING) -- así que no alcanza
   con "dejarlo correr en el fondo": hay que matar el pool entero (`_matar_pool`)
   y levantar uno nuevo (`_reciclar`).
2. WORKER MUERTO SIN AVISAR (OOM, segfault de pypdf, `os._exit`): el
   ProcessPoolExecutor lo detecta solo (un hilo de gestión interno) y marca
   TODO el pool roto (`_broken`) -- cualquier submit posterior, y cualquier
   future que estuviera corriendo o en cola en ese momento, sale con
   `BrokenProcessPool`. Se mapea igual que un timeout.
3. NADIE ESPERÓ EL FUTURE QUE MURIÓ (ronda de corrección 2, hallazgo NUEVO):
   si a un request lo cancelan (el worker sigue corriendo) y ESE worker
   muere un poco después, no queda NADIE despierto para notar la muerte y
   disparar `_reciclar` -- sin nada más, el pool queda roto PARA SIEMPRE
   (todo submit futuro revienta con `BrokenProcessPool`, un wedge
   permanente). `_pool_actual` chequea proactivamente, ANTES de devolver el
   pool a un request nuevo, si sigue sirviendo (`_esta_roto_o_cerrado`) y si
   no, lo recicla ahí mismo -- así el PRÓXIMO request (no uno que ya estaba
   en vuelo) repara el wedge solo.
4. SUBMIT SOBRE UN POOL ROTO/CERRADO (ronda de corrección 2, item 1):
   `loop.run_in_executor(pool, ...)` llama a `pool.submit(...)`
   SINCRÓNICAMENTE -- si el pool ya está roto o en shutdown, `submit()`
   revienta ANTES de devolver ningún future, con `BrokenProcessPool` o con
   un `RuntimeError('cannot schedule new futures after shutdown')` (no hay
   una excepción dedicada para esto último). El `try` de
   `extraer_texto_en_pool` cubre TAMBIÉN esa línea (antes quedaba afuera, y
   esos dos escapaban crudos como 500 durante la ventana de un reciclado en
   curso). El chequeo proactivo de `_pool_actual` (punto 3) reduce la
   ventana pero no la cierra del todo -- el hilo de gestión del executor
   puede marcar el pool roto en cualquier momento, en OTRO hilo, entre ese
   chequeo y el `submit()` real -- por eso el catch reactivo sigue
   haciendo falta como respaldo.
5. CANCELACIÓN QUE SE ORIGINA EN EL POOL, NO EN EL REQUEST: cuando UNA
   extracción recicla el pool (por 1, 2 o 4), CUALQUIER OTRA que estuviera
   en cola en ese mismo pool (todavía no despachada a un worker) puede
   salir con `CancelledError` en vez de `BrokenProcessPool` (variable según
   timing). Importante: una cancelación REAL de este request -- alguien
   llamó `.cancel()` sobre ESTA tarea, p. ej. el cliente se desconectó --
   tiene que seguir siendo `CancelledError` de verdad, no convertirse en
   `pdf_ilegible`, y NO dispara un reciclado por sí misma (no hay que
   castigar a otras extracciones en vuelo por un cliente ajeno que se fue).
   Final fix wave #2 (I1): el worker sigue ocupado, así que el lugar del
   turno de pdf queda tomado (`_vigilar_abandonada`) hasta que termine o
   venza SU presupuesto; si vence, se recicla como cualquier timeout. Antes
   ese worker "podía quedar corriendo hasta que algo MÁS lo note", y ese algo
   era la extracción siguiente vencida por esperar detrás. Se distinguen con `Task.cancelling()` (Python
   3.11+): >0 sólo cuando ALGUIEN pidió cancelar ESTA tarea puntualmente,
   no cuando lo que se canceló es el future que esta tarea esperaba por
   otra razón (ver `extraer_texto_en_pool`).

NI `_esta_roto_o_cerrado` NI EL `RuntimeError` DE SUBMIT TIENEN UNA SEÑAL
PÚBLICA: `ProcessPoolExecutor` no expone una propiedad "¿sigo sirviendo?" --
el propio `submit()` (ver `concurrent/futures/process.py` en la stdlib) lee
`self._broken`/`self._shutdown_thread` (privados) para decidir qué excepción
lanzar, y el mensaje del `RuntimeError` es el único identificador que existe
para ese caso. `_esta_roto_o_cerrado` es el ÚNICO lugar de este módulo que
toca esos atributos, con un test dedicado (`test_esta_roto_o_cerrado_...`) --
si una versión futura de Python les cambia el nombre, se rompe ahí primero,
no en silencio.

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

PRECALENTADO (ronda de corrección 1, item 5; ronda 2 lo extiende a
`_reciclar`): `crear_pool()` no arranca procesos reales -- ProcessPoolExecutor
los arranca perezosamente recién cuando le llega el primer trabajo. Sin
precalentar, el PRIMER timeout real de cada worker (en el arranque, o
después de CUALQUIER reciclado) incluiría el costo fijo de spawn + import de
pypdf. `precalentar_pool()` manda una tarea trivial por cada worker
configurado, acotada y best-effort (si tarda o falla, no tumba nada -- es
una optimización, no un contrato). `_reciclar` la dispara en BACKGROUND
(`asyncio.create_task`, sin awaitarla) después de soltar el lock de
reciclado -- si la esperara ADENTRO del lock, cualquier otra extracción que
necesite ese mismo lock (otro reciclado, `_pool_actual`) quedaría bloqueada
hasta 10 s en vez de la fracción de segundo que toma matar+crear (ver
`test_reciclar_no_bloquea_el_lock_durante_el_precalentado`).

CIERRE FAIL-CLOSED (ronda de corrección 2, item 4): `cerrar_pool()` (el
shutdown del lifespan) marca `_cerrado = True` ANTES de matar el pool.
Mientras `_cerrado`, ni `_pool_actual` ni `_reciclar` vuelven a crear un pool
-- un request que seguía en vuelo durante el shutdown no puede "revivir" un
pool que después nadie va a volver a cerrar. Ese request sale con
`pdf_ilegible` en vez de con un pool fantasma."""
import asyncio
import logging
import multiprocessing
import weakref
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool

from adjuntos import limites
from adjuntos.errores import AdjuntoRechazado
from adjuntos.pdf import PdfIlegible, extraer_texto
from adjuntos.turno import turno_de_pdf

logger = logging.getLogger(__name__)

_MP_CONTEXT = multiprocessing.get_context("spawn")

_pool: ProcessPoolExecutor | None = None

# Ronda de corrección 2, item 4: una vez cerrado, no se vuelve a crear nada.
# Reseteado a False sólo por los tests (ver conftest de este archivo); en
# producción un proceso corre el lifespan una sola vez.
_cerrado = False

# Precalentados lanzados en segundo plano por `_reciclar` (RD3, minor de
# RD1). asyncio guarda solo una referencia débil a cada tarea: sin esta
# referencia fuerte, una tarea puede desaparecer a mitad sin aviso. Cada una
# se quita sola al terminar (done-callback).
_tareas_de_precalentado: set = set()

# Vigilancias de extracciones abandonadas (Final fix wave #2, I1): mismo
# motivo que el set de arriba -- cada una retiene un lugar del turno de pdf y
# lo suelta al terminar; sin referencia fuerte podría desaparecer con el lugar
# tomado.
_tareas_de_vigilancia: set = set()

# Un asyncio.Lock por event loop (no uno solo a nivel de módulo): desde
# Python 3.10 el Lock no exige un loop corriendo al crearse, pero SÍ se ata
# al primer loop donde se usa y explota si un loop DISTINTO lo vuelve a usar
# después -- exactamente lo que pasa acá si dos tests llaman asyncio.run()
# por separado (cada uno abre un loop nuevo) contra este mismo módulo
# (mismo patrón que adjuntos/turno.py, mismo motivo).
_LocksPorLoop = "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock]"
_locks_por_loop: _LocksPorLoop = weakref.WeakKeyDictionary()


class TurnoDePdfSinLugar(AdjuntoRechazado):
    """Ruling R34 (2026-09-17): la espera del turno de pdf venció sin que se
    liberara un lugar. 503 `adjuntos_reintentar`: el PDF no es ilegible, el
    servidor está ocupado y el cliente reintenta. No se tomó nada."""

    def __init__(self):
        super().__init__(503, "adjuntos_reintentar")


async def _tomar_turno(turno: asyncio.Semaphore, plazo: float) -> None:
    """Toma un lugar del turno de pdf esperando a lo sumo `plazo` segundos
    (Ruling R34). Sin plazo, la cola del semáforo no tenía tope: Starlette no
    cancela el handler cuando el cliente se desconecta, así que un solo
    usuario (acotado sólo por su cuota ÷ max_bytes) podía encolar decenas de
    PDFs y dejar a TODOS los usuarios esperando detrás.

    `asyncio.wait_for` sobre `Semaphore.acquire()` no pierde lugares: si el
    vencimiento llega cuando el lugar ya se había concedido, `acquire()` lo
    devuelve antes de relanzar la cancelación (asyncio/locks.py). Una
    cancelación REAL de la tarea mientras espera sigue saliendo como
    `CancelledError` (asyncio.timeout distingue la suya de una ajena)."""
    try:
        await asyncio.wait_for(turno.acquire(), timeout=plazo)
    except TimeoutError:
        logger.warning(
            "extraccion de pdf: sin lugar en el pool tras esperar %s s, 503 adjuntos_reintentar",
            plazo)
        raise TurnoDePdfSinLugar() from None


def _lock_de_reciclado() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    lock = _locks_por_loop.get(loop)
    if lock is None:
        lock = _locks_por_loop[loop] = asyncio.Lock()
    return lock


def _esta_roto_o_cerrado(pool: ProcessPoolExecutor) -> bool:
    """¿Este executor ya no puede aceptar trabajo? No hay una propiedad
    pública para esto (ver el docstring del módulo) -- `submit()` mismo lee
    estos dos atributos privados para decidir si revienta. Único lugar del
    módulo que los toca; con test dedicado."""
    return bool(pool._broken) or pool._shutdown_thread


def _runtime_error_de_pool_cerrado(error: RuntimeError) -> bool:
    """El único mensaje que `ProcessPoolExecutor.submit()` usa para "no
    puedo aceptar más trabajo" (más allá de `BrokenProcessPool`) es un
    `RuntimeError` con este texto -- no hay una excepción dedicada (ver
    `concurrent/futures/process.py::submit`, citado en el fix report).
    Cualquier OTRO `RuntimeError` (de `extraer_texto`, de cualquier otra
    parte) tiene que seguir de largo, no convertirse en `pdf_ilegible`."""
    return "cannot schedule new futures" in str(error)


def _precalentar() -> None:
    """Tarea trivial de precalentado: fuerza, en un worker recién
    arrancado, el import de `adjuntos.pdf` (que a su vez importa pypdf a
    nivel de módulo) antes de que llegue la primera extracción real."""
    import adjuntos.pdf  # noqa: F401


def crear_pool() -> ProcessPoolExecutor:
    """Crea (o reemplaza) el pool global. Lo llama el lifespan al arrancar
    (después de validar JAX_ADJUNTO_PDF_PROCESOS) y `_reciclar`/`_pool_actual`
    tras matar un pool colgado/roto/cerrado. Síncrona y sin bloquear:
    ProcessPoolExecutor no arranca procesos reales en el constructor, los
    arranca de a uno según hace falta cuando se le manda trabajo
    (submit/run_in_executor) -- por eso `precalentar_pool()` existe aparte.

    NO chequea `_cerrado`: es una primitiva de bajo nivel, sin opinión sobre
    cuándo es válido llamarla -- esa decisión vive en `_pool_actual` y
    `_reciclar`, los únicos dos lugares que la invocan después del arranque."""
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
    try:
        # El submit va DENTRO del try (RD3, minor de RD1): run_in_executor
        # llama a pool.submit sincrónicamente y sobre un pool que se cerró o
        # rompió entre medio lanza RuntimeError/BrokenProcessPool ahí mismo.
        futuros = [loop.run_in_executor(pool, _precalentar) for _ in range(tamano)]
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

    Marca `_cerrado = True` (ronda de corrección 2, item 4) ANTES de matar:
    ningún reciclado ni ninguna creación perezosa posterior puede volver a
    levantar un pool que nadie va a cerrar de nuevo -- un request que sigue
    en vuelo cuando el lifespan está bajando sale con pdf_ilegible, no con
    un pool fantasma. Sin pool creado, no hace nada -- no es fail-open: no
    hay nada que cerrar."""
    global _pool, _cerrado
    async with _lock_de_reciclado():
        _cerrado = True
        pool, _pool = _pool, None
        if pool is not None:
            await _matar_pool(pool)


async def _pool_actual() -> ProcessPoolExecutor:
    """Devuelve un pool que sigue sirviendo -- crea uno si no hay
    (perezoso), o lo reemplaza si el que había quedó roto/cerrado por fuera
    de este módulo (ronda de corrección 2, item 1: el hallazgo del wedge
    permanente -- nadie esperó el future que mató al worker, así que nadie
    llamó a `_reciclar` por las buenas). El chequeo se hace ANTES de que
    `extraer_texto_en_pool` intente un submit que reventaría crudo.

    Fail-closed durante el shutdown (item 4): si `_cerrado`, no crea nada --
    levanta `PdfIlegible` directo, la misma familia de errores que ya usa
    el resto del módulo."""
    pool = _pool
    if pool is not None and not _esta_roto_o_cerrado(pool):
        return pool
    if pool is not None:
        # Roto/cerrado por fuera -- el mismo camino de matar+recrear que un
        # timeout, con la MISMA guarda contra dobles reciclados.
        await _reciclar(pool)
        if _pool is not None:
            return _pool
        raise PdfIlegible("cerrado")
    async with _lock_de_reciclado():
        if _cerrado:
            raise PdfIlegible("cerrado")
        if _pool is None:
            crear_pool()
        return _pool


async def _reciclar(pool_sospechoso: ProcessPoolExecutor) -> None:
    """Descarta `pool_sospechoso` y levanta un pool nuevo en su lugar --
    ante un timeout, un BrokenProcessPool (un worker murió de verdad), un
    RuntimeError de submit sobre un pool cerrado, o una cancelación que se
    originó en el pool mismo (ver el docstring del módulo). La guarda evita
    que dos llamadas casi simultáneas reciclen dos veces: la primera en
    tomar el lock gana, pone `_pool` en el pool nuevo, y la segunda, al
    entrar, ve que el pool global ya no es el que ella vio sospechoso y no
    hace nada (ver test_dos_timeouts_simultaneos_no_duplican_el_reciclado).

    Si `_cerrado` (el lifespan está bajando, ver `cerrar_pool`), mata el
    pool sospechoso IGUAL (no dejarlo colgado) pero NO crea uno nuevo --
    fail-closed, ver item 4.

    Si esta tarea se cancela MIENTRAS espera `_matar_pool` (ronda de
    corrección 2, item 3), ni `_pool = None` ni `crear_pool()` llegan a
    correr: `_pool` SIGUE apuntando al pool ya matado (corregido en RD3: este
    docstring decía que quedaba en `None`). Deliberado, sin `asyncio.shield`:
    ese pool está cerrado, así que `_esta_roto_o_cerrado` lo detecta y
    `_pool_actual` (arriba) se autocura sola en la SIGUIENTE llamada,
    sin necesitar que ESTA tarea cancelada termine su trabajo (ver
    test_cancelar_mientras_reciclar_espera_matar_pool_se_autocura)."""
    global _pool
    async with _lock_de_reciclado():
        if _pool is not pool_sospechoso:
            return  # otra llamada ya reciclo por este mismo pool
        await _matar_pool(pool_sospechoso)
        _pool = None
        if _cerrado:
            return  # apagado en curso -- no revivir el pool (item 4)
        crear_pool()
    # Fuera del lock (item 6): precalentar puede tardar hasta 10 s
    # (best-effort); adentro del lock bloquearía a cualquier otra extracción
    # o reciclado que necesite el mismo lock. En background: si otra
    # extracción llega antes de que termine, paga el costo de spawn que el
    # precalentado no llegó a adelantar -- ni mejor ni peor que sin
    # precalentado, nunca una regresión.
    tarea = asyncio.create_task(precalentar_pool())
    _tareas_de_precalentado.add(tarea)
    tarea.add_done_callback(_tareas_de_precalentado.discard)


async def _vigilar_abandonada(futuro, pool: ProcessPoolExecutor, turno: asyncio.Semaphore,
                              vence: float) -> None:
    """Final fix wave #2 (I1): el request de `futuro` se canceló (el cliente se
    fue) con su PDF YA corriendo en un worker. El lugar del turno sigue tomado
    hasta que ese worker queda libre de verdad: soltarlo antes mandaría a la
    extracción siguiente a esperar DENTRO del pool, con su timeout contando la
    espera. Pero nunca más allá del presupuesto de la abandonada (`vence`):
    si sigue corriendo, se recicla el pool igual que si nadie la hubiera
    cancelado -- la cancelación no le da más ni menos tiempo -- y el lugar se
    suelta. Sin esa cota, un PDF colgado con un solo worker dejaría el turno
    tomado para siempre."""
    loop = asyncio.get_running_loop()
    try:
        espera = asyncio.wrap_future(futuro)
        await asyncio.wait({espera}, timeout=max(0.0, vence - loop.time()))
        if not espera.done():
            logger.warning(
                "extraccion de pdf abandonada: siguió corriendo pasado su timeout, reciclando")
            espera.cancel()
            await _reciclar(pool)
        elif not espera.cancelled() and isinstance(espera.exception(), BrokenProcessPool):
            await _reciclar(pool)
    except Exception:  # fail-soft: vigilancia de fondo de un request que ya se fue; lo único que no puede fallar es soltar el turno (finally), y un pool que quedó roto lo repara _pool_actual en la próxima extracción
        logger.warning("extraccion de pdf abandonada: fallo al vigilarla", exc_info=True)
    finally:
        turno.release()


async def extraer_texto_en_pool(
    datos: bytes | str, max_paginas: int, max_chars: int
) -> tuple[str, bool]:
    """Corre `adjuntos.pdf.extraer_texto` (pypdf) en el ProcessPoolExecutor,
    con timeout (JAX_ADJUNTO_PDF_TIMEOUT_SEGUNDOS). El límite de páginas y de
    caracteres los sigue aplicando `extraer_texto` DENTRO del worker -- este
    módulo solo decide dónde corre y cuánto tiempo se le da. `datos` son
    los bytes o la ruta del PDF (RD2: api/upload.py pasa la ruta, así no
    serializa 10 MB hacia el worker).

    TURNO (Final fix wave #2, I1): antes del submit se toma
    `adjuntos.turno.turno_de_pdf()`, un semáforo del tamaño del pool. Así
    nunca hay más extracciones enviadas que workers, ninguna espera dentro
    del pool y el `wait_for` mide SOLO la corrida. La espera en el semáforo
    tiene su propio plazo (Ruling R34): el mismo
    JAX_ADJUNTO_PDF_TIMEOUT_SEGUNDOS, es decir, a lo sumo una corrida entera.
    Vencido, `TurnoDePdfSinLugar` (503 `adjuntos_reintentar`) sin haber tomado
    nada; cancelarla tampoco toma ni retiene nada.

    Timeout, worker muerto (BrokenProcessPool), submit sobre un pool
    cerrado (RuntimeError puntual) y cancelación inducida por el pool se
    mapean los cuatro al mismo PdfIlegible que ya usa el endpoint para "PDF
    ilegible" (código estable `pdf_ilegible`, sin agregar uno nuevo) y
    disparan el reciclado del pool. Una cancelación REAL de este request
    (alguien llamó `.cancel()` sobre esta tarea puntualmente) se relanza tal
    cual y no recicla por sí misma (ver el docstring del módulo, punto 5);
    si el PDF ya corría, `_vigilar_abandonada` retiene el lugar hasta que el
    worker termine o venza su presupuesto."""
    timeout = limites.cargar_timeout_de_pdf()
    loop = asyncio.get_running_loop()
    turno = turno_de_pdf()
    await _tomar_turno(turno, timeout)
    soltar = True
    futuro = None
    try:
        pool = await _pool_actual()
        try:
            # El submit real (ronda de corrección 2, item 1) va DENTRO del try:
            # `submit(...)` puede reventar (BrokenProcessPool/RuntimeError)
            # antes de devolver ningún future si el pool se rompió/cerró entre
            # el chequeo de `_pool_actual` y esta línea (otro hilo, otra
            # ventana). El future de concurrent.futures se guarda para poder
            # vigilarlo si este request se cancela (ver abajo).
            futuro = pool.submit(extraer_texto, datos, max_paginas, max_chars)
            vence = loop.time() + timeout
            return await asyncio.wait_for(asyncio.wrap_future(futuro), timeout=timeout)
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
        except RuntimeError as e:
            if not _runtime_error_de_pool_cerrado(e):
                raise
            logger.warning(
                "extraccion de pdf: submit sobre un pool ya cerrado/roto, reciclando")
            await _reciclar(pool)
            raise PdfIlegible("pool_roto") from None
        except asyncio.CancelledError:
            tarea = asyncio.current_task()
            if tarea is not None and tarea.cancelling() > 0:
                # Cancelación real de ESTE request -- no tocar el pool. Si el
                # PDF ya corre en un worker (cancel() no lo detuvo), el lugar
                # queda tomado hasta que termine o venza (I1).
                if futuro is not None and not futuro.done():
                    soltar = False
                    vigilancia = asyncio.create_task(
                        _vigilar_abandonada(futuro, pool, turno, vence))
                    _tareas_de_vigilancia.add(vigilancia)
                    vigilancia.add_done_callback(_tareas_de_vigilancia.discard)
                raise
            logger.warning(
                "extraccion de pdf: cancelada por un reciclado ajeno (pool roto o "
                "recién reciclado), mapeando igual")
            await _reciclar(pool)
            raise PdfIlegible("pool_reciclado") from None
    finally:
        if soltar:
            turno.release()
