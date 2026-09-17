from collections import defaultdict
from typing import Callable, Awaitable
from .schemas import JAXEvent

Callback = Callable[[JAXEvent], Awaitable[None]]


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


event_bus = EventBus()
