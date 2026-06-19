from fastapi import APIRouter
from jax_engine.state import engine_state

router = APIRouter()


@router.get("/api/health")
async def health():
    state = engine_state.get_state()
    return {
        "service": "JAX Platform",
        "status": "alive",
        "las_manos": "alive" if state.las_manos_alive else "down",
        "connected_users": len(state.connected_users),
        "active_pipelines": len(state.active_pipelines),
    }
