import asyncio
from collections import defaultdict
from typing import Callable, Awaitable
from .schemas import JAXEvent

Callback = Callable[[JAXEvent], Awaitable[None]]


class EventBus:
    def __init__(self):
        # tenant_id -> {user_id -> callback}
        self._subscribers: dict[str, dict[str, Callback]] = defaultdict(dict)
        self._lock = asyncio.Lock()

    async def subscribe(self, tenant_id: str, user_id: str, callback: Callback):
        async with self._lock:
            self._subscribers[tenant_id][user_id] = callback

    async def unsubscribe(self, user_id: str):
        async with self._lock:
            for tenant_subscribers in self._subscribers.values():
                tenant_subscribers.pop(user_id, None)

    async def publish(self, event: JAXEvent):
        tenant_id = str(event.tenant_id)
        async with self._lock:
            callbacks = list(self._subscribers.get(tenant_id, {}).values())
        for cb in callbacks:
            try:
                await cb(event)
            except Exception:
                pass


event_bus = EventBus()
