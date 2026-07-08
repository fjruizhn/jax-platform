import asyncio
from fastapi import WebSocket
from .schemas import JAXEvent


class WebSocketHub:
    def __init__(self):
        self._connections: dict[str, WebSocket] = {}
        self._lock = asyncio.Lock()

    async def connect(self, user_id: str, websocket: WebSocket):
        if getattr(getattr(websocket, "application_state", None), "name", "") != "CONNECTED":
            await websocket.accept()
        async with self._lock:
            self._connections[user_id] = websocket

    async def disconnect(self, user_id: str):
        async with self._lock:
            self._connections.pop(user_id, None)

    async def send_to_user(self, user_id: str, event: JAXEvent):
        async with self._lock:
            ws = self._connections.get(user_id)
        if ws:
            try:
                await ws.send_json(event.model_dump())
            except Exception:
                await self.disconnect(user_id)

    async def broadcast_to_tenant(self, tenant_id: str, event: JAXEvent, user_tenant_map: dict[str, str]):
        async with self._lock:
            targets = [uid for uid, tid in user_tenant_map.items() if tid == tenant_id]
            connections = {uid: self._connections[uid] for uid in targets if uid in self._connections}
        for uid, ws in connections.items():
            try:
                await ws.send_json(event.model_dump())
            except Exception:
                await self.disconnect(uid)

    async def connected_user_ids(self) -> list[str]:
        async with self._lock:
            return list(self._connections.keys())


ws_hub = WebSocketHub()
