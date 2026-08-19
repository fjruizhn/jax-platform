import asyncio
import json
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from auth.middleware import get_current_user
from auth.models import AuthUser
from jax_engine.events import event_bus
from jax_engine.lifecycle import lifecycle_lock, sse_connections
from jax_engine.websocket_hub import ws_hub
from jax_engine.schemas import JAXEvent

router = APIRouter(prefix="/api/events")


async def _sse_connect_and_subscribe(user_id: str, tenant_id: str, callback):
    async with lifecycle_lock:
        sse_connections.increment(user_id)
        await event_bus.subscribe(tenant_id, user_id, callback)


async def _sse_disconnect_and_maybe_unsubscribe(user_id: str):
    async with lifecycle_lock:
        sse_connections.decrement(user_id)
        # Only tear down the shared subscription once no connection on EITHER
        # channel is left for this user — otherwise this SSE connection
        # closing could wipe out a live WS tab's subscription for the same
        # user (or a sibling SSE connection's), see jax_engine/lifecycle.py.
        if not sse_connections.has_connections(user_id) and not await ws_hub.has_connections(user_id):
            await event_bus.unsubscribe(user_id)


@router.get("")
async def sse_events(user: AuthUser = Depends(get_current_user)):
    queue: asyncio.Queue[JAXEvent] = asyncio.Queue()

    async def callback(event: JAXEvent):
        await queue.put(event)

    await _sse_connect_and_subscribe(user.user_id, user.tenant_id, callback)

    async def generator():
        try:
            while True:
                event = await queue.get()
                yield f"data: {json.dumps(event.model_dump())}\n\n"
        except asyncio.CancelledError:  # fail-soft: CancelledError es la forma normal de terminar el generador SSE al desconectar el cliente
            pass
        finally:
            await _sse_disconnect_and_maybe_unsubscribe(user.user_id)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
