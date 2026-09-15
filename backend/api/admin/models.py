"""Bloque D (D1.5 tab 2) — catalogo de modelos y proposals. Ver
jax-platform/docs/fase2-facetas-diseno.md D1.3.

REGLA DE ORO: /sync solo escribe `model` (via model_catalog, capas a+b).
/proposals/{id}/approve es el UNICO camino de este router hacia
facet_binding — nunca un UPDATE directo disparado por el sync.
"""
import json
import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel

import facet_resolver
import model_catalog
from auth.middleware import require_superadmin
from auth.models import AuthUser
from contrato_dispatch import (
    _MAX_TOKENS_PARAM_NAMES,
    detalle_si_rompe_el_contrato,
    errores_del_contrato,
    ip_de,
    registrar_rechazo_de_binding,
)
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
    "deprecation_date, status, source, source_checked_at, consecutive_misses, "
    # max_tokens_param (2026-08-27, incidente thot): visible para el superadmin
    # porque NULL es el estado que rompe el dispatch de esa fila
    # (api/chat.py::_max_tokens_field falla ruidoso) — un operador tiene que
    # poder VER cual modelo del catalogo le falta el dato, no solo enterarse
    # cuando una faceta se cae.
    # max_output_tokens (2026-08-27, segunda mitad del mismo incidente): igual
    # que max_tokens_param, NULL es el estado que rompe el dispatch de esa fila
    # (api/chat.py::_max_output_tokens_value falla ruidoso). Se muestra al lado
    # del anterior a proposito: son un par (como se llama el parametro / que
    # valor admite) y un operador tiene que poder ver los dos huecos de una.
    "max_tokens_param, max_output_tokens"
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
    # Las opciones del formulario "declarar contrato" salen del mismo
    # conjunto que usa el validador del dispatch (PR-L): el frontend no
    # replica el ENUM.
    return {
        "models": [_row_to_model(r) for r in rows],
        "max_tokens_param_opciones": list(_MAX_TOKENS_PARAM_NAMES),
    }


# PR-L (2026-09-14): sentencias del endpoint de abajo, como constantes para
# que el test de EXPLAIN mida la consulta REAL y no una copia. Las dos van por
# PK (model.id): una fila, sin filesort.
_SQL_CONTRATO_ACTUAL = (
    "SELECT model_id, max_tokens_param, max_output_tokens FROM model WHERE id=%s FOR UPDATE"
)
_SQL_DECLARAR_CONTRATO = (
    "UPDATE model SET max_tokens_param=%s, max_output_tokens=%s WHERE id=%s"
)


class ContratoDispatchRequest(BaseModel):
    # Any a propósito: el tipo y el rango los decide el MISMO validador que
    # el dispatch (contrato_dispatch.py). Con `int` pydantic convertiría
    # "4096" o True antes de que el validador los vea, y rechazaría otros con
    # un 422 sin `code` -- dos reglas para el mismo dato.
    max_tokens_param: Any = None
    max_output_tokens: Any = None


