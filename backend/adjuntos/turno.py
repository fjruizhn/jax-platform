"""Turno para el trabajo pesado de una imagen en el event loop (R16, 2026-09-17).

Parsear el cuerpo de 14 MB y validar el alfabeto del base64 corren en el loop
cediendo por tramos (en un hilo le disputarían el GIL al loop: medido, peor).
Cediendo, 25 imágenes a la vez se intercalan y cada pedido chico espera la
vuelta completa: /api/health a 5 VUs tuvo p95 17,2 ms bajo chat_imagen_max a
c=25. Con este turno (JAX_ADJUNTO_IMAGENES_EN_PROCESO=1), 2,5 ms.

El turno cubre SOLO esas pasadas (decenas de ms), nunca la espera al
proveedor: un LLM lento no frena a las otras imágenes.

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
