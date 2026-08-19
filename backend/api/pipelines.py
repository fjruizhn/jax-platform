import json
import os
import uuid
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Request, status
from auth.middleware import get_current_user
from auth.models import AuthUser
from http_client import get_http_client
from jax_engine.resource_manager import resource_manager
from jax_engine.state import engine_state
from jax_engine.schemas import PipelineState

router = APIRouter(prefix="/api/pipelines")

JACOBS_URL = os.getenv("JACOBS_URL", "http://127.0.0.1:7777/jacobs")

# engine_state.active_pipelines (memoria) se descarta apenas la pipeline
# termina -- justo cuando normalmente se pide /results. Este registro en
# disco es la única fuente de ownership que sobrevive a eso; mismo patrón
# que web-task-{id}_owner.json en api/command.py.
PIPELINES_DIR = Path.home() / "jax" / "pipelines"


def _pipeline_owner_file(pipeline_id: str) -> Path:
    return PIPELINES_DIR / f"{pipeline_id}_owner.json"


def _record_pipeline_owner(pipeline_id: str, tenant_id: str, user_id: str):
    PIPELINES_DIR.mkdir(parents=True, exist_ok=True)
    _pipeline_owner_file(pipeline_id).write_text(json.dumps({"tenant_id": tenant_id, "user_id": user_id}))


def _require_pipeline_owner(pipeline_id: str, user: AuthUser):
    # 404 (no 403) para no confirmarle a un no-dueño que el pipeline_id
    # existe. Pipelines creadas antes de este cambio no tienen owner file y
    # también devuelven 404 -- costo único de la migración, no un bug.
    try:
        uuid.UUID(pipeline_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="pipeline_id inválido")
    try:
        owner = json.loads(_pipeline_owner_file(pipeline_id).read_text())
    except (OSError, ValueError):
        raise HTTPException(status_code=404, detail="Pipeline no encontrado")
    if (
        not isinstance(owner, dict)
        or owner.get("user_id") != user.user_id
        or owner.get("tenant_id") != user.tenant_id
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
        r = await client.post(f"{JACOBS_URL}/pipeline", json=body, timeout=10.0)
        data = r.json()
        if r.status_code == 200:
            pipeline_id = data.get("pipeline_id")
            if pipeline_id:
                # Antes de admitir el recurso o publicar el evento de WS
                # (que ya revela pipeline_id al dueño) — así un fallo acá
                # aborta limpio, sin slot de tenant huérfano ni owner file
                # faltante para un id que el cliente ya recibió.
                _record_pipeline_owner(pipeline_id, user.tenant_id, user.user_id)
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
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/{pipeline_id}/results")
async def get_pipeline_results(pipeline_id: str, user: AuthUser = Depends(get_current_user)):
    _require_pipeline_owner(pipeline_id, user)
    client = await get_http_client()
    try:
        r = await client.get(f"{JACOBS_URL}/pipeline/{pipeline_id}/results", timeout=10.0)
        return r.json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/{pipeline_id}")
async def get_pipeline(pipeline_id: str, user: AuthUser = Depends(get_current_user)):
    _require_pipeline_owner(pipeline_id, user)
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
    _require_pipeline_owner(pipeline_id, user)
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
    _require_pipeline_owner(pipeline_id, user)
    client = await get_http_client()
    try:
        r = await client.post(f"{JACOBS_URL}/pipeline/{pipeline_id}/cancel", timeout=10.0)
        if r.status_code == 200:
            engine_state.remove_pipeline(pipeline_id)
            await resource_manager.release_pipeline(user.tenant_id, pipeline_id)
        return r.json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
