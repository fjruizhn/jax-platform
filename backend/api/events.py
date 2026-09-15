import asyncio
import json
import logging
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from auth.middleware import get_current_user, reverificar_sesion
from auth.models import AuthUser
from jax_engine.events import event_bus
from jax_engine.lifecycle import lifecycle_lock, sse_connections
from jax_engine.websocket_hub import ws_hub
from jax_engine.schemas import JAXEvent

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/events")

# Streams SSE abiertos por usuario (2026-09-15, admin usuarios etapa 3, Step
# 4b / Ruling U5): el SSE autentica una sola vez al conectar, igual que el
# handshake del WS. Cuando cambian rol o estado del usuario (o se borra),
# close_user_streams mete _CERRAR en cada cola y el generador termina. Se muta
# bajo lifecycle_lock, igual que sse_connections.
_CERRAR = object()
_streams: dict[str, set[asyncio.Queue]] = {}


async def _sse_connect_and_subscribe(user_id: str, tenant_id: str, callback, cola: asyncio.Queue | None = None):
    async with lifecycle_lock:
        sse_connections.increment(user_id)
        if cola is not None:
            _streams.setdefault(user_id, set()).add(cola)
        await event_bus.subscribe(tenant_id, user_id, callback)


async def _sse_disconnect_and_maybe_unsubscribe(user_id: str, cola: asyncio.Queue | None = None):
    async with lifecycle_lock:
        sse_connections.decrement(user_id)
        if cola is not None:
            colas = _streams.get(user_id)
            if colas is not None:
                colas.discard(cola)
                if not colas:
                    _streams.pop(user_id, None)
        # Only tear down the shared subscription once no connection on EITHER
        # channel is left for this user — otherwise this SSE connection
        # closing could wipe out a live WS tab's subscription for the same
        # user (or a sibling SSE connection's), see jax_engine/lifecycle.py.
        if not sse_connections.has_connections(user_id) and not await ws_hub.has_connections(user_id):
            await event_bus.unsubscribe(user_id)


async def close_user_streams(user_id: str) -> int:
    """Termina todos los streams SSE abiertos del usuario; devuelve cuántos.
    La cola es sin límite: put_nowait no bloquea ni falla. El `finally` del
    generador hace el disconnect/unsubscribe."""
    async with lifecycle_lock:
        colas = list(_streams.get(user_id, ()))
    for cola in colas:
        cola.put_nowait(_CERRAR)
    return len(colas)


@router.get("")
async def sse_events(user: AuthUser = Depends(get_current_user)):
    queue: asyncio.Queue = asyncio.Queue()

    async def callback(event: JAXEvent):
        await queue.put(event)

    await _sse_connect_and_subscribe(user.user_id, user.tenant_id, callback, queue)

    # m1 (revisión final, etapa 3): el corte de un admin pudo caer entre
    # get_current_user y el registro del stream, y entonces no lo encontró. Ya
    # registrado, se verifica de nuevo; si la sesión ya no vale, el stream
    # termina igual que con close_user_streams (el finally del generador limpia).
    try:
        await reverificar_sesion(user)
    except HTTPException:
        queue.put_nowait(_CERRAR)
    except Exception:  # fail-soft: fallo de infraestructura al re-verificar -- falla cerrado (termina el stream) con rastro en el log
        logger.exception("Fallo inesperado al re-verificar la sesión del stream SSE")
        queue.put_nowait(_CERRAR)

    async def generator():
        try:
            while True:
                event = await queue.get()
                if event is _CERRAR:
                    return
                yield f"data: {json.dumps(event.model_dump())}\n\n"
        except asyncio.CancelledError:  # fail-soft: CancelledError es la forma normal de terminar el generador SSE al desconectar el cliente
            pass
        finally:
            await _sse_disconnect_and_maybe_unsubscribe(user.user_id, queue)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
