import os
import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from auth.middleware import get_current_user
from auth.models import AuthUser
from jax_engine.resource_manager import resource_manager
from jax_engine.state import engine_state
from jax_engine.schemas import PipelineState

router = APIRouter(prefix="/api/pipelines")

JACOBS_URL = os.getenv("JACOBS_URL", "http://127.0.0.1:7777/jacobs")


@router.get("")
async def list_pipelines(user: AuthUser = Depends(get_current_user)):
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            r = await client.get(f"{JACOBS_URL}/pipeline")
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
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            r = await client.post(f"{JACOBS_URL}/pipeline", json=body)
            data = r.json()
            if r.status_code == 200:
                pipeline_id = data.get("pipeline_id")
                if pipeline_id:
                    await resource_manager.admit_pipeline(user.tenant_id, pipeline_id)
                    initial = PipelineState(
                        pipeline_id=pipeline_id,
                        tenant_id=user.tenant_id,
                        name=body.get("name", "Pipeline"),
                        status="running",
                    )
                    await engine_state.upsert_pipeline(initial, user.tenant_id, user.user_id)
            return data
        except Exception as e:
            raise HTTPException(status_code=502, detail=str(e))


@router.get("/{pipeline_id}/results")
async def get_pipeline_results(pipeline_id: str, user: AuthUser = Depends(get_current_user)):
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            r = await client.get(f"{JACOBS_URL}/pipeline/{pipeline_id}/results")
            return r.json()
        except Exception as e:
            raise HTTPException(status_code=502, detail=str(e))


@router.get("/{pipeline_id}")
async def get_pipeline(pipeline_id: str, user: AuthUser = Depends(get_current_user)):
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            r = await client.get(f"{JACOBS_URL}/pipeline/{pipeline_id}")
            return r.json()
        except Exception as e:
            raise HTTPException(status_code=502, detail=str(e))


@router.post("/{pipeline_id}/resume")
async def resume_pipeline(
    pipeline_id: str,
    user: AuthUser = Depends(get_current_user),
):
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            r = await client.post(
                f"{JACOBS_URL}/pipeline/{pipeline_id}/resume",
                json={"invoked_by": "Fernando"},
            )
            return r.json()
        except Exception as e:
            raise HTTPException(status_code=502, detail=str(e))


@router.post("/{pipeline_id}/cancel")
async def cancel_pipeline(
    pipeline_id: str,
    user: AuthUser = Depends(get_current_user),
):
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            r = await client.post(f"{JACOBS_URL}/pipeline/{pipeline_id}/cancel")
            if r.status_code == 200:
                engine_state.remove_pipeline(pipeline_id)
                await resource_manager.release_pipeline(user.tenant_id, pipeline_id)
            return r.json()
        except Exception as e:
            raise HTTPException(status_code=502, detail=str(e))
