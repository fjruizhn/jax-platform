from fastapi import APIRouter, Depends

import kill_switch
from auth.middleware import get_current_user
from auth.models import AuthUser
from api.pipelines import es_del_usuario
from jax_engine.state import engine_state

router = APIRouter(prefix="/api")


@router.get("/state")
async def get_ecosystem_state(user: AuthUser = Depends(get_current_user)):
    """Foto del ecosistema para el usuario del token.

    Task 6 S2 (2026-09-15): antes devolvia el estado entero, con los
    pipelines activos (nombre, pasos, user_id, tenant_id) y las sesiones
    conectadas de TODOS los usuarios y tenants. Ahora:
      - active_pipelines: solo los del usuario, con es_del_usuario -- la misma
        regla de los 4 endpoints por id de api/pipelines.py. En memoria solo
        entra un pipeline despues de _record_pipeline_owner (create_pipeline),
        asi que owner_ack_at no hace falta volver a leerlo de la DB aca.
      - connected_users: solo la sesion propia.
      - facets, las_manos_alive, last_health_check: globales, igual que antes.

    Costo por pedido (camino caliente, se mide en la Task 4): sin DB ni red;
    un recorrido en memoria de active_pipelines y connected_users mas un
    model_dump de lo que queda. El estado global no se modifica."""
    state = engine_state.get_state()
    datos = state.model_dump(exclude={"active_pipelines", "connected_users"})
    datos["active_pipelines"] = {
        pid: p.model_dump()
        for pid, p in state.active_pipelines.items()
        if es_del_usuario(p.user_id, p.tenant_id, user)
    }
    propia = state.connected_users.get(user.user_id)
    datos["connected_users"] = (
        {user.user_id: propia.model_dump()}
        if propia is not None and propia.tenant_id == user.tenant_id
        else {}
    )
    # Global, para todos los roles (2026-09-16, frente B): quien no es
    # superadmin tiene que saber que la Mesa está frenada. Un stat por pedido,
    # sin caché (el freno no espera un TTL).
    datos["kill_switch_active"] = kill_switch.activo()
    return datos
