"""Pantalla de Memoria — API de lectura de hechos. Plan:
docs/superpowers/plans/2026-09-20-memoria-admin.md (Task 3). Spec:
docs/superpowers/specs/2026-09-18-memoria-admin-design.md.

«La memoria sin procedencia es otra forma de suposición» (Protocolo de la
Memoria Viva) -- cada hecho devuelve su procedencia SIEMPRE, aunque sea None.

Acceso a datos: SQL propio contra `db.connection.get_pool()` (mismo patrón
que config_admin.py), no `MemoryDB.get_facts()` -- ese método arma la
consulta en Python con un ORDER BY (`importance`) que no coincide con
`idx_facts_revision`, así que no hay una constante de módulo que reproduzca
"la consulta real" tal como pide el test del EXPLAIN
(test_memoria_indices.py). Con SQL propio, la que se ve acá ES la que corre.

Task 4 (aprobar, corregir, caducar) agrega la escritura a este módulo.
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query

from auth.middleware import require_superadmin
from auth.models import AuthUser
from db.connection import get_pool

router = APIRouter(prefix="/api/admin/memoria")

# --------------------------------------------------------------------------
# Listado
# --------------------------------------------------------------------------
# `(%s IS NULL OR is_verified = %s)`: aiomysql/pymysql sustituyen los `%s` en
# el CLIENTE antes de mandar el texto al servidor (no son prepared statements
# de verdad) -- por eso, cuando `verificado` es False/True, MariaDB VE la
# constante literal (`0 IS NULL OR is_verified = 0`) y la pliega en tiempo de
# planificación, usando `idx_facts_revision` sin filesort. Medido a mano
# contra jax_memory_test el 2026-09-20 (las tres formas: con filtro, sin
# filtro, y con IN(0,1) -- esta última NO sirve: MariaDB no la reduce a un
# rango indexado y cae a filesort). Con `verificado=None` (listado sin
# filtrar, "todo") el predicado se pliega a TRUE y esa consulta puntual SÍ
# hace filesort -- es una limitación real del índice ya fijado en la Task 1
# (is_verified como primera columna), no un descuido de esta consulta; con
# el volumen actual (116 hechos) es inofensivo. `SQL_LISTAR`/`ARGS_EJEMPLO`
# protegen el camino caliente de verdad: la cola de revisión
# (`verificado=False`, spec §1 -- "115 hechos esperan revisión").
SQL_LISTAR = (
    "SELECT id, fact_text, fact_type, confidence, is_verified, verified_by, "
    "verified_at, expires_at, "
    "(expires_at IS NOT NULL AND expires_at <= NOW()) AS vencido, "
    "created_at, source_message_id, source_facet, superseded_by "
    "FROM facts "
    "WHERE (%s IS NULL OR is_verified = %s) "
    "AND (%s = 1 OR superseded_by IS NULL) "
    "AND (%s = 1 OR expires_at IS NULL OR expires_at > NOW()) "
    "ORDER BY expires_at DESC, created_at DESC "
    "LIMIT %s"
)
# verificado=False, incluir_superados=False, incluir_vencidos=False, limite=20:
# la cola de revisión por defecto. test_memoria_indices.py corre EXPLAIN
# sobre ESTA tupla exacta.
ARGS_EJEMPLO = (False, False, False, False, 20)

SQL_CONTAR = (
    "SELECT COUNT(*) FROM facts "
    "WHERE (%s IS NULL OR is_verified = %s) "
    "AND (%s = 1 OR superseded_by IS NULL) "
    "AND (%s = 1 OR expires_at IS NULL OR expires_at > NOW())"
)

_COLUMNAS_LISTAR = (
    "id", "fact_text", "fact_type", "confidence", "is_verified", "verified_by",
    "verified_at", "expires_at", "vencido", "created_at", "source_message_id",
    "source_facet", "superseded_by",
)


def _iso(momento) -> Optional[str]:
    return momento.isoformat() if momento is not None else None


def _hecho_de_fila(fila) -> dict:
    d = dict(zip(_COLUMNAS_LISTAR, fila))
    return {
        "id": d["id"],
        "texto": d["fact_text"],
        "tipo": d["fact_type"],
        "confianza": d["confidence"],
        "verificado": bool(d["is_verified"]),
        "verificado_por": d["verified_by"],
        "verificado_at": _iso(d["verified_at"]),
        "vence_at": _iso(d["expires_at"]),
        "vencido": bool(d["vencido"]),
        "creado_at": _iso(d["created_at"]),
        "superado_por": d["superseded_by"],
        "procedencia": {
            "mensaje_id": d["source_message_id"],
            "faceta": d["source_facet"],
        },
    }


def _args_filtro(verificado, incluir_superados, incluir_vencidos):
    return (verificado, verificado, incluir_superados, incluir_vencidos)


@router.get("/hechos")
async def listar_hechos(
    verificado: Optional[bool] = Query(default=None),
    incluir_vencidos: bool = Query(default=False),
    incluir_superados: bool = Query(default=False),
    limite: int = Query(default=20, ge=1, le=500),
    user: AuthUser = Depends(require_superadmin),
):
    base = _args_filtro(verificado, incluir_superados, incluir_vencidos)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(SQL_LISTAR, (*base, limite))
            filas = await cur.fetchall()
            await cur.execute(SQL_CONTAR, base)
            total = (await cur.fetchone())[0]
    return {"hechos": [_hecho_de_fila(f) for f in filas], "total": total}
