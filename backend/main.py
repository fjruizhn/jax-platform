import asyncio
import logging
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# El freno primero (2026-09-16, frente B): sin JAX_KILL_SWITCH_PATH la
# plataforma no sabe dónde escribir ni dónde mirar el kill switch, y una Mesa
# sin freno no arranca. Lanza InterruptorSinConfigurar y uvicorn sale con
# error: systemd lo muestra en el journal.
import interruptor
interruptor.ruta_del_interruptor()

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
#
# setLevel(INFO) solo no alcanza: uvicorn con --log-level warning no deja
# un handler en el logger raíz, así que un registro que pasa el check de
# nivel de este logger cae igual en logging.lastResort (WARNING por
# default) y se pierde antes de journald. Hace falta un handler propio,
# sin propagar al raíz (evita duplicar la línea si algo más adelante le
# agrega un handler al raíz).
_cred_logger = logging.getLogger("credential_resolver")
_cred_logger.setLevel(logging.INFO)
_cred_handler = logging.StreamHandler()
_cred_handler.setLevel(logging.INFO)
_cred_handler.setFormatter(logging.Formatter("%(levelname)s credential_resolver: %(message)s"))
_cred_logger.addHandler(_cred_handler)
_cred_logger.propagate = False

import ajustes
from adjuntos import limites as limites_de_adjuntos
from db.connection import get_pool, close_pool
from http_client import get_http_client, close_http_client
from db.migrations import run_migrations
from db.seed import run_seed
from jax_engine.state import engine_state
from jax_engine.events import event_bus
from jax_engine.owner_cleanup import start_owner_file_cleanup
from jax_engine.facet_canary import start_facet_canary
from uso.reintento import start_reintento_de_uso
from jax_engine.websocket_hub import ws_hub
from jax_engine.lifecycle import lifecycle_lock, sse_connections
from jax_engine.schemas import JAXEvent
from auth.jwt import decode_token
from auth.middleware import reverificar_sesion, verificar_sesion

from api.health import router as health_router
from api.auth import router as auth_router
from api.state import router as state_router
from api.facets import router as facets_router
from api.pipelines import router as pipelines_router
from api.events import router as events_router
from api.chat import router as chat_router, _raiz_del_carril, _url_de_ollama
from api.command import router as command_router
from api.audit import router as audit_router
from api.image import router as image_router
from api.upload import router as upload_router
from api.motors import router as motors_router
from api.apariencia import router as apariencia_router
from api.ejecutor import router as ejecutor_router
from ejecutor import misiones as ejecutor_misiones
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
    admin_motors_router,
    smtp_router,
    kill_switch_router,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Revisión final del frente E (2026-09-16): JAX_OLLAMA_URL se valida ANTES
    # de abrir nada. Antes solo se leía en el turno del chat (y el embedding de
    # la memoria tragaba el error): el servicio arrancaba sano y fallaba delante
    # del usuario. Sin una URL base válida, EntornoInvalido y no arranca.
    _url_de_ollama()
    # SP3 del Ejecutor (2026-09-17): sin directorio del carril la Mesa no puede tomar su
    # prioridad sobre el Ejecutor. Mismo criterio que JAX_OLLAMA_URL: no arranca.
    _raiz_del_carril()
    # Frente D (2026-09-16): sin límites de adjuntos configurados no se
    # arranca. Antes que la base: es config, no depende de nada. Por atributo
    # del módulo (no `from ... import`) para que el test lo pueda sustituir.
    limites_de_adjuntos.cargar_limites()
    limites_de_adjuntos.cargar_imagenes_en_proceso()
    await get_pool()
    await get_http_client()
    await run_migrations()
    # SP2 del Ejecutor (2026-09-17): un turno en curso de un arranque anterior quedó huérfano
    # (el reinicio mató el runner y su vigía con el grupo del servicio): se cierra con código.
    await ejecutor_misiones.reconciliar_al_arrancar()
    # Ruling R16 (2026-09-17): nombra en ERROR cada ajuste ilegible (p.ej. tras
    # cambiar ACCESS_EXPIRE_SECONDS o MAX_PARALLEL_PIPELINES); no aborta.
    await ajustes.avisar_claves_ilegibles()
    await run_seed()
    await engine_state.cargar_nombres_de_facetas()
    engine_state.start_background_tasks()
    asyncio.create_task(start_owner_file_cleanup())
    asyncio.create_task(start_facet_canary())
    # Drenaje del respaldo de uso (2026-09-15, Task 3): reinserta las filas
    # de axioma_usage que quedaron en disco cuando la base no respondió.
    # Drena una vez al arrancar, antes del primer sleep: un reinicio
    # después de una caída tiene que recuperar enseguida, no al minuto.
    asyncio.create_task(start_reintento_de_uso())
    yield
    # Cerrar conversaciones web abiertas -> el worker de facts las destila.
    try:
        from api.chat import flush_open_conversations
        n = await flush_open_conversations()
        if n:
            # flush=True: sin esto el print se pierde por buffering al salir el proceso.
            print(f"[memoria] {n} conversación(es) web cerradas en shutdown", flush=True)
    except Exception:  # fail-soft: flush de conversaciones en shutdown, best-effort documentado — el proceso ya esta cerrando, nada depende de este resultado
        pass
    await close_http_client()
    await close_pool()


app = FastAPI(title="JAX Platform", version="0.1.0", lifespan=lifespan)

# Frente C (2026-09-16): un ajuste de admin ilegible es un 503 con código, en
# cualquier endpoint que lo lea -- nunca un default silencioso (ajustes.py).
app.add_exception_handler(ajustes.AjusteIlegible, ajustes.respuesta_de_ajuste_ilegible)

