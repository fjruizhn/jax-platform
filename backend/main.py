import asyncio
import logging
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# Debe correr antes de importar cualquier router: systemd carga
# /etc/jax/.env vía EnvironmentFile con las API keys de proveedor ya
# cifradas (ver crypto_secrets.py); esto las deja en texto plano en
# os.environ para que api/chat.py y api/image.py sigan leyendo
# os.getenv(...) exactamente igual que antes.
from crypto_secrets import decrypt_provider_keys_in_env
decrypt_provider_keys_in_env()

# jax-platform.service corre con --log-level warning a propósito (sin
# access-log, sin ruido) — pero eso deja invisible el camino EXITOSO de
# credential_resolver (source=db se loguea a logger.info, ver
# credential_resolver.py). Solo source=env_fallback (warning) y
# FAIL_CLOSED (error) quedan visibles a ese nivel global. Sin este bump,
# la ventana de 7 días de B1.4 podría "pasar" sin que nadie vea nunca una
# confirmación positiva de que credential_resolver está resolviendo desde
# DB — ausencia de fallas no es evidencia de éxito (mismo principio que
# C3 del doc de reformas, aplicado a logging en vez de a claims del
# modelo). Sube SOLO este logger — el nivel global sigue en warning.
logging.getLogger("credential_resolver").setLevel(logging.INFO)

from db.connection import get_pool, close_pool
from http_client import get_http_client, close_http_client
from db.migrations import run_migrations
from db.seed import run_seed
from jax_engine.state import engine_state
from jax_engine.events import event_bus
from jax_engine.owner_cleanup import start_owner_file_cleanup
from jax_engine.websocket_hub import ws_hub
from jax_engine.lifecycle import lifecycle_lock, sse_connections
from jax_engine.schemas import JAXEvent
from auth.jwt import decode_token

from api.health import router as health_router
from api.auth import router as auth_router
from api.state import router as state_router
from api.facets import router as facets_router
from api.pipelines import router as pipelines_router
from api.events import router as events_router
from api.chat import router as chat_router
from api.command import router as command_router
from api.audit import router as audit_router
from api.image import router as image_router
from api.upload import router as upload_router
from api.admin import (
    dashboard_router,
    keys_router,
    credentials_router,
    users_router,
    repository_router,
    config_router,
    usage_router,
    models_router,
    facet_bindings_router,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await get_pool()
    await get_http_client()
    await run_migrations()
    await run_seed()
    engine_state.start_background_tasks()
    asyncio.create_task(start_owner_file_cleanup())
    yield
    # Cerrar conversaciones web abiertas -> el worker de facts las destila.
    try:
        from api.chat import flush_open_conversations
        n = await flush_open_conversations()
        if n:
            # flush=True: sin esto el print se pierde por buffering al salir el proceso.
            print(f"[memoria] {n} conversación(es) web cerradas en shutdown", flush=True)
    except Exception:
        pass
    await close_http_client()
    await close_pool()


app = FastAPI(title="JAX Platform", version="0.1.0", lifespan=lifespan)

ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:8080",
    os.getenv("FRONTEND_ORIGIN", ""),
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o for o in ALLOWED_ORIGINS if o],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(health_router)
app.include_router(auth_router)
app.include_router(state_router)
app.include_router(facets_router)
app.include_router(pipelines_router)
app.include_router(events_router)
app.include_router(chat_router)
app.include_router(command_router)
app.include_router(audit_router)
app.include_router(image_router)
app.include_router(upload_router)
app.include_router(dashboard_router)
app.include_router(keys_router)
app.include_router(credentials_router)
app.include_router(users_router)
app.include_router(repository_router)
app.include_router(config_router)
app.include_router(usage_router)
# facet_models_router desregistrado — ver api/admin/__init__.py
app.include_router(models_router)
app.include_router(facet_bindings_router)


# ws_hub and event_bus each guard their own state with their own lock, so a
# disconnecting tab's disconnect+maybe-unsubscribe sequence can interleave
# with a reconnecting tab's connect+subscribe sequence for the same user
# (e.g. a browser tab reconnecting right as the old tab closes): the
# reconnect's subscribe can land in the gap between the disconnecting tab's
# "any connections left?" check and its unsubscribe call, and then get wiped
# out by that unsubscribe. lifecycle_lock (jax_engine/lifecycle.py) serializes
# the connect+subscribe / disconnect+maybe-unsubscribe sequences of BOTH the
# WS endpoint and the SSE endpoint against each other (connect/disconnect is
# not a hot path — no I/O happens under it) so that can no longer happen
# same-channel OR cross-channel (a WS tab and an SSE connection for the same
# user share the same event_bus subscription slot).


async def _ws_connect_and_subscribe(
    user_id: str, tenant_id: str, role: str, websocket: WebSocket
) -> str:
    async with lifecycle_lock:
        connection_id = await ws_hub.connect(user_id, websocket)
        engine_state.register_user(user_id, tenant_id, role)

        async def event_callback(event: JAXEvent):
            await ws_hub.send_to_user(user_id, event)

        await event_bus.subscribe(tenant_id, user_id, event_callback)
    return connection_id


async def _ws_disconnect_and_maybe_unsubscribe(user_id: str, connection_id: str):
    async with lifecycle_lock:
        await ws_hub.disconnect(user_id, connection_id)
        no_ws_left = not await ws_hub.has_connections(user_id)
        # register_user/unregister_user is WS-only presence bookkeeping — SSE
        # never calls either — so it must be gated on WS state alone, not on
        # whether an SSE connection is still around for this user.
        if no_ws_left:
            engine_state.unregister_user(user_id)
        # The event_bus subscription, in contrast, IS shared cross-channel:
        # only tear it down once no connection is left on EITHER channel,
        # otherwise closing this WS tab would silence a live SSE connection
        # for the same user.
        if no_ws_left and not sse_connections.has_connections(user_id):
            await event_bus.unsubscribe(user_id)


@app.websocket("/ws/{user_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    user_id: str,
):
    await websocket.accept()

    try:
        auth_msg = await asyncio.wait_for(websocket.receive_json(), timeout=5)
        if auth_msg.get("type") != "auth":
            await websocket.close(code=4001)
            return

        token = auth_msg.get("token")
        payload = decode_token(token)

        if str(payload.get("user_id")) != str(user_id):
            await websocket.close(code=4001)
            return

    except WebSocketDisconnect:
        return
    except asyncio.TimeoutError:
        try:
            await websocket.close(code=4001)
        except RuntimeError:
            pass
        return
    except Exception:
        try:
            await websocket.close(code=4001)
        except RuntimeError:
            pass
        return

    tenant_id = str(payload["tenant_id"])
    role = payload["role"]

    await websocket.send_json({"type": "auth_ok"})

    connection_id = await _ws_connect_and_subscribe(user_id, tenant_id, role, websocket)

    heartbeat_task = asyncio.create_task(_heartbeat(user_id, tenant_id))

    try:
        while True:
            data = await websocket.receive_json()
            if data.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        pass
    finally:
        heartbeat_task.cancel()
        await _ws_disconnect_and_maybe_unsubscribe(user_id, connection_id)


async def _heartbeat(user_id: str, tenant_id: str):
    while True:
        await asyncio.sleep(30)
        event = JAXEvent(
            event_type="heartbeat",
            tenant_id=tenant_id,
            user_id=user_id,
            payload={"ping": True},
        )
        await ws_hub.send_to_user(user_id, event)


FRONTEND_DIST = os.path.join(
    os.path.dirname(__file__), "..", "frontend", "dist"
)
if os.path.isdir(FRONTEND_DIST):
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="static")
