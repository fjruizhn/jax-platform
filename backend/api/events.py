import asyncio
import json
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from auth.middleware import get_current_user
from auth.models import AuthUser
from jax_engine.events import event_bus
from jax_engine.schemas import JAXEvent

router = APIRouter(prefix="/api/events")


@router.get("")
async def sse_events(user: AuthUser = Depends(get_current_user)):
    queue: asyncio.Queue[JAXEvent] = asyncio.Queue()

    async def callback(event: JAXEvent):
        await queue.put(event)

    await event_bus.subscribe(user.tenant_id, user.user_id, callback)

    async def generator():
        try:
            while True:
                event = await queue.get()
                yield f"data: {json.dumps(event.model_dump())}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            await event_bus.unsubscribe(user.user_id)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
