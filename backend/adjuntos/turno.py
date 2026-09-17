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

_Semaforos = "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore]"
_por_loop: _Semaforos = weakref.WeakKeyDictionary()
_subidas_por_loop: _Semaforos = weakref.WeakKeyDictionary()


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
    2026-09-17). Su trabajo corre en asyncio.to_thread, pero b64encode de 10 MB
    y pypdf retienen el GIL: 25 hilos a la vez se lo disputaban al event loop
    (health p95 65 ms con imágenes y 244 ms con PDFs a c=25, contra 0,3 ms
    solo). Mismo patrón que el de imágenes: por loop, leído una vez."""
    return _turno(_subidas_por_loop, limites.cargar_subidas_en_proceso)
