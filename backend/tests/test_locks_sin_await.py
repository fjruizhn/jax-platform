"""Frente A, A-24 (2026-09-16): EventBus, WebSocketHub y ResourceManager
tomaban un asyncio.Lock alrededor de operaciones sobre dicts/sets SIN ningun
await adentro. En asyncio esas secciones ya corren sin interrupcion, asi que
el lock nunca se disputaba. Las firmas `async` se conservan (los llamadores
hacen await) y lifecycle_lock, que SI serializa secuencias con await, no se
toca. Puros."""
import asyncio
import inspect

from jax_engine.events import EventBus
from jax_engine.lifecycle import lifecycle_lock
from jax_engine.resource_manager import ResourceManager
from jax_engine.websocket_hub import WebSocketHub


def test_ninguno_de_los_tres_tiene_lock():
    for objeto in (EventBus(), WebSocketHub(), ResourceManager()):
        assert not hasattr(objeto, "_lock"), type(objeto).__name__


def test_las_firmas_siguen_siendo_async():
    metodos = (
        EventBus.subscribe, EventBus.unsubscribe, EventBus.publish,
        WebSocketHub.connect, WebSocketHub.disconnect, WebSocketHub.has_connections,
        WebSocketHub.send_to_user, WebSocketHub.close_user, WebSocketHub.connected_user_ids,
        ResourceManager.can_start_pipeline, ResourceManager.admit_pipeline,
        ResourceManager.release_pipeline, ResourceManager.active_count,
    )
    for metodo in metodos:
        assert inspect.iscoroutinefunction(metodo), metodo.__qualname__


def test_lifecycle_lock_sigue_siendo_un_lock():
    assert isinstance(lifecycle_lock, asyncio.Lock)


async def test_close_user_recorre_una_foto_aunque_el_close_desconecte():
    hub = WebSocketHub()

    class _Socket:
        def __init__(self):
            self.cerrado_con = None
            self.id = None

        async def close(self, code=1000):
            await hub.disconnect("u-a", self.id)   # muta el dict durante el recorrido
            self.cerrado_con = code

    a, b = _Socket(), _Socket()
    a.id = await hub.connect("u-a", a)
    b.id = await hub.connect("u-a", b)
    assert await hub.close_user("u-a") == 2
    assert (a.cerrado_con, b.cerrado_con) == (4001, 4001)
    assert not await hub.has_connections("u-a")
