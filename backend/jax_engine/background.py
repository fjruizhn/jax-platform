"""
Encolado de tareas de background con aislamiento entre tareas.

POR QUE EXISTE. `BackgroundTasks` de Starlette ejecuta la cadena de tareas
SECUENCIAL y SIN AISLAMIENTO: si una lanza, la excepcion propaga y las tareas
encoladas DESPUES en la misma request nunca corren. No es un defecto de este
codigo -- es como funciona el mecanismo, verificado empiricamente contra
fastapi 0.139.2 / starlette 1.3.1 (2026-09-01), no leido en la documentacion.

Y NO SE VE CUANDO PASA. Bajo uvicorn la respuesta ya se emitio cuando corren
las tareas, asi que el cliente ve 200. El traceback de la que lanzo queda en
`journalctl`; de la que NUNCA CORRIO no queda nada: ni log, ni fila, ni error.
Es el mismo modo de falla silencioso que la ronda de alertas 2026-08-27 vino a
eliminar en otro mecanismo.

LA REGLA. Nadie llama `add_task()` crudo; todo pasa por `add_safe_task()`. Es
"ninguna", no "no mas de una": hoy cada endpoint encola una sola tarea y eso
alcanza para que el defecto no se manifieste, pero esa es una propiedad de los
llamadores de hoy y no una garantia del mecanismo. Se exige mecanicamente en
tests/test_policy_background_tasks.py -- una regla que dependa de que alguien
se acuerde ya fallo dos veces en este repo.

En memoria de Jairo Urbina.
"""
from __future__ import annotations

import inspect
import logging
from typing import Any, Callable

from fastapi import BackgroundTasks

logger = logging.getLogger("background")


def add_safe_task(
    background_tasks: BackgroundTasks,
    func: Callable[..., Any],
    /,
    *args: Any,
    **kwargs: Any,
) -> None:
    """Encola `func` envuelta en su propio try/except.

    Acepta funciones sincronicas y asincronicas, igual que `add_task`.

    El `except` es fail-soft A PROPOSITO y RUIDOSO a proposito: la excepcion se
    detiene aca porque dejarla propagar cancela las tareas encoladas despues, y
    se registra con traceback porque tragarla en silencio cambiaria un modo de
    falla silencioso por otro. Nadie aguas abajo depende de que esta tarea haya
    salido bien: `add_task` no devuelve nada y la respuesta HTTP ya se emitio.

    Lo que este envoltorio NO hace, dicho para que nadie lo suponga: no
    reintenta, no persiste el fallo en la DB y no alerta. Una tarea cuyo
    resultado alguien necesite confirmar no es una BackgroundTask -- es trabajo
    que necesita su propio registro (ver `probe_after_rebind`, que escribe
    `probe_error` por su cuenta y no delega eso al mecanismo de encolado).
    """
    nombre = getattr(func, "__name__", repr(func))

    async def _envuelta() -> None:
        try:
            resultado = func(*args, **kwargs)
            if inspect.isawaitable(resultado):
                await resultado
        except Exception:
            logger.exception(
                "background_task_failed task=%s -- la excepcion se detiene en el "
                "envoltorio a proposito: propagarla cancelaria las tareas "
                "encoladas DESPUES en esta misma request, sin dejar rastro de "
                "las que no llegaron a correr.",
                nombre,
            )

    # Que envuelve, visible desde afuera. Dos motivos, los dos reales:
    # (1) al depurar, una cola llena de `_envuelta` no dice nada;
    # (2) los tests que afirman "este endpoint encola probe_after_rebind"
    #     (test_facet_canary_rebind.py) lo hacen POR IDENTIDAD, y sin esto
    #     habria que debilitarlos a "encola algo", que es justamente la
    #     afirmacion que no sirve.
    _envuelta.tarea_original = func
    _envuelta.tarea_args = args
    _envuelta.tarea_kwargs = kwargs

    background_tasks.add_task(_envuelta)
