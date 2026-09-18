import asyncio
import json
import logging
import os
from tiempo import utc_ahora
import httpx
import aviso_pipeline
from http_client import get_http_client
from credencial_las_manos import encabezados_las_manos
from db.connection import get_pool
from .schemas import (
    EcosystemState, FacetState, PipelineState, PipelineStep, UserSession, JAXEvent
)
from .events import event_bus
from .resource_manager import resource_manager

logger = logging.getLogger(__name__)

# Solo el orden y el conjunto de facetas conocidas; identidad y color viven en
# la tabla `facet` (display_name) y en el tema del frontend (token).
DEFAULT_FACETS = ["jax_local", "jekyll", "hyde", "hipatia", "thot", "kimi", "ada", "jacobs"]

LAS_MANOS_URL = os.getenv("LAS_MANOS_URL", "http://127.0.0.1:7777")

_JACOBS_STATUS_MAP = {
    "pending":     "pending",
    "running":     "running",
    "completed":   "completed",
    "failed":      "failed",
    "interrupted": "waiting_gate",
    "aborted":     "failed",
    # Importante B (revisión final, 2026-09-18): faltaba -- .get(jacobs_status,
    # "running") dejaba un pipeline expired (jacobs/reaper.py, T4 2026-08-19)
    # mapeado a "running" para siempre. El poller nunca entraba al `if
    # updated.status in ("completed", "failed")`: remove_pipeline() no
    # corría, el cupo del tenant quedaba fugado para siempre, y no había
    # aviso por correo/Telegram -- justo el pipeline que murió solo, que es
    # el caso que nadie mira. Mismo trato que "aborted": terminó sin éxito,
    # no por decisión humana explícita, pero terminó.
    "expired":     "failed",
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
        self._init_facets()
        self._poller_task: asyncio.Task | None = None

    def _init_facets(self):
        for name in DEFAULT_FACETS:
            self._state.facets[name] = FacetState(name=name, status="idle")

    async def cargar_nombres_de_facetas(self):
        """A-48. Una lectura al arrancar (main.py lifespan, después de
        run_seed, que corre después de run_migrations). Invalidación: reinicio -- el único escritor de
        facet.display_name son las migraciones del arranque
        (tests/test_facetas_nombres.py lo fija). Sin base, el lifespan ya
        falló antes: no hay estado sin nombres que servir."""
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT `key`, display_name FROM facet")
                filas = await cur.fetchall()
        for clave, nombre in filas:
            faceta = self._state.facets.get(clave)
            if faceta is not None:
                faceta.display_name = nombre

    def get_state(self) -> EcosystemState:
        return self._state

    def register_user(self, user_id: str, tenant_id: str, role: str):
        self._state.connected_users[user_id] = UserSession(
            user_id=user_id, tenant_id=tenant_id, role=role
        )

    def unregister_user(self, user_id: str):
        self._state.connected_users.pop(user_id, None)

    async def set_facet_status(self, facet: str, status: str, tenant_id: str, user_id: str, message: str = ""):
        if facet not in self._state.facets:
            self._state.facets[facet] = FacetState(name=facet)
        self._state.facets[facet].status = status
        self._state.facets[facet].last_message = message
        self._state.facets[facet].last_update = utc_ahora().isoformat() + "Z"

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

    async def continuar_pipeline(self, pipeline: PipelineState, tenant_id: str, user_id: str, continuacion: dict):
        """Spec 2026-09-17 §6.2: vuelve a la lista de activos (el poller lo
        sigue) y publica pipeline_continued con la época y los pasos reusados."""
        self._state.active_pipelines[pipeline.pipeline_id] = pipeline
        event = JAXEvent(
            event_type="pipeline_continued",
            tenant_id=tenant_id,
            user_id=user_id,
            payload={**pipeline.model_dump(), **continuacion},
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
                except Exception:  # fail-soft: preview cosmetico de un output_ref para mostrar en UI; si no parsea queda string vacio, nada depende de este valor
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
            user_id=existing.user_id,
            name=p.get("name", existing.name),
            status=mapped_status,
            steps=steps,
            created_at=existing.created_at,
            updated_at=utc_ahora().isoformat() + "Z",
        )

    async def _check_las_manos_health(self, client: httpx.AsyncClient):
        try:
            r = await client.get(f"{LAS_MANOS_URL}/health", timeout=5.0)
            alive = r.status_code == 200
        except Exception:  # fail-soft: cualquier error de la sonda ES la señal 'caído' (alive=False) y se emite las_manos_health_changed
            alive = False

        if alive != self._state.las_manos_alive:
            self._state.las_manos_alive = alive
            self._state.last_health_check = utc_ahora().isoformat() + "Z"
            for user_id, session in list(self._state.connected_users.items()):
                event = JAXEvent(
                    event_type="las_manos_health_changed",
                    tenant_id=session.tenant_id,
                    user_id=user_id,
                    payload={"alive": alive},
                )
                await event_bus.publish(event)

    async def _poll_las_manos(self):
        while True:
            client = await get_http_client()
            await self._check_las_manos_health(client)
            await asyncio.sleep(30)

    async def _poll_pipelines(self):
        while True:
            client = await get_http_client()
            for pid, pipeline in list(self._state.active_pipelines.items()):
                if pipeline.status not in ("running", "waiting_gate"):
                    continue
                await self._poll_one_pipeline(client, pid, pipeline)

            await asyncio.sleep(5)

    async def _poll_one_pipeline(self, client: httpx.AsyncClient, pid: str, pipeline: PipelineState):
        try:
            r = await client.get(f"{LAS_MANOS_URL}/jacobs/pipeline/{pid}", timeout=5.0,
                                 headers=encabezados_las_manos())
            if r.status_code != 200:
                return
            data = r.json()
            updated = self._parse_jacobs_pipeline(pid, pipeline, data)

            old_fp = _steps_fingerprint(pipeline.steps)
            new_fp = _steps_fingerprint(updated.steps)
            if updated.status == pipeline.status and old_fp == new_fp:
                return

            prev_status = pipeline.status
            await self.upsert_pipeline(updated, pipeline.tenant_id, updated.user_id)

            if updated.status == "waiting_gate" and prev_status != "waiting_gate":
                gate_event = JAXEvent(
                    event_type="human_gate_requested",
                    tenant_id=pipeline.tenant_id,
                    user_id=updated.user_id,
                    payload={"pipeline_id": pid},
                )
                await event_bus.publish(gate_event)

            if updated.status in ("completed", "failed"):
                self.remove_pipeline(pid)
                # cancel_pipeline() ya liberaba el slot del tenant; una
                # pipeline que termina SOLA (no cancelada) nunca lo hacía,
                # así que cada una consumía uno de los 3 cupos concurrentes
                # para siempre.
                # Sin try propio (Task 3, 2026-09-15, clase c del triage):
                # release_pipeline es un set.discard sobre un defaultdict(set)
                # (resource_manager.py) -- no puede lanzar
                # Exception. El try que habia aca describia un riesgo que el
                # codigo no tiene.
                await resource_manager.release_pipeline(pipeline.tenant_id, pid)
                # Bloqueante 2 (revisión final, 2026-09-18): este aviso vivía
                # ANTES de remove_pipeline/release_pipeline, dentro del MISMO
                # except Exception: pass de abajo. encolar_aviso_fin_pipeline
                # SÍ puede lanzar (asyncio.create_task sin loop corriendo,
                # una excepción de _TAREAS_EN_VUELO.add) -- a diferencia de
                # release_pipeline, que el comentario de arriba describe
                # correctamente. Si lanzaba ahí, el except se lo tragaba
                # ANTES de liberar el cupo: el pipeline nunca soltaba su
                # lugar, el defecto exacto que este mismo bloque ya había
                # arreglado una vez (Task 3, 2026-09-15). Movido a DESPUÉS de
                # liberar el cupo: si esto lanza, el cupo ya es libre y el
                # fail-soft de abajo solo pierde el aviso de esta corrida, no
                # el cupo del tenant.
                # Task 8 (2026-09-18): aviso por correo al dueño. Síncrona y
                # no bloqueante -- dispara su propia Task y suelta el
                # control ya mismo (ver aviso_pipeline.py); un SMTP lento no
                # puede frenar este tick ni los de las demás pipelines.
                aviso_pipeline.encolar_aviso_fin_pipeline(
                    pid, pipeline.tenant_id, updated.user_id, updated.status, updated.name)

        except Exception:  # fail-soft: cubre fetch/parse HTTP de UNA pipeline en _poll_one_pipeline; un fallo transitorio no debe tumbar el polling de las demás pipelines activas en este ciclo — la liberación de cupo de arriba es en memoria y no lanza (resource_manager.py), y ahora corre ANTES del aviso (bloqueante 2, 2026-09-18)
            pass

    def start_background_tasks(self):
        loop = asyncio.get_event_loop()
        loop.create_task(self._poll_las_manos())
        loop.create_task(self._poll_pipelines())


engine_state = JAXEngineState()
