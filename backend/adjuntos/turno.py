"""Turnos para el trabajo pesado de los adjuntos (R16, 2026-09-17; RD3 el mismo día).

`turno_de_imagen` (JAX_ADJUNTO_IMAGENES_EN_PROCESO): cuántas imágenes se leen
de disco y se codifican a base64 a la vez para mandarlas al proveedor
(adjuntos/contrato.py::leer_adjuntos). Corre en un hilo, pero binascii
retiene el GIL durante cada tramo de 256 KB: medido en hall9000 (tic de 1 ms
en el loop, 25 imágenes de 10 MB), las 25 a la vez atrasan el tic p95 60 ms;
de a una, 0,35 ms. Antes de RD3 este mismo turno cubría el barrido del
alfabeto del base64 que mandaba el cliente, que ya no existe.

El turno cubre SOLO la lectura y la codificación (ms), nunca la espera al
proveedor: un LLM lento no frena a las otras imágenes.

Un semáforo por event loop (asyncio.Semaphore se ata al loop que lo usa; los
tests abren uno por asyncio.run). El tope se lee una vez por loop: el entorno
de un proceso no cambia en caliente, se invalida reiniciando el servicio.
"""
import asyncio
import weakref

from adjuntos import limites

_Semaforos = "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore]"
_por_loop: _Semaforos = weakref.WeakKeyDictionary()
_subidas_por_loop: _Semaforos = weakref.WeakKeyDictionary()
_pdf_por_loop: _Semaforos = weakref.WeakKeyDictionary()


def _turno(tabla, cargar) -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    semaforo = tabla.get(loop)
    if semaforo is None:
        semaforo = tabla[loop] = asyncio.Semaphore(cargar())
    return semaforo


def turno_de_imagen() -> asyncio.Semaphore:
    return _turno(_por_loop, limites.cargar_imagenes_en_proceso)


def turno_de_subida() -> asyncio.Semaphore:
    """Tope de /api/chat/upload (JAX_ADJUNTO_SUBIDAS_EN_PROCESO, revisión final
    2026-09-17). Cubre SOLO la clasificación (adjuntos/tipos.py::
    clasificar_archivo, en un hilo): validar hasta 10 MB de texto con el
    decodificador incremental retiene el GIL por bloque, y 25 a la vez se lo
    disputaban al event loop. pypdf ya no va acá (corre en otro proceso, RD1)
    y la espera al pool tampoco (Final fix wave #2, I1): la acota
    `turno_de_pdf`. Mismo patrón que el de imágenes: por loop, leído una vez."""
    return _turno(_subidas_por_loop, limites.cargar_subidas_en_proceso)


def turno_de_pdf() -> asyncio.Semaphore:
    """Lugares del ProcessPoolExecutor de pypdf (JAX_ADJUNTO_PDF_PROCESOS,
    Final fix wave #2, I1). adjuntos/pdf_pool.py lo toma ANTES del submit: a lo
    sumo tantas extracciones enviadas como workers, así ninguna espera DENTRO
    del pool y su timeout mide solo su corrida. Antes, con más subidas que
    workers, la que quedaba en la cola del pool vencía por la espera y el
    reciclado mataba a la que sí corría. Mismo patrón: por loop, leído una
    vez (el tamaño del pool sale de la misma variable)."""
    return _turno(_pdf_por_loop, limites.cargar_procesos_de_pdf)
