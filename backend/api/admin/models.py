"""Bloque D (D1.5 tab 2) — catalogo de modelos y proposals. Ver
jax-platform/docs/fase2-facetas-diseno.md D1.3.

REGLA DE ORO: /sync solo escribe `model` (via model_catalog, capas a+b).
/proposals/{id}/approve es el UNICO camino de este router hacia
facet_binding — nunca un UPDATE directo disparado por el sync.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException

import model_catalog
from auth.middleware import require_superadmin
from auth.models import AuthUser
from db.connection import get_pool

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin/models")

# Proveedores con catalogo real hoy. anthropic (2026-08-10): sync contra
# /v1/models, credencial via OAuth local de Claude Code, no `credential` DB.
# ollama (2026-08-10): sync local contra /api/tags, sin ninguna credencial
# (provider.auth_type='none') — ver ramas explicitas en model_catalog.py.
_SYNCABLE_PROVIDERS = ["openai", "deepseek", "gemini", "moonshot", "zhipu", "anthropic", "ollama"]

_MODEL_COLUMNS = (
    "id, provider_id, model_id, is_alias, context_window, supports_tool_use, "
    "supports_structured_output, input_modalities, price_input_per_1m_usd, "
    "price_output_per_1m_usd, price_cache_per_1m_usd, release_date, "
    "deprecation_date, status, source, source_checked_at, consecutive_misses"
)
_MODEL_FIELDS = [c.strip() for c in _MODEL_COLUMNS.split(",")]


def _row_to_model(row) -> dict:
    d = dict(zip(_MODEL_FIELDS, row))
    for date_field in ("release_date", "deprecation_date", "source_checked_at"):
        if d[date_field] is not None:
            d[date_field] = str(d[date_field])
    if d["input_modalities"] is not None:
        d["input_modalities"] = str(d["input_modalities"]).split(",") if d["input_modalities"] else []
    for price_field in ("price_input_per_1m_usd", "price_output_per_1m_usd", "price_cache_per_1m_usd"):
        if d[price_field] is not None:
            d[price_field] = float(d[price_field])
    return d


@router.get("")
async def list_models(
    provider: str | None = None,
    status: str | None = None,
    user: AuthUser = Depends(require_superadmin),
):
    pool = await get_pool()
    where = []
    params = []
    if provider:
        where.append("provider_id = %s")
        params.append(provider)
    if status:
        where.append("status = %s")
        params.append(status)
    sql = f"SELECT {_MODEL_COLUMNS} FROM model"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY provider_id, model_id"

    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(sql, tuple(params))
            rows = await cur.fetchall()
    return {"models": [_row_to_model(r) for r in rows]}


@router.post("/sync")
async def sync_models(user: AuthUser = Depends(require_superadmin)):
    """D1.3: capa (a) por cada proveedor con catalogo remoto propio, luego
    capa (b) de enriquecimiento. Solo escribe `model` — ver docstring del
    modulo."""
    results = []
    for provider_id in _SYNCABLE_PROVIDERS:
        try:
            results.append(await model_catalog.sync_provider_models(provider_id))
        except Exception as e:
            logger.warning(f"sync_models provider={provider_id} failed reason={type(e).__name__}: {e}")
            results.append({"provider_id": provider_id, "error": str(e)[:200]})

    try:
        enrich_result = await model_catalog.enrich_from_models_dev()
    except Exception as e:
        logger.warning(f"sync_models enrich failed reason={type(e).__name__}: {e}")
        enrich_result = {"error": str(e)[:200]}

    return {"ok": True, "providers": results, "enrich": enrich_result}


@router.get("/proposals")
async def list_proposals(
    status: str | None = None,
    user: AuthUser = Depends(require_superadmin),
):
    pool = await get_pool()
    sql = (
        "SELECT id, facet_key, current_model_ref, proposed_model_ref, reason, "
        "detail, status, decided_by, decided_at, created_at FROM model_binding_proposal"
    )
    params = []
    if status:
        sql += " WHERE status = %s"
        params.append(status)
    sql += " ORDER BY created_at DESC"

    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(sql, tuple(params))
            rows = await cur.fetchall()

    fields = ["id", "facet_key", "current_model_ref", "proposed_model_ref", "reason",
              "detail", "status", "decided_by", "decided_at", "created_at"]
    proposals = []
    for row in rows:
        d = dict(zip(fields, row))
        d["decided_at"] = str(d["decided_at"]) if d["decided_at"] else None
        d["created_at"] = str(d["created_at"]) if d["created_at"] else None
        proposals.append(d)
    return {"proposals": proposals}


async def _fetch_proposal(cur, proposal_id: int):
    await cur.execute(
        "SELECT facet_key, proposed_model_ref, status FROM model_binding_proposal WHERE id=%s",
        (proposal_id,),
    )
    return await cur.fetchone()


@router.post("/proposals/{proposal_id}/approve")
async def approve_proposal(proposal_id: int, user: AuthUser = Depends(require_superadmin)):
    """UNICO endpoint de este router que escribe facet_binding — regla de
    oro: siempre una aprobacion explicita de un superadmin, nunca el sync."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            row = await _fetch_proposal(cur, proposal_id)
            if not row:
                raise HTTPException(status_code=404, detail="Proposal no encontrada")
            facet_key, proposed_model_ref, current_status = row
            if current_status != "pending":
                raise HTTPException(status_code=409, detail=f"Proposal ya esta '{current_status}'")

            decided_by = int(user.user_id)
            await cur.execute(
                "UPDATE facet_binding SET model_ref=%s, approved_by=%s, approved_at=NOW() "
                "WHERE facet_key=%s AND role='primary'",
                (proposed_model_ref, decided_by, facet_key),
            )
            await cur.execute(
                "UPDATE model_binding_proposal SET status='approved', decided_by=%s, decided_at=NOW() "
                "WHERE id=%s",
                (decided_by, proposal_id),
            )
        await conn.commit()
    return {"ok": True, "proposal_id": proposal_id, "status": "approved"}


@router.post("/proposals/{proposal_id}/reject")
async def reject_proposal(proposal_id: int, user: AuthUser = Depends(require_superadmin)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            row = await _fetch_proposal(cur, proposal_id)
            if not row:
                raise HTTPException(status_code=404, detail="Proposal no encontrada")
            _facet_key, _proposed_model_ref, current_status = row
            if current_status != "pending":
                raise HTTPException(status_code=409, detail=f"Proposal ya esta '{current_status}'")

            await cur.execute(
                "UPDATE model_binding_proposal SET status='rejected', decided_by=%s, decided_at=NOW() "
                "WHERE id=%s",
                (user.user_id, proposal_id),
            )
        await conn.commit()
    return {"ok": True, "proposal_id": proposal_id, "status": "rejected"}
