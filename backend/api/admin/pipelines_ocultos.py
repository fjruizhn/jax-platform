"""Pipelines ocultos Y descartados (spec 2026-09-22-descartar-pipelines §4-5;
GET /descartados agregado 2026-09-22 al cerrar los dos huecos de la revisión
final): las dos vistas del superadmin sobre TODOS los usuarios, paginadas.

"Un hidden no aparece en ninguna lista de su dueño: para él, ya no existe"
(spec §4) -- /ocultos es la ÚNICA vista que los muestra, y sólo al
superadmin. /descartados es su equivalente para 'discarded': el superadmin
ya podía ocultar/restaurar el descartado de OTRO usuario (POST
/api/pipelines/{id}/hide, sin exigir dueño), pero no tenía forma de VERLO
si no era el que lo había descartado -- GET /api/pipelines?estado=discarded
filtra por el usuario del token. Sin esta vista, un superadmin que
restauraba el oculto de otro no podía volver a ocultarlo desde la UI.
"""
from fastapi import APIRouter, Depends, Query

from auth.middleware import require_superadmin
from auth.models import AuthUser
from db.connection import get_pool
from api.paginacion_descartados import (
    CURSOR_MAX, ORDEN, consulta_y_parametros, cursor_siguiente, exigir_cursor_sin_offset,
)

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
    # Fix round 1, Ruling 13(d) (2026-09-22): "has_more", NO "hay_mas" --
    # el contrato ya existente de list_pipelines (api/pipelines.py). Dos
    # nombres para el mismo campo de paginación es el tipo de cosa que un
    # cliente descubre en producción, no en review.
    return {"pipelines": [dict(zip(campos, f)) for f in filas[:limite]],
            "has_more": len(filas) > limite}


# 2026-09-22 (cierre de los dos huecos de la revisión final de Descartar
# Pipelines, punto 1): el equivalente de SQL_OCULTOS para status='discarded'
# -- MISMA forma (campos, paginación limite+1), sólo cambia el status del
# WHERE. Reusa `idx_pipelines_ocultos` (status, descartado_at), NO
# `idx_pipelines_descartados` (user_id, tenant_id, status, descartado_at):
# ese índice sirve a la vista de descartados DEL DUEÑO
# (SQL_DESCARTADOS_DEL_USUARIO, api/pipelines.py), que SÍ filtra por
# user_id/tenant_id -- esta vista es de TODOS los usuarios, igual que
# /ocultos, así que el índice que evita el filesort es el mismo que ya usa
# su hermano. Verificado con EXPLAIN contra datos con forma de producción
# (test_explain_descartados_admin_usa_idx_pipelines_ocultos_sin_filesort,
# tests/test_pipelines_descartados_admin.py) -- ver el docstring de ese
# archivo para la medición completa y por qué NO es el índice que menciona
# el encargo original.
#
# 2026-09-23: orden TOTAL (descartado_at DESC, pipeline_id DESC) y
# paginación por cursor además de offset -- ver api/paginacion_descartados.py
# y docs/carga-descartados-cursor-2026-09-23.md.
SQL_DESCARTADOS_ADMIN_BASE = (
    "SELECT pipeline_id, name, user_id, tenant_id, descartado_por, descartado_at, created_at "
    "FROM jacobs_pipelines WHERE status='discarded' "
)
SQL_DESCARTADOS_ADMIN = SQL_DESCARTADOS_ADMIN_BASE + ORDEN + "LIMIT %s OFFSET %s"


@router.get("/descartados")
async def listar_descartados_admin(
    limite: int = Query(LIMITE_MAX, ge=1, le=LIMITE_MAX),
    offset: int = Query(0, ge=0),
    cursor: str | None = Query(None, min_length=1, max_length=CURSOR_MAX),
    user: AuthUser = Depends(require_superadmin),
):
    exigir_cursor_sin_offset(cursor, offset)
    consulta, params = consulta_y_parametros(SQL_DESCARTADOS_ADMIN_BASE, (), limite, offset, cursor)
    pool = await get_pool()
    async with pool.acquire() as conn, conn.cursor() as cur:
        await cur.execute(consulta, params)
        filas = await cur.fetchall()
    campos = ("pipeline_id", "name", "user_id", "tenant_id", "descartado_por",
              "descartado_at", "created_at")
    pagina = filas[:limite]
    hay_mas = len(filas) > limite
    return {"pipelines": [dict(zip(campos, f)) for f in pagina],
            "has_more": hay_mas,
            "cursor_siguiente": cursor_siguiente(pagina, hay_mas, idx_fecha=5)}
