import uuid
from fastapi import WebSocket
from .schemas import JAXEvent


class WebSocketHub:
    def __init__(self):
        # user_id -> {connection_id -> websocket}. One user may hold several
        # live connections at once (e.g. multiple browser tabs).
        self._connections: dict[str, dict[str, WebSocket]] = {}

    async def connect(self, user_id: str, websocket: WebSocket) -> str:
        connection_id = str(uuid.uuid4())
        self._connections.setdefault(user_id, {})[connection_id] = websocket
        return connection_id

    async def disconnect(self, user_id: str, connection_id: str):
        conns = self._connections.get(user_id)
        if conns is None:
            return
        conns.pop(connection_id, None)
        if not conns:
            self._connections.pop(user_id, None)

    async def has_connections(self, user_id: str) -> bool:
        return bool(self._connections.get(user_id))

    async def send_to_user(self, user_id: str, event: JAXEvent):
        conns = list(self._connections.get(user_id, {}).items())
        for connection_id, ws in conns:
            try:
                await ws.send_json(event.model_dump())
            except Exception:  # fail-soft: un socket muerto se desconecta; los demás sockets del usuario siguen recibiendo (CancelledError no se atrapa)
                await self.disconnect(user_id, connection_id)

    async def close_user(self, user_id: str, code: int = 4001) -> int:
        """Cierra todas las conexiones vivas del usuario (2026-09-15, admin
        usuarios etapa 3, Step 4b): verificar_sesion corta el WS solo en el
        handshake, así que una pestaña de un usuario degradado, desactivado o
        borrado seguiría recibiendo eventos hasta reconectar.

        Las conexiones se copian a una lista antes del primer await: un close
        que desconecta no altera el recorrido. Las entradas no se borran acá:
        el `finally` del endpoint (main.py::_ws_disconnect_and_maybe_unsubscribe,
        bajo lifecycle_lock) hace disconnect/unregister/unsubscribe al salir
        del receive. Devuelve cuántas cerró."""
        conns = list(self._connections.get(user_id, {}).values())
        cerradas = 0
        for ws in conns:
            try:
                await ws.close(code=code)
                cerradas += 1
            except Exception:  # fail-soft: el socket pudo cerrarse solo entre la lectura y el close (cliente que se fue, close ya enviado); una conexión rota no debe impedir cerrar las demás del mismo usuario
                continue
        return cerradas

    async def connected_user_ids(self) -> list[str]:
        return list(self._connections.keys())


ws_hub = WebSocketHub()