# Frente A (2026-09-16, A-18): el dev es mismo origen (proxy de Vite para /api
# y /ws) y producción también (nginx de la VM dev). Solo el origen declarado.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o for o in [os.getenv("FRONTEND_ORIGIN", "")] if o],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)

ROUTERS = (
    health_router,
    auth_router,
    state_router,
    facets_router,
    pipelines_router,
    events_router,
    chat_router,
    command_router,
    audit_router,
    image_router,
    upload_router,
    motors_router,
    dashboard_router,
    keys_router,
    credentials_router,
    users_router,
    repository_router,
    config_router,
    usage_router,
    # facet_models_router desregistrado (2026-08-10) y su módulo borrado
    # (2026-09-16, frente A): ver api/admin/__init__.py. La tabla queda.
    models_router,
    facet_bindings_router,
    admin_motors_router,
    smtp_router,
    kill_switch_router,
    apariencia_router,
    ejecutor_router,
)
for _router in ROUTERS:
    app.include_router(_router)


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
        # El wait_for va en su propio try: SÓLO el timeout de ESTE receive()
        # (la persona no manda el mensaje de auth a tiempo) es un 4001
        # silencioso. Si queda en el try grande de abajo, un TimeoutError de
        # cualquier otra cosa (p.ej. verificar_sesion/pool.acquire, si algún
        # día tuviera su propio timeout) caería en la misma rama silenciosa
        # -- indistinguible de esto, sin loguear el fallo real (M-2, code
        # review de esta ronda).
        try:
            auth_msg = await asyncio.wait_for(websocket.receive_json(), timeout=5)
        except asyncio.TimeoutError:
            try:
                await websocket.close(code=4001)
            except RuntimeError:  # fail-soft: best-effort close() tras un error ya manejado arriba; el except externo ya hace return
                pass
            return

        if auth_msg.get("type") != "auth":
            await websocket.close(code=4001)
            return

        token = auth_msg.get("token")
        payload = decode_token(token)

        if str(payload.get("user_id")) != str(user_id):
            await websocket.close(code=4001)
            return

        # La misma verificación que cada request HTTP (admin usuarios etapa
        # 2): usuario existente, `active` y con la versión de token vigente.
        # Un HTTPException (sesión inválida) cae en el `except HTTPException`
        # de abajo -> 4001 sin log; cualquier otra excepción (pool agotado,
        # MariaDB caída, o un TimeoutError que no sea el del receive() de
        # arriba) cae en el `except Exception` -> 4001 CON log (code review
        # de esta ronda).
        sesion = await verificar_sesion(payload, "access")

    except WebSocketDisconnect:
        return
    except (HTTPException, ValueError, TypeError, AttributeError, KeyError):
        # Caso esperado del handshake, no un fallo de infraestructura: token
        # invalido/expirado o sesion invalida -- inactiva, rol cambiado,
        # token_version vieja (HTTPException de decode_token/verificar_sesion,
        # code review de esta ronda), o el mensaje de auth malformado (JSON
        # invalido, no es un dict, faltan campos -- el comportamiento de
        # antes de esta ronda). No se loguea como error.
        try:
            await websocket.close(code=4001)
        except RuntimeError:  # fail-soft: mismo best-effort close() que la rama anterior, el except externo ya hace return
            pass
        return
    except Exception:  # fail-soft: fallo real de infraestructura (pool agotado, MariaDB caida, timeout de la consulta) -- cierra 4001 igual que arriba, pero primero deja rastro en el log; sin esto es indistinguible de una sesion invalida (code review de esta ronda)
        logger.exception("Fallo inesperado en el handshake WebSocket")
        try:
            await websocket.close(code=4001)
        except RuntimeError:  # fail-soft: mismo best-effort close() que la rama anterior, el except externo ya hace return
            pass
        return

    tenant_id = sesion.tenant_id
    role = sesion.role  # de la base, no del token

    await websocket.send_json({"type": "auth_ok"})

    connection_id = await _ws_connect_and_subscribe(user_id, tenant_id, role, websocket)

    heartbeat_task = None
    try:
        # m1 (revisión final, etapa 3): el corte de un admin pudo caer entre
        # verificar_sesion y el registro en el hub, y entonces no encontró esta
        # conexión. Ya registrada, se verifica de nuevo: cualquier corte
        # posterior la encuentra, y uno anterior se ve acá.
        try:
            await reverificar_sesion(sesion)
        except HTTPException:
            await _cerrar_4001(websocket)
            return
        except Exception:  # fail-soft: fallo de infraestructura al re-verificar -- falla cerrado (4001) como el handshake, con rastro en el log
            logger.exception("Fallo inesperado al re-verificar la sesión del WebSocket")
            await _cerrar_4001(websocket)
            return

        heartbeat_task = asyncio.create_task(_heartbeat(user_id, tenant_id))
        while True:
            data = await websocket.receive_json()
            if data.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:  # fail-soft: WebSocketDisconnect es el cierre normal del cliente, no un error
        pass
    finally:
        if heartbeat_task is not None:
            heartbeat_task.cancel()
        await _ws_disconnect_and_maybe_unsubscribe(user_id, connection_id)


async def _cerrar_4001(websocket: WebSocket):
    try:
        await websocket.close(code=4001)
    except RuntimeError:  # fail-soft: best-effort close() de un socket que el cliente ya pudo haber cerrado; el finally del endpoint limpia igual
        pass


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
