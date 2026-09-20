"""Pantalla de Memoria — API de lectura, aprobación, corrección y caducidad
de `facts` (jax_memory). Plan: docs/superpowers/plans/2026-09-20-memoria-admin.md
(Task 3 y 4). Spec: docs/superpowers/specs/2026-09-18-memoria-admin-design.md.

«La memoria sin procedencia es otra forma de suposición» (Protocolo de la
Memoria Viva) -- cada hecho devuelve su procedencia SIEMPRE, aunque sea None.

No hay aprobación automática (spec §2.2) ni endpoint de borrado (spec §5):
un hecho falso no se borra en silencio, se marca como corregido.

Dos accesos a datos, a propósito:
  - LECTURA (`SQL_LISTAR`/`SQL_CONTAR`): SQL propio contra `db.connection.get_pool()`
    (mismo patrón que config_admin.py), no `MemoryDB.get_facts()` -- ese método
    arma la consulta en Python con un ORDER BY (`importance`) que no coincide
    con `idx_facts_revision`, así que no hay una constante de módulo que
    reproduzca "la consulta real" tal como pide el test del EXPLAIN
    (test_memoria_indices.py). Con SQL propio, la que se ve acá ES la que
    corre.
  - ESCRITURA de un solo UPDATE (aprobar, caducar): se reusa `MemoryDB`
    (helper de api/chat.py, sin duplicar el sys.path.insert -- corrección de
    Fernando 2026-09-20) porque ya tiene `verify_fact`/`expire_fact`.
  - Corregir es la excepción: crea un fact Y supera al viejo. Antecedente
    real, jax-platform#107 (un evento fuera de la transacción del turno fue
    una carrera) -- por eso NO se compone `MemoryDB.save_fact` +
    `MemoryDB.supersede_fact` (cada uno abre y confirma su propia conexión):
    van los dos SQL a mano dentro de UNA transacción de
    `db/transaccion.py` (mismo pool que config_admin.py, misma base física
    que MemoryDB -- `jax_memory`/`jax_memory_test`).
"""
import json
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from api import chat as _chat_mod
from auth.middleware import require_superadmin
from auth.models import AuthUser
from db.connection import get_pool
from db.transaccion import AISLAMIENTO_ADMIN, transaccion

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


# --------------------------------------------------------------------------
# Escritura -- el que produce no aprueba (spec §2.2): `verified_by` /
# `superseded_by_user` SIEMPRE es el `user_id` de `require_superadmin`, nunca
# un valor implícito. No hay aprobación automática: ningún camino de este
# módulo pone `is_verified = TRUE` sin ese `user_id` real.
# --------------------------------------------------------------------------
async def _memoria_conectada():
    """Reusa el MemoryDB compartido de api/chat.py (mismo pool que el chat,
    misma jax_memory) -- sin un sys.path.insert nuevo (corrección de
    Fernando, 2026-09-20)."""
    if not await _chat_mod._ensure_memory():
        raise HTTPException(status_code=503, detail="memoria_no_disponible")
    return _chat_mod._memory


class AprobarBody(BaseModel):
    ids: list[int]


@router.post("/hechos/aprobar")
async def aprobar_hechos(body: AprobarBody, user: AuthUser = Depends(require_superadmin)):
    if not body.ids:
        return {"aprobados": 0}
    memoria = await _memoria_conectada()
    autor = int(user.user_id)
    aprobados = 0
    for fact_id in body.ids:
        if await memoria.verify_fact(fact_id, autor):
            aprobados += 1
    return {"aprobados": aprobados}


class CorregirBody(BaseModel):
    texto: str


@router.post("/hechos/{fact_id}/corregir")
async def corregir_hecho(fact_id: int, body: CorregirBody,
                         user: AuthUser = Depends(require_superadmin)):
    texto = body.texto.strip()
    if not texto:
        raise HTTPException(status_code=400, detail="texto_vacio")
    memoria = await _memoria_conectada()
    # El embedding se calcula ANTES de abrir la transacción (llamada HTTP a
    # Ollama, spec §4 async: nada bloqueante -- ni de más, ni adentro de un
    # BEGIN sosteniendo un lock). Si falla, la fila queda con el vector cero
    # por defecto y la repara backfill_zero_embeddings() (jax/memory/db.py),
    # igual que cualquier otro fact: no es un caso especial de esta pantalla.
    embedding = await memoria.get_embedding(texto)
    autor = int(user.user_id)

    async with transaccion(AISLAMIENTO_ADMIN) as cur:
        await cur.execute(
            "SELECT fact_type, confidence, user_id, project_id, importance, "
            "superseded_by FROM facts WHERE id = %s FOR UPDATE",
            (fact_id,),
        )
        vieja = await cur.fetchone()
        if vieja is None:
            raise HTTPException(status_code=404, detail="hecho_no_encontrado")
        fact_type, confidence, user_id, project_id, importance, superseded_by = vieja
        if superseded_by is not None:
            raise HTTPException(status_code=409, detail="hecho_ya_superado")

        if embedding:
            await cur.execute(
                "INSERT INTO facts (fact_uuid, fact_text, fact_type, confidence, "
                "is_verified, user_id, project_id, importance, embedding_bge_m3) "
                "VALUES (UUID(), %s, %s, %s, FALSE, %s, %s, %s, VEC_FromText(%s))",
                (texto, fact_type, confidence, user_id, project_id, importance,
                 json.dumps(embedding)),
            )
        else:
            await cur.execute(
                "INSERT INTO facts (fact_uuid, fact_text, fact_type, confidence, "
                "is_verified, user_id, project_id, importance) "
                "VALUES (UUID(), %s, %s, %s, FALSE, %s, %s, %s)",
                (texto, fact_type, confidence, user_id, project_id, importance),
            )
        nuevo_id = cur.lastrowid

        # Misma transacción que el INSERT de arriba: las dos escrituras
        # confirman juntas o ninguna (jax-platform#107).
        await cur.execute(
            "UPDATE facts SET superseded_by = %s, superseded_at = NOW(), "
            "superseded_by_user = %s WHERE id = %s",
            (nuevo_id, autor, fact_id),
        )
    return {"nuevo_id": nuevo_id}


class CaducarBody(BaseModel):
    vence_at: Optional[str] = None


@router.post("/hechos/{fact_id}/caducar")
async def caducar_hecho(fact_id: int, body: CaducarBody,
                        user: AuthUser = Depends(require_superadmin)):
    memoria = await _memoria_conectada()
    expira = None
    if body.vence_at:
        try:
            expira = datetime.fromisoformat(body.vence_at)
        except ValueError:
            raise HTTPException(status_code=400, detail="vence_at_invalido") from None
    ok = await memoria.expire_fact(fact_id, expira)
    if not ok:
        raise HTTPException(status_code=404, detail="hecho_no_encontrado")
    return {"ok": True}
