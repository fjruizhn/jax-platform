from facet_health import write_failure_stats
from fastapi import APIRouter
from jax_engine.state import engine_state
from jax_engine.websocket_hub import ws_hub

router = APIRouter()


@router.get("/api/health")
async def health():
    state = engine_state.get_state()
    ws_users = await ws_hub.connected_user_ids()
    return {
        "service": "JAX Platform",
        "status": "alive",
        "las_manos": "alive" if state.las_manos_alive else "down",
        "connected_users": len(state.connected_users),
        "ws_connected_users": len(ws_users),
        "active_pipelines": len(state.active_pipelines),
        "facet_health_writer": write_failure_stats(),
    }
