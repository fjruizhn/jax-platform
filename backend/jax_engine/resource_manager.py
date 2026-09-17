from collections import defaultdict


class ResourceManager:
    """Sin lock (frente A, A-24): cada método es una lectura o escritura de un
    set, sin await adentro. El hueco entre can_start_pipeline y admit_pipeline
    (api/pipelines.py) tiene awaits en el medio y el lock tampoco lo cubría."""

    def __init__(self):
        self._active: dict[str, set[str]] = defaultdict(set)

    async def can_start_pipeline(self, tenant_id: str, limite: int) -> bool:
        # El límite lo decide el ajuste max_pipelines (frente C, 2026-09-16),
        # leído por request en api/pipelines.py: este objeto no toca la DB.
        return len(self._active[tenant_id]) < limite

    async def admit_pipeline(self, tenant_id: str, pipeline_id: str):
        self._active[tenant_id].add(pipeline_id)

    async def release_pipeline(self, tenant_id: str, pipeline_id: str):
        self._active[tenant_id].discard(pipeline_id)

    async def active_count(self, tenant_id: str) -> int:
        return len(self._active[tenant_id])


resource_manager = ResourceManager()
