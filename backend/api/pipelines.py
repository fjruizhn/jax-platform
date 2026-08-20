import os
import time
import uuid
from fastapi import APIRouter, Depends, HTTPException, Request, status
from auth.middleware import get_current_user
from auth.models import AuthUser
from db.connection import get_pool
from http_client import get_http_client
from jax_engine.resource_manager import resource_manager
from jax_engine.state import engine_state
from jax_engine.schemas import PipelineState

router = APIRouter(prefix="/api/pipelines")

JACOBS_URL = os.getenv("JACOBS_URL", "http://127.0.0.1:7777/jacobs")

# _plan_builder.build() en Jacobs llama a un LLM real para armar el plan,
# incluso en dry_run (ver T1.c). Medido 2026-08-19: 17s, 23.4s, 27.6s,
# 29.6s en 4 corridas — la latencia de un cliente HTTP externo (probable
# grounding/web de hipatia) no tiene cota firme. 10s cortaba conexiones
# legítimas: jax-platform devolvía 502 mientras Jacobs seguía corriendo y
# persistía el pipeline sin que el cliente se enterara del pipeline_id
# (huérfano confirmado, sonda T1.a). Margen ~2x sobre el máximo medido.
JACOBS_PIPELINE_TIMEOUT = float(os.getenv("JACOBS_PIPELINE_TIMEOUT", "60.0"))

# engine_state.active_pipelines (memoria) se descarta apenas la pipeline
# termina -- justo cuando normalmente se pide /results. owner_ack_at en
# jacobs_pipelines (misma DB fisica jax_memory que ya comparten ambos
# servicios) es la fuente de ownership que sobrevive a eso.
#
# Ronda 5 (2026-08-20, T1): reemplaza el owner file en
# ~/jax/pipelines/{id}_owner.json (deuda con dientes documentada en
# jacobs/reaper.py de jax -- el reaper cruzaba de repo leyendo ese
# archivo). UPDATE/SELECT directos contra jacobs_pipelines, mismo pool
# de DB que ya usa este servicio para capability/motor -- no un
# request HTTP a Jacobs por cada chequeo de ownership (eso hubiera
# agregado un salto de red a cada GET/resume/cancel, y de cualquier
# forma esas rutas ya dependen de que Jacobs este arriba para el
# reenvio real).
async def _record_pipeline_owner(pipeline_id: str, tenant_id: str, user_id: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE jacobs_pipelines SET owner_ack_at=%s "
                "WHERE pipeline_id=%s AND user_id=%s AND tenant_id=%s",
                (time.time(), pipeline_id, user_id, tenant_id),
            )


async def _require_pipeline_owner(pipeline_id: str, user: AuthUser):
    # 404 (no 403) para no confirmarle a un no-dueño que el pipeline_id
    # existe. Pipelines creadas antes de esta migración no tienen
    # owner_ack_at poblado y también devuelven 404 -- costo único de la
    # migración, no un bug (mismo criterio que regia con el owner file).
    try:
        uuid.UUID(pipeline_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="pipeline_id inválido")
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT user_id, tenant_id, owner_ack_at FROM jacobs_pipelines WHERE pipeline_id=%s",
                (pipeline_id,),
            )
            row = await cur.fetchone()
    if (
        row is None
        or row[2] is None
        or row[0] != user.user_id
        or row[1] != user.tenant_id
    ):
        raise HTTPException(status_code=404, detail="Pipeline no encontrado")


@router.get("")
async def list_pipelines(user: AuthUser = Depends(get_current_user)):
    client = await get_http_client()
    try:
        r = await client.get(f"{JACOBS_URL}/pipeline", timeout=5.0)
        return r.json()
    except Exception:
        return {"pipelines": [], "error": "LAS MANOS no disponible"}


@router.post("")
async def create_pipeline(request: Request, user: AuthUser = Depends(get_current_user)):
    if not await resource_manager.can_start_pipeline(user.tenant_id):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Límite de 3 pipelines concurrentes alcanzado",
        )
    body = await request.json()
    body["user_id"] = user.user_id
    body["tenant_id"] = user.tenant_id
    client = await get_http_client()
    try:
        r = await client.post(f"{JACOBS_URL}/pipeline", json=body, timeout=JACOBS_PIPELINE_TIMEOUT)
        data = r.json()
        if r.status_code != 200:
            # Jacobs rechazó el pipeline (ej. 422 límite de concurrentes,
            # 423 kill switch) — propagar el error real en vez de
            # reenviarlo como 200 con el body de error de Jacobs.
            raise HTTPException(status_code=r.status_code, detail=data.get("detail", "Error de Jacobs"))
        pipeline_id = data.get("pipeline_id")
        if pipeline_id:
            # Antes de admitir el recurso o publicar el evento de WS
            # (que ya revela pipeline_id al dueño) — así un fallo acá
            # aborta limpio, sin slot de tenant huérfano ni owner file
            # faltante para un id que el cliente ya recibió.
            await _record_pipeline_owner(pipeline_id, user.tenant_id, user.user_id)
            await resource_manager.admit_pipeline(user.tenant_id, pipeline_id)
            initial = PipelineState(
                pipeline_id=pipeline_id,
                tenant_id=user.tenant_id,
                user_id=user.user_id,
                name=body.get("name", "Pipeline"),
                status="running",
            )
            await engine_state.upsert_pipeline(initial, user.tenant_id, user.user_id)
        return data
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/{pipeline_id}/results")
async def get_pipeline_results(pipeline_id: str, user: AuthUser = Depends(get_current_user)):
    await _require_pipeline_owner(pipeline_id, user)
    client = await get_http_client()
    try:
        r = await client.get(f"{JACOBS_URL}/pipeline/{pipeline_id}/results", timeout=10.0)
        return r.json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/{pipeline_id}")
async def get_pipeline(pipeline_id: str, user: AuthUser = Depends(get_current_user)):
    await _require_pipeline_owner(pipeline_id, user)
    client = await get_http_client()
    try:
        r = await client.get(f"{JACOBS_URL}/pipeline/{pipeline_id}", timeout=5.0)
        return r.json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.post("/{pipeline_id}/resume")
async def resume_pipeline(
    pipeline_id: str,
    user: AuthUser = Depends(get_current_user),
):
    await _require_pipeline_owner(pipeline_id, user)
    client = await get_http_client()
    try:
        r = await client.post(
            f"{JACOBS_URL}/pipeline/{pipeline_id}/resume",
            json={"invoked_by": "Fernando", "user_id": user.user_id, "tenant_id": user.tenant_id},
            timeout=10.0,
        )
        return r.json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.post("/{pipeline_id}/cancel")
async def cancel_pipeline(
    pipeline_id: str,
    user: AuthUser = Depends(get_current_user),
):
    await _require_pipeline_owner(pipeline_id, user)
    client = await get_http_client()
    try:
        r = await client.post(f"{JACOBS_URL}/pipeline/{pipeline_id}/cancel", timeout=10.0)
        if r.status_code == 200:
            engine_state.remove_pipeline(pipeline_id)
            await resource_manager.release_pipeline(user.tenant_id, pipeline_id)
        return r.json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
