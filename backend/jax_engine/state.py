import asyncio
import json
import os
from datetime import datetime
import httpx
from .schemas import (
    EcosystemState, FacetState, PipelineState, PipelineStep, UserSession, JAXEvent
)
from .events import event_bus

FACET_COLORS = {
    "jax_local": "#3b82f6",
    "jekyll":    "#6366f1",
    "hyde":      "#f97316",
    "hipatia":   "#10b981",
    "thot":      "#f59e0b",
    "kimi":      "#06b6d4",
    "ada":       "#7c3aed",
    "jacobs":    "#ffffff",
}

DEFAULT_FACETS = list(FACET_COLORS.keys())

LAS_MANOS_URL = os.getenv("LAS_MANOS_URL", "http://127.0.0.1:7777")

_JACOBS_STATUS_MAP = {
    "pending":     "pending",
    "running":     "running",
    "completed":   "completed",
    "failed":      "failed",
    "interrupted": "waiting_gate",
    "aborted":     "failed",
}

_STEP_STATUS_MAP = {
    "pending":            "pending",
    "running":            "running",
    "completed":          "completed",
    "failed":             "failed",
    "blocked":            "waiting_gate",
    "blocked_human_gate": "waiting_gate",
    "skipped":            "completed",
}


def _steps_fingerprint(steps: list[PipelineStep]) -> str:
    return "|".join(f"{s.step_id}:{s.status}" for s in steps)


class JAXEngineState:
    def __init__(self):
        self._state = EcosystemState()
        self._user_tenant_map: dict[str, str] = {}
        self._init_facets()
        self._poller_task: asyncio.Task | None = None

    def _init_facets(self):
        for name in DEFAULT_FACETS:
            self._state.facets[name] = FacetState(
                name=name,
                status="idle",
                color=FACET_COLORS[name],
            )

    def get_state(self) -> EcosystemState:
        return self._state

    def register_user(self, user_id: str, tenant_id: str, role: str):
        self._state.connected_users[user_id] = UserSession(
            user_id=user_id, tenant_id=tenant_id, role=role
        )
        self._user_tenant_map[user_id] = tenant_id

    def unregister_user(self, user_id: str):
        self._state.connected_users.pop(user_id, None)
        self._user_tenant_map.pop(user_id, None)

    async def set_facet_status(self, facet: str, status: str, tenant_id: str, user_id: str, message: str = ""):
        if facet not in self._state.facets:
            self._state.facets[facet] = FacetState(
                name=facet, color=FACET_COLORS.get(facet, "#6b7280")
            )
        self._state.facets[facet].status = status
        self._state.facets[facet].last_message = message
        self._state.facets[facet].last_update = datetime.utcnow().isoformat() + "Z"

        event = JAXEvent(
            event_type="facet_status_changed",
            tenant_id=tenant_id,
            user_id=user_id,
            payload={"facet": facet, "status": status, "message": message},
        )
        await event_bus.publish(event)

    async def upsert_pipeline(self, pipeline: PipelineState, tenant_id: str, user_id: str):
        self._state.active_pipelines[pipeline.pipeline_id] = pipeline
        event = JAXEvent(
            event_type="pipeline_step_changed",
            tenant_id=tenant_id,
            user_id=user_id,
            payload=pipeline.model_dump(),
        )
        await event_bus.publish(event)

    def remove_pipeline(self, pipeline_id: str):
        self._state.active_pipelines.pop(pipeline_id, None)

    def _parse_jacobs_pipeline(self, pid: str, existing: PipelineState, data: dict) -> PipelineState:
        p = data.get("pipeline", {})
        steps_raw = data.get("steps", [])

        jacobs_status = str(p.get("status", "running"))
        mapped_status = _JACOBS_STATUS_MAP.get(jacobs_status, "running")

        steps = []
        for s in steps_raw:
            raw_step_status = str(s.get("status", "pending"))
            mapped_step_status = _STEP_STATUS_MAP.get(raw_step_status, raw_step_status)

            started = s.get("started_at") or 0
            finished = s.get("finished_at") or 0
            duration_ms = int((finished - started) * 1000) if started and finished else 0

            output_ref = str(s.get("output_ref", "") or "")
            output_preview = ""
            if output_ref.startswith("inline:"):
                try:
                    ref_data = json.loads(output_ref[7:])
                    raw = ref_data.get("result") or ref_data.get("text") or ""
                    output_preview = str(raw)[:200]
                except Exception:
                    pass

            steps.append(PipelineStep(
                step_id=s.get("step_id", ""),
                name=f"{s.get('facet', '')} — {s.get('capability', '')}",
                status=mapped_step_status,
                facet=s.get("facet", ""),
                duration_ms=duration_ms,
                output=output_preview,
            ))

        return PipelineState(
            pipeline_id=pid,
            tenant_id=existing.tenant_id,
            name=p.get("name", existing.name),
            status=mapped_status,
            steps=steps,
            created_at=existing.created_at,
            updated_at=datetime.utcnow().isoformat() + "Z",
        )

    async def _poll_las_manos(self):
        async with httpx.AsyncClient(timeout=5.0) as client:
            while True:
                try:
                    r = await client.get(f"{LAS_MANOS_URL}/health")
                    alive = r.status_code == 200
                except Exception:
                    alive = False

                if alive != self._state.las_manos_alive:
                    self._state.las_manos_alive = alive
                    self._state.last_health_check = datetime.utcnow().isoformat() + "Z"
                    event = JAXEvent(
                        event_type="las_manos_health_changed",
                        tenant_id="1",
                        user_id="1",
                        payload={"alive": alive},
                    )
                    await event_bus.publish(event)

                await asyncio.sleep(30)

    async def _poll_pipelines(self):
        async with httpx.AsyncClient(timeout=5.0) as client:
            while True:
                for pid, pipeline in list(self._state.active_pipelines.items()):
                    if pipeline.status not in ("running", "waiting_gate"):
                        continue
                    try:
                        r = await client.get(f"{LAS_MANOS_URL}/jacobs/pipeline/{pid}")
                        if r.status_code != 200:
                            continue
                        data = r.json()
                        updated = self._parse_jacobs_pipeline(pid, pipeline, data)

                        old_fp = _steps_fingerprint(pipeline.steps)
                        new_fp = _steps_fingerprint(updated.steps)
                        if updated.status == pipeline.status and old_fp == new_fp:
                            continue

                        prev_status = pipeline.status
                        await self.upsert_pipeline(updated, pipeline.tenant_id, "1")

                        if updated.status == "waiting_gate" and prev_status != "waiting_gate":
                            gate_event = JAXEvent(
                                event_type="human_gate_requested",
                                tenant_id=pipeline.tenant_id,
                                user_id="1",
                                payload={
                                    "pipeline_id": pid,
                                    "message": "Jacobs espera aprobación para continuar",
                                },
                            )
                            await event_bus.publish(gate_event)

                        if updated.status in ("completed", "failed"):
                            self.remove_pipeline(pid)

                    except Exception:
                        pass

                await asyncio.sleep(5)

    def start_background_tasks(self):
        loop = asyncio.get_event_loop()
        loop.create_task(self._poll_las_manos())
        loop.create_task(self._poll_pipelines())


engine_state = JAXEngineState()