@router.put("/{model_ref}/contrato-dispatch")
async def declarar_contrato_dispatch(
    model_ref: int,
    req: ContratoDispatchRequest,
    request: Request,
    user: AuthUser = Depends(require_superadmin),
):
    """Declara el contrato de dispatch (max_tokens_param + max_output_tokens)
    de UNA fila de `model` (PR-L, Ruling 33, 2026-09-14).

    Por qué existe: el guard de PR-J rechaza (409) aprobar una propuesta o un
    PUT de binding hacia una fila sin contrato, y el único remedio era un
    UPDATE a mano -- contra la regla del ecosistema "cambiar el modelo NUNCA
    es un UPDATE a mano". Esto es ese remedio con autor, validación y rastro.

    Valida con _max_tokens_field/_max_output_tokens_value, los validadores
    del dispatch: lo que acá se acepta es exactamente lo que _call_openai_compat
    acepta. Escribe la fila y la auditoría (antes -> después) en UNA
    transacción, por PK. Después del commit estampa el sello de
    facet_resolver: la resolución cacheada de cualquier faceta que use esta
    fila (Mesa web, LAS MANOS, REPL) queda obsoleta y el próximo dispatch lee
    el valor nuevo sin reiniciar. Sin caché nueva."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(_SQL_CONTRATO_ACTUAL, (model_ref,))
            fila = await cur.fetchone()
            if fila is None:
                await conn.rollback()
                raise HTTPException(status_code=404, detail="modelo_no_encontrado")
            model_id, param_antes, tope_antes = fila

            errores = errores_del_contrato(model_id, req.max_tokens_param, req.max_output_tokens)
            if errores:
                await conn.rollback()
                raise HTTPException(status_code=422, detail={
                    "code": "contrato_dispatch_invalido",
                    "model_ref": model_ref,
                    "model_id": model_id,
                    "campos": [campo for campo, _ in errores],
                    "message": " | ".join(str(e) for _, e in errores),
                })

            antes = {"max_tokens_param": param_antes, "max_output_tokens": tope_antes}
            despues = {"max_tokens_param": req.max_tokens_param, "max_output_tokens": req.max_output_tokens}
            await cur.execute(_SQL_DECLARAR_CONTRATO, (req.max_tokens_param, req.max_output_tokens, model_ref))
            await cur.execute(
                "INSERT INTO model_catalog_audit (action, model_ref, valor_antes, valor_despues, "
                "performed_by, performed_from_ip) VALUES ('contrato_declarado', %s, %s, %s, %s, %s)",
                (model_ref, json.dumps(antes), json.dumps(despues), int(user.user_id), ip_de(request)),
            )
        await conn.commit()

    # Después del commit (mismo criterio que api/admin/motors.py): un sello
    # antes del commit haría que otro proceso recargue el valor VIEJO y crea
    # que está al día. El sello invalida también el _cache de ESTE proceso
    # (resolve_facet descarta toda entrada más vieja que el sello).
    facet_resolver._tocar_sello()
    logger.info(
        f"contrato de dispatch declarado model_ref={model_ref} model={model_id!r} "
        f"antes={antes} despues={despues} by={user.user_id}"
    )
    return {"ok": True, "model_ref": model_ref, "model_id": model_id, "antes": antes, **despues}


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
            ids = [row[0] for row in rows]
            rechazos = await _ultimos_rechazos(cur, ids)

    fields = ["id", "facet_key", "current_model_ref", "proposed_model_ref", "reason",
              "detail", "status", "decided_by", "decided_at", "created_at"]
    proposals = []
    for row in rows:
        d = dict(zip(fields, row))
        d["decided_at"] = str(d["decided_at"]) if d["decided_at"] else None
        d["created_at"] = str(d["created_at"]) if d["created_at"] else None
        d["ultimo_rechazo"] = rechazos.get(d["id"])
        proposals.append(d)
    return {"proposals": proposals}


# PR-L (2026-09-14): el último 409 del guard por propuesta. Una sola query
# para toda la lista (no una por fila), por idx_proposal_id (proposal_id, id).
_SQL_RECHAZOS_DE_PROPUESTAS = (
    "SELECT proposal_id, code, valor_despues, performed_by, performed_at "
    "FROM model_catalog_audit "
    "WHERE action='binding_rechazado' AND proposal_id IN ({marcas}) ORDER BY proposal_id, id"
)


async def _ultimos_rechazos(cur, proposal_ids: list[int]) -> dict:
    if not proposal_ids:
        return {}
    marcas = ", ".join(["%s"] * len(proposal_ids))
    await cur.execute(_SQL_RECHAZOS_DE_PROPUESTAS.format(marcas=marcas), tuple(proposal_ids))
    ultimos = {}
    for proposal_id, code, detalle, performed_by, performed_at in await cur.fetchall():
        detalle = json.loads(detalle) if detalle else {}
        # ORDER BY id: el último pisa a los anteriores.
        ultimos[proposal_id] = {
            "code": code,
            "model_ref": detalle.get("model_ref"),
            "model_id": detalle.get("model_id"),
            "campos": detalle.get("campos", []),
            "provider_modelo": detalle.get("provider_modelo"),
            "provider_binding": detalle.get("provider_binding"),
            "performed_by": performed_by,
            "performed_at": str(performed_at) if performed_at else None,
        }
    return ultimos


async def _fetch_proposal(cur, proposal_id: int):
    await cur.execute(
        "SELECT facet_key, proposed_model_ref, status FROM model_binding_proposal WHERE id=%s",
        (proposal_id,),
    )
    return await cur.fetchone()


@router.post("/proposals/{proposal_id}/approve")
async def approve_proposal(
    proposal_id: int,
    background_tasks: BackgroundTasks,
    request: Request,
    user: AuthUser = Depends(require_superadmin),
):
    """UNICO endpoint de este router que escribe facet_binding — regla de
    oro: siempre una aprobacion explicita de un superadmin, nunca el sync.

    2026-08-19 a 2026-08-24: este endpoint sincronizaba tambien
    `motor.model_ref` con un segundo UPDATE en la misma transaccion --
    ese sync se agrego el 2026-08-19 tras un incidente de divergencia con
    qwen3.6, y volvio a fallar 5 dias despues porque PUT
    /api/admin/facet-bindings/{key} (facet_bindings.py) escribia
    facet_binding sin pasar por aca, y nada sincronizaba motor para ese
    camino. El UPDATE motor se elimino (no se reemplaza por otro sync):
    motor.model_ref ya no es una fuente independiente para una clave con
    faceta homonima, la vista `motor_resolved` resuelve siempre por
    facet_binding.model_ref para esos casos -- ver
    _eliminate_motor_model_ref_denormalization en migrations.py. Ya no
    hace falta que ESTE endpoint (ni ningun otro futuro) se acuerde de
    tocar motor."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            row = await _fetch_proposal(cur, proposal_id)
            if not row:
                raise HTTPException(status_code=404, detail="Proposal no encontrada")
            facet_key, proposed_model_ref, current_status = row
            if current_status != "pending":
                raise HTTPException(status_code=409, detail=f"Proposal ya esta '{current_status}'")

            # 2026-09-14 (PR-J): ANTES de escribir, el modelo propuesto tiene
            # que cumplir el contrato de dispatch del transporte de la faceta,
            # con los MISMOS validadores que el dispatch. Si no, 409 y nada se
            # escribe: la propuesta sigue 'pending' (se aprueba cuando la fila
            # de `model` este sembrada) y el binding no cambia. Aprobar la #11
            # sin este chequeo dejo a jekyll caida hasta su primer uso.
            decided_by = int(user.user_id)
            detalle = await detalle_si_rompe_el_contrato(cur, facet_key, proposed_model_ref)
            if detalle is not None:
                # PR-L (2026-09-14): el rechazo queda en la DB y la lista de
                # propuestas lo muestra (ultimo_rechazo). Commit antes del
                # 409: es lo único que esta transacción escribe -- el guard
                # corre antes de cualquier UPDATE.
                await registrar_rechazo_de_binding(
                    cur, detalle, proposal_id, decided_by, ip_de(request))
                await conn.commit()
                raise HTTPException(status_code=409, detail=detalle)

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

    # DESPUES del commit a proposito: la sonda solo tiene sentido sobre un
    # binding ya aprobado. Encolada, no await inline -- un await colgaria
    # la request del admin de una llamada a un proveedor externo.
    #
    # Import diferido: api/admin/__init__.py importa ansiosamente .models y
    # .facet_bindings, asi que api.chat -> api.admin.usage arrastra ESTE
    # modulo. A nivel de modulo el ciclo cierra de verdad (main.py:49 importa
    # facet_canary antes que api.chat) y el servicio no arranca: ImportError
    # sobre facet_canary parcialmente inicializado. Verificado, no supuesto.
    from jax_engine.background import add_safe_task
    from jax_engine.facet_canary import probe_after_rebind
    add_safe_task(background_tasks, probe_after_rebind, facet_key)

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
