from fastapi import APIRouter, Depends
from auth.middleware import get_current_user
from auth.models import AuthUser
from jax_engine.state import engine_state

router = APIRouter(prefix="/api")


@router.get("/state")
async def get_ecosystem_state(user: AuthUser = Depends(get_current_user)):
    state = engine_state.get_state()
    return state.model_dump()
