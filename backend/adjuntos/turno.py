"""Turno para el trabajo pesado de una imagen en el event loop (R16, 2026-09-17).

La validación del alfabeto del base64 corre en el loop cediendo por tramos
(en un hilo le disputaría el GIL al loop: medido, peor). Cediendo, muchas
imágenes a la vez se intercalan y cada pedido chico espera la vuelta completa.
Este turno (JAX_ADJUNTO_IMAGENES_EN_PROCESO) limita cuántas validan a la vez.
Números medidos en task-11-memoria-report.md (el parseo del cuerpo es el
normal de FastAPI desde el ruling del principal sobre 3ba4630).

El turno cubre SOLO esa pasada (ms), nunca la espera al proveedor: un LLM
lento no frena a las otras imágenes.

Un semáforo por event loop (asyncio.Semaphore se ata al loop que lo usa; los
tests abren uno por asyncio.run). El tope se lee una vez por loop: el entorno
de un proceso no cambia en caliente, se invalida reiniciando el servicio.
"""
import asyncio
import weakref

from adjuntos import limites

_por_loop: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore]" = weakref.WeakKeyDictionary()


def turno_de_imagen() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    semaforo = _por_loop.get(loop)
    if semaforo is None:
        semaforo = _por_loop[loop] = asyncio.Semaphore(limites.cargar_imagenes_en_proceso())
    return semaforo
