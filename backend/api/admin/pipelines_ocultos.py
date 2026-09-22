"""Pipelines ocultos (spec 2026-09-22-descartar-pipelines §4-5): la vista del
superadmin. Todos los usuarios, paginada, por idx_pipelines_ocultos.

"Un hidden no aparece en ninguna lista de su dueño: para él, ya no existe"
(spec §4) -- esta es la ÚNICA vista que los muestra, y sólo al superadmin.
"""
from fastapi import APIRouter, Depends, Query

from auth.middleware import require_superadmin
from auth.models import AuthUser
from db.connection import get_pool

router = APIRouter(prefix="/api/admin/pipelines")

LIMITE_MAX = 50

SQL_OCULTOS = (
    "SELECT pipeline_id, name, user_id, tenant_id, descartado_por, descartado_at, created_at "
    "FROM jacobs_pipelines WHERE status='hidden' "
    "ORDER BY descartado_at DESC LIMIT %s OFFSET %s"
)


@router.get("/ocultos")
async def listar_ocultos(
    limite: int = Query(LIMITE_MAX, ge=1, le=LIMITE_MAX),
    offset: int = Query(0, ge=0),
    user: AuthUser = Depends(require_superadmin),
):
    pool = await get_pool()
    async with pool.acquire() as conn, conn.cursor() as cur:
        # limite+1 (LAS CUATRO/cache): sabe si hay página siguiente sin un
        # segundo COUNT(*), mismo patrón que list_pipelines (api/pipelines.py).
        await cur.execute(SQL_OCULTOS, (limite + 1, offset))
        filas = await cur.fetchall()
    campos = ("pipeline_id", "name", "user_id", "tenant_id", "descartado_por",
              "descartado_at", "created_at")
    return {"pipelines": [dict(zip(campos, f)) for f in filas[:limite]],
            "hay_mas": len(filas) > limite}
