import asyncio
import uuid
from fastapi import WebSocket
from .schemas import JAXEvent


class WebSocketHub:
    def __init__(self):
        # user_id -> {connection_id -> websocket}. One user may hold several
        # live connections at once (e.g. multiple browser tabs).
        self._connections: dict[str, dict[str, WebSocket]] = {}
        self._lock = asyncio.Lock()

    async def connect(self, user_id: str, websocket: WebSocket) -> str:
        if getattr(getattr(websocket, "application_state", None), "name", "") != "CONNECTED":
            await websocket.accept()
        connection_id = str(uuid.uuid4())
        async with self._lock:
            self._connections.setdefault(user_id, {})[connection_id] = websocket
        return connection_id

    async def disconnect(self, user_id: str, connection_id: str):
        async with self._lock:
            conns = self._connections.get(user_id)
            if conns is None:
                return
            conns.pop(connection_id, None)
            if not conns:
                self._connections.pop(user_id, None)

    async def has_connections(self, user_id: str) -> bool:
        async with self._lock:
            return bool(self._connections.get(user_id))

    async def send_to_user(self, user_id: str, event: JAXEvent):
        async with self._lock:
            conns = list(self._connections.get(user_id, {}).items())
        for connection_id, ws in conns:
            try:
                await ws.send_json(event.model_dump())
            except Exception:
                await self.disconnect(user_id, connection_id)

    async def connected_user_ids(self) -> list[str]:
        async with self._lock:
            return list(self._connections.keys())


ws_hub = WebSocketHub()
