"""Bloque D (D1.5 tab 3) — facetas y bindings. Ver
jax-platform/docs/fase2-facetas-diseno.md D1.1/C1.2.

Contrato de capabilities: en Bloque C (C1.2) quedaba explicitamente marcado
'unknown' porque el catalogo de modelos no existia todavia. Con `model`
(Bloque D) ya es un chequeo real.
"""
import aiomysql
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel

from auth.middleware import require_superadmin
from auth.models import AuthUser
from db.connection import get_pool

router = APIRouter(prefix="/api/admin/facet-bindings")


def _capability_check(facet_row: dict, model_row: dict | None) -> str:
    """'unknown': sin binding o sin metadata de modelo todavia (nunca se
    simula un chequeo que no puede ser cierto — mismo principio que C1.2).
    'warning': el modelo declarado no cubre lo que la faceta exige.
    'ok': cubre tool_use/contexto exigidos (o la faceta no exige nada)."""
    if model_row is None:
        return "unknown"
    if facet_row["requires_tool_use"] and not model_row["supports_tool_use"]:
        return "warning"
    if model_row["context_window"] is not None and model_row["context_window"] < facet_row["min_context_tokens"]:
        return "warning"
    if model_row["context_window"] is None and facet_row["min_context_tokens"] > 0:
        return "unknown"
    return "ok"


@router.get("")
async def list_facet_bindings(user: AuthUser = Depends(require_superadmin)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT f.`key`, f.display_name, f.transport, f.requires_tool_use, "
                "f.requires_structured_output, f.min_context_tokens, f.status, "
                "b.role, b.provider_id, b.model_ref, m.model_id, m.supports_tool_use, "
                "m.context_window, m.status "
                "FROM facet f "
                "LEFT JOIN facet_binding b ON b.facet_key = f.`key` AND b.role = 'primary' "
                "LEFT JOIN model m ON m.id = b.model_ref "
                "ORDER BY f.`key`"
            )
            rows = await cur.fetchall()

    bindings = []
    for (key, display_name, transport, requires_tool_use, requires_structured_output,
         min_context_tokens, facet_status, role, provider_id, model_ref, model_id,
         supports_tool_use, context_window, model_status) in rows:
        facet_row = {
            "requires_tool_use": bool(requires_tool_use),
            "min_context_tokens": min_context_tokens,
        }
        model_row = None
        if model_ref is not None:
            model_row = {"supports_tool_use": bool(supports_tool_use), "context_window": context_window}
        bindings.append({
            "facet_key": key,
            "display_name": display_name,
            "transport": transport,
            "requires_tool_use": bool(requires_tool_use),
            "requires_structured_output": bool(requires_structured_output),
            "min_context_tokens": min_context_tokens,
            "facet_status": facet_status,
            "role": role,
            "provider_id": provider_id,
            "model_ref": model_ref,
            "model_id": model_id,
            "model_status": model_status,
            "capability_check": _capability_check(facet_row, model_row),
        })
    return {"bindings": bindings}


class UpdateBindingRequest(BaseModel):
    provider_id: str
    model_ref: int
    role: str = "primary"


@router.put("/{facet_key}")
async def update_facet_binding(
    facet_key: str,
    req: UpdateBindingRequest,
    background_tasks: BackgroundTasks,
    user: AuthUser = Depends(require_superadmin),
):
    """Escritura directa autorizada por un superadmin (approved_by/approved_at
    reales) — distinto del flujo de sync, que nunca toca facet_binding (ver
    api/admin/models.py). model_ref debe existir en `model`: la FK es la
    validacion, no una capa aparte que pueda desincronizarse de ella."""
    pool = await get_pool()
    approved_by = int(user.user_id)
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT `key` FROM facet WHERE `key`=%s", (facet_key,))
            if not await cur.fetchone():
                raise HTTPException(status_code=404, detail=f"Faceta '{facet_key}' no existe")

            try:
                await cur.execute(
                    "INSERT INTO facet_binding (facet_key, provider_id, model_id, model_ref, role, approved_by, approved_at) "
                    "VALUES (%s, %s, "
                    "  (SELECT model_id FROM model WHERE id=%s), "
                    "  %s, %s, %s, NOW()) "
                    "ON DUPLICATE KEY UPDATE provider_id=VALUES(provider_id), "
                    "  model_id=VALUES(model_id), model_ref=VALUES(model_ref), "
                    "  approved_by=VALUES(approved_by), approved_at=NOW()",
                    (facet_key, req.provider_id, req.model_ref, req.model_ref, req.role, approved_by),
                )
            except aiomysql.IntegrityError as e:
                await conn.rollback()
                raise HTTPException(
                    status_code=400,
                    detail=f"model_ref {req.model_ref} no existe en el catalogo (o provider_id invalido): {e}",
                )
        await conn.commit()

    # DESPUES del commit a proposito: la sonda solo tiene sentido sobre un
    # binding ya aprobado. Encolada, no await inline -- un await colgaria
    # la request del admin de una llamada a un proveedor externo. Import
    # diferido: mismo motivo que en api/admin/models.py::approve_proposal,
    # el otro escritor de facet_binding -- ver el COMENTARIO de ese import
    # (no el docstring del endpoint) para la cadena real y verificada.
    from jax_engine.background import add_safe_task
    from jax_engine.facet_canary import probe_after_rebind
    add_safe_task(background_tasks, probe_after_rebind, facet_key)

    return {"ok": True, "facet_key": facet_key, "model_ref": req.model_ref, "role": req.role}
