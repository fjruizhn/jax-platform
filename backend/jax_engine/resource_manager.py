import asyncio
from collections import defaultdict

MAX_PIPELINES_PER_TENANT = 3


class ResourceManager:
    def __init__(self):
        self._active: dict[str, set[str]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def can_start_pipeline(self, tenant_id: str) -> bool:
        async with self._lock:
            return len(self._active[tenant_id]) < MAX_PIPELINES_PER_TENANT

    async def admit_pipeline(self, tenant_id: str, pipeline_id: str):
        async with self._lock:
            self._active[tenant_id].add(pipeline_id)

    async def release_pipeline(self, tenant_id: str, pipeline_id: str):
        async with self._lock:
            self._active[tenant_id].discard(pipeline_id)

    async def active_count(self, tenant_id: str) -> int:
        async with self._lock:
            return len(self._active[tenant_id])


resource_manager = ResourceManager()
