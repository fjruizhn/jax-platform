import asyncio
from collections import defaultdict
from typing import Callable, Awaitable
from .schemas import JAXEvent

Callback = Callable[[JAXEvent], Awaitable[None]]

# Tope por suscriptor en `publicar_a_todos` (fix round 1 del kill switch,
# 2026-09-17, ruling R8). Un WS colgado no detecta el socket muerto hasta el
# timeout de ping (~40 s); sin este tope, `kill_switch.activar`/`reanudar`
# quedarían esperando esos 40 s ANTES de auditar, con `_cambio` retenido --
# una emergencia real (frenar la Mesa) bloqueada por un cliente colgado. 2.0
# segundos es generoso para un callback normal (WS/SSE en el mismo proceso)
# y corto frente a los 40 s del peor caso.
TIEMPO_MAXIMO_POR_SUSCRIPTOR = 2.0


class EventBus:
    """Sin lock (2026-09-16, frente A, A-24): subscribe/unsubscribe y la
    lectura de publish no tienen await entre leer y escribir, así que en
    asyncio corren sin interrupción. El await del callback ya estaba fuera."""

    def __init__(self):
        # tenant_id -> {user_id -> callback}
        self._subscribers: dict[str, dict[str, Callback]] = defaultdict(dict)

    async def subscribe(self, tenant_id: str, user_id: str, callback: Callback):
        self._subscribers[tenant_id][user_id] = callback

    async def unsubscribe(self, user_id: str):
        for tenant_subscribers in self._subscribers.values():
            tenant_subscribers.pop(user_id, None)

    async def publish(self, event: JAXEvent):
        # WS canal por usuario — nunca por tenant: route only to the subscriber
        # that owns this event's user_id, not every user in the tenant.
        cb = self._subscribers.get(str(event.tenant_id), {}).get(str(event.user_id))
        if cb is None:
            return
        try:
            await cb(event)
        except Exception:  # fail-soft: aislar el fallo de un subscriber de WS del resto del event bus; los demas subscribers no deben verse afectados por uno roto
            pass

    async def publicar_a_todos(self, event_type: str, payload: dict) -> int:
        """Un evento para CADA suscriptor del bus, WS y SSE (kill switch,
        2026-09-16). `publish` enruta a un solo usuario a propósito; esto es
        solo para estado global. Devuelve cuántos lo recibieron.

        Sin lock (frente A, A-24, ya quitó `self._lock` de esta clase): la
        foto de `_subscribers` se toma directo, sin `await` entre leer y
        copiar, así que en asyncio corre sin interrupción -- mismo criterio
        que `subscribe`/`unsubscribe` de arriba.

        CONCURRENTE, no en serie (fix round 1, ruling R8): cada suscriptor
        corre bajo su propio `asyncio.wait_for(TIEMPO_MAXIMO_POR_SUSCRIPTOR)`
        y todos se lanzan juntos con `asyncio.gather`. `kill_switch.activar`/
        `reanudar` llaman a esto DENTRO de `_cambio` a propósito (para no
        mezclar el orden de activated/released); si el envío fuera en serie
        y sin tope, un solo WS colgado retendría ese lock -- y con él, la
        próxima activación de emergencia -- hasta su propio timeout de ping
        (~40 s). Un timeout o una excepción cuentan como "no recibido", igual
        que antes."""
        destinos = [
            (tenant_id, user_id, cb)
            for tenant_id, suscriptores in self._subscribers.items()
            for user_id, cb in suscriptores.items()
        ]

        async def _entregar(tenant_id, user_id, cb) -> bool:
            evento = JAXEvent(event_type=event_type, tenant_id=tenant_id, user_id=user_id, payload=payload)
            try:
                await asyncio.wait_for(cb(evento), timeout=TIEMPO_MAXIMO_POR_SUSCRIPTOR)
                return True
            except Exception:  # fail-soft: un suscriptor roto o colgado (socket muerto, WS sin ping) no impide que el resto se entere del freno ni retiene _cambio; el estado real igual llega por /api/state
                return False

        resultados = await asyncio.gather(*(_entregar(t, u, cb) for t, u, cb in destinos))
        return sum(resultados)


event_bus = EventBus()
