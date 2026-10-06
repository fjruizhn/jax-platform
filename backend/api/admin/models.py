"""Bloque D (D1.5 tab 2) — catalogo de modelos y proposals. Ver
jax-platform/docs/fase2-facetas-diseno.md D1.3.

REGLA DE ORO: /sync solo escribe `model` (via model_catalog, capas a+b).
/proposals/{id}/approve es el UNICO camino de este router hacia
facet_binding — nunca un UPDATE directo disparado por el sync.

PR-L (2026-09-14): PUT /{model_ref}/contrato-dispatch declara el contrato de
dispatch de una fila, y los 409 del guard quedan en model_catalog_audit. Sin
backfill a proposito: los rechazos anteriores a ese deploy solo existieron en
el log y en la pantalla de quien hizo click, no hay de donde reconstruirlos.
"""
import json
import logging
from typing import Any

import aiomysql
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel

import catalogo_sync_config
import catalogo_sync_registro
import facet_resolver
import model_catalog
from auth.middleware import require_superadmin
from auth.models import AuthUser
from contrato_dispatch import (
    _MAX_TOKENS_PARAM_NAMES,
    DETALLE_CONFLICTO_CONCURRENTE,
    binding_de,
    detalle_si_rompe_el_contrato,
    errores_del_contrato,
    es_conflicto_de_lock,
    fila_de_rechazo,
    ip_de,
    registrar_binding_aplicado,
    registrar_rechazo_de_binding,
)
from db.connection import get_pool
from db.transaccion import AISLAMIENTO_ADMIN, transaccion
from tiempo import iso_utc

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin/models")

_MODEL_FIELDS = (
    "id", "provider_id", "model_id", "is_alias", "context_window", "supports_tool_use",
    "supports_structured_output", "input_modalities", "price_input_per_1m_usd",
    "price_output_per_1m_usd", "price_cache_per_1m_usd", "release_date",
    "deprecation_date", "status", "source", "source_checked_at", "consecutive_misses",
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
    "max_tokens_param", "max_output_tokens",
)
_MODEL_COLUMNS = ", ".join(_MODEL_FIELDS)


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
    "SELECT provider_id, model_id, max_tokens_param, max_output_tokens FROM model WHERE id=%s FOR UPDATE"
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
    el valor nuevo sin reiniciar. Sin caché nueva.

    NO reaprueba la propuesta rechazada ni reintenta el PUT del binding, a
    propósito (decisión de la ronda 1 de PR-L): declarar el contrato arregla
    el catálogo; cambiar el modelo de una faceta sigue siendo una decisión
    explícita de un superadmin, que vuelve a hacer click en Aprobar."""
    async with transaccion(AISLAMIENTO_ADMIN) as cur:
        await cur.execute(_SQL_CONTRATO_ACTUAL, (model_ref,))
        fila = await cur.fetchone()
        if fila is None:
            raise HTTPException(status_code=404, detail="modelo_no_encontrado")
        provider_id, model_id, param_antes, tope_antes = fila

        errores = errores_del_contrato(model_id, req.max_tokens_param, req.max_output_tokens)
        if errores:
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
            "INSERT INTO model_catalog_audit (action, model_ref, provider_id, model_id, valor_antes, "
            "valor_despues, performed_by, performed_by_email, performed_from_ip) "
            "VALUES ('contrato_declarado', %s, %s, %s, %s, %s, %s, %s, %s)",
            (model_ref, provider_id, model_id, json.dumps(antes), json.dumps(despues),
             int(user.user_id), user.email, ip_de(request)),
        )

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


@router.post("/sync", status_code=202)
async def sync_models(
    background_tasks: BackgroundTasks,
    response: Response,
    user: AuthUser = Depends(require_superadmin),
):
    """D1.3: capa (a) por cada proveedor con catalogo remoto propio, luego
    capa (b) de enriquecimiento. Solo escribe `model` — ver docstring del
    modulo.

    Capa delgada desde 2026-09-27: la logica de orquestacion (que
    proveedores se sincronizan, que cuenta como fallo, las facetas en
    riesgo, el candado contra syncs concurrentes) vive en
    `model_catalog.sync_all()`, compartida con el ejecutor programado
    (catalogo_modelos_ejecutor.py) -- Regla Absoluta: una sola fuente de
    "que significa que el catalogo este sano", nunca dos implementaciones
    que puedan divergir.

    Tercera auditoría adversarial (2026-09-27): el endpoint volvió a NO
    aceptar cuerpo -- se retiró por completo el mecanismo de `forzar` el
    guardián de "lista encogida" (guardián que también se retiró: la
    complejidad de sostenerlo, más forzar/auditar, traía más defectos
    nuevos que los que resolvía). Lo único que sigue siendo un FALLO del
    proveedor sin sumar misses es una lista VACÍA.

    CORRECCIÓN (MINOR-7, cuarta auditoría adversarial, 2026-09-28): esta
    misma línea decía antes "un retiro masivo legítimo fluye por los misses
    normales de D1.4" -- es falso. Un proveedor que queda con la lista
    VACÍA no pasa nunca por D1.4: queda en error permanente, avisado sync
    tras sync, mientras la lista siga vacía (ver
    model_catalog._motivo_si_respuesta_sospechosa). D1.4 sólo degrada
    modelos puntuales que faltan de una lista que YA NO está vacía.

    2026-09-27 (pedido de Fernando: avance real + no bloquear el click):
    ahora RESERVA la fila de ejecución SINCRÓNICAMENTE (para poder responder
    202 con su id, o 409 si ya hay una corriendo -- candado tomado o fila
    'corriendo' viva, ver `catalogo_sync_registro.reservar_ejecucion`) y
    delega el sync real a una `BackgroundTask` (`add_safe_task`: una
    excepción ahí no puede tumbar nada ni quedar en silencio, ver su
    docstring). El sync YA NO bloquea el click del admin -- la pantalla
    sigue el avance con `GET /admin/models/sync/estado`."""
    pool = await get_pool()
    pasos_total = model_catalog.pasos_totales_de_sync()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await catalogo_sync_registro.limpiar_ejecuciones_viejas(cur)
        await conn.commit()
        async with conn.cursor() as cur:
            ejecucion_id, resultado_en_curso = await catalogo_sync_registro.reservar_ejecucion(
                cur, conn, origen="manual", iniciado_por=int(user.user_id), pasos_total=pasos_total)

    if ejecucion_id is None:
        response.status_code = 409
        return {**resultado_en_curso, "ejecucion_id": None}

    from jax_engine.background import add_safe_task
    add_safe_task(background_tasks, catalogo_sync_registro.ejecutar_reservada, ejecucion_id)

    return {"ok": True, "ejecucion_id": ejecucion_id, "pasos_total": pasos_total}


@router.get("/sync/estado")
async def sync_estado(user: AuthUser = Depends(require_superadmin)):
    """Avance en vivo (si hay un sync corriendo AHORA) más la última corrida
    terminada, sea cual sea su estado -- "última actualización" siempre
    visible (pedido de Fernando, 2026-09-27), incluso si nunca hubo ninguna
    corriendo. Antes de leer, interrumpe cualquier fila 'corriendo' huérfana
    (más vieja que el timeout del .service): sin esto, un proceso caído
    dejaría la pantalla mostrando "corriendo" para siempre."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await catalogo_sync_registro.marcar_huerfanas_interrumpidas(cur)
        await conn.commit()

        async with conn.cursor() as cur:
            # LEFT JOIN jax_users: "quién" (si manual) es el email, no un
            # user_id crudo -- el join va sobre `e.` (alias de
            # catalogo_sync_ejecucion), así que el EXPLAIN sigue leyendo el
            # índice de ESA tabla, no el de jax_users (PK, siempre barato).
            await cur.execute(
                "SELECT e.id, e.origen, e.iniciado_por, e.estado, e.paso_actual, e.pasos_total, "
                "e.detalle_paso, e.iniciado_en, e.terminado_en, e.resultado, u.email "
                "FROM catalogo_sync_ejecucion e LEFT JOIN jax_users u ON u.user_id = e.iniciado_por "
                "WHERE e.estado='corriendo' LIMIT 1"
            )
            fila_corriendo = await cur.fetchone()

        async with conn.cursor() as cur:
            # ORDER BY terminado_en DESC sin WHERE de estado a proposito --
            # ver el comentario de los índices en db/migrations.py
            # (CREATE_CATALOGO_SYNC_EJECUCION): NULL ordena último en DESC,
            # así que esto ya excluye la fila 'corriendo' sin perder el
            # índice.
            await cur.execute(
                "SELECT e.id, e.origen, e.iniciado_por, e.estado, e.paso_actual, e.pasos_total, "
                "e.detalle_paso, e.iniciado_en, e.terminado_en, e.resultado, u.email "
                "FROM catalogo_sync_ejecucion e LEFT JOIN jax_users u ON u.user_id = e.iniciado_por "
                "ORDER BY e.terminado_en DESC LIMIT 1"
            )
            fila_ultima = await cur.fetchone()

    return {
        "corriendo": _fila_con_email(fila_corriendo),
        "ultima": _fila_con_email(fila_ultima),
    }


def _fila_con_email(fila) -> dict | None:
    if fila is None:
        return None
    *columnas_base, email = fila
    d = catalogo_sync_registro.fila_a_dict(columnas_base)
    d["iniciado_por_email"] = email
    return d


class ConfigSyncRequest(BaseModel):
    # Any a propósito, mismo criterio que ContratoDispatchRequest más abajo:
    # el tipo y el rango los valida catalogo_sync_config.validar_config(), no
    # pydantic -- así el 422 siempre trae el mismo `code`/`campo`, venga el
    # dato mal tipado o fuera de rango.
    habilitado: bool
    cada_valor: Any
    cada_unidad: str


@router.get("/sync/config")
async def obtener_config_sync(user: AuthUser = Depends(require_superadmin)):
    """MINOR-2 (tercera ronda de la auditoría adversarial, 2026-09-27):
    `leer_config_cruda()`, no `leer_config()` -- una fila corrupta (escrita a
    mano, o por un bug futuro) tiene que poder VERSE en la pantalla para que
    un operador la repare desde ahí (el PUT sabe reparar, ver
    `catalogo_sync_config.actualizar_config`), nunca un 500. El ejecutor
    programado SIGUE usando `leer_config()` (que sí revienta) -- ese
    comportamiento no cambia."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            config = await catalogo_sync_config.leer_config_cruda(cur)
            ultima_exitosa = await catalogo_sync_registro.ultima_actualizacion_exitosa(cur)
    return {
        **_config_serializable(config),
        "proxima_corrida_estimada": _proxima_corrida_estimada(config, ultima_exitosa),
    }


def _config_serializable(config: dict) -> dict:
    # MINOR-6 de la ronda anterior (auditoría adversarial, 2026-09-27):
    # `iso_utc()`, no `str()` -- `actualizado_en` se escribe con
    # `UTC_TIMESTAMP()` (ver catalogo_sync_config.actualizar_config);
    # `iso_utc()` le pone la zona UTC explícita para que `new Date(...)` del
    # navegador no lo lea como hora local (la sesión de MariaDB de esta app
    # corre en CST, ver tiempo.py). `valida` (si viene, MINOR-2 tercera
    # ronda) pasa tal cual -- lo consume el frontend para avisar de una
    # config corrupta que hay que reparar.
    d = dict(config)
    d["actualizado_en"] = iso_utc(d["actualizado_en"])
    return d


def _proxima_corrida_estimada(config: dict, ultima_exitosa) -> str | None:
    """`None` si está apagado, si todavía no hubo ninguna corrida exitosa
    (tocaría en la próxima pasada del timer, no en una fecha calculable), o
    si la config es inválida (MINOR-2: `cada_unidad`/`cada_valor` corruptos
    no se pueden usar para estimar nada -- `proxima_corrida()` asume una
    unidad conocida). MINOR-6 de la ronda anterior: `iso_utc()`, mismo
    motivo que `_config_serializable` -- `ultima_exitosa` es UTC (viene de
    `catalogo_sync_ejecucion.iniciado_en`) y `proxima_corrida()` sólo le suma
    un intervalo, así que el resultado sigue siendo UTC."""
    if not config["habilitado"] or ultima_exitosa is None or not config.get("valida", True):
        return None
    return iso_utc(catalogo_sync_config.proxima_corrida(ultima_exitosa, config["cada_valor"], config["cada_unidad"]))


@router.put("/sync/config")
async def actualizar_config_sync(
    req: ConfigSyncRequest,
    request: Request,
    user: AuthUser = Depends(require_superadmin),
):
    """MAJOR-2 (auditoría adversarial, 2026-09-27): el UPDATE y su auditoría
    (`axioma_config_audit`, origen `catalogo_sync`) van en la MISMA
    transacción EXPLÍCITA (`db.transaccion.transaccion`, mismo patrón que
    `api/admin/config_admin.py::update_config`) -- el pool es
    `autocommit=True` (ver `db/connection.py`), así que sin `BEGIN`
    explícito un `conn.rollback()` no revierte nada: cada sentencia ya se
    había confirmado sola. Con `transaccion()`, si el INSERT de auditoría
    (dentro de `catalogo_sync_config.actualizar_config`) revienta, el
    UPDATE se revierte con él -- fail-closed, sin try/except que lo tape."""
    async with transaccion(AISLAMIENTO_ADMIN) as cur:
        try:
            config = await catalogo_sync_config.actualizar_config(
                cur,
                habilitado=req.habilitado,
                cada_valor=req.cada_valor,
                cada_unidad=req.cada_unidad,
                actualizado_por=int(user.user_id),
                ip=ip_de(request),
            )
        except catalogo_sync_config.ConfigInvalidaError as e:
            raise HTTPException(status_code=422, detail={
                "code": "catalogo_sync_config_invalida",
                "campo": e.campo,
                "message": str(e),
            }) from None
        ultima_exitosa = await catalogo_sync_registro.ultima_actualizacion_exitosa(cur)

    return {
        **_config_serializable(config),
        "proxima_corrida_estimada": _proxima_corrida_estimada(config, ultima_exitosa),
    }


# PR-L ronda 2 (2026-09-14, punto 7 de la revisión): la lista era sin límite y,
# sin `status`, traía la historia entera (y el IN de _ultimos_rechazos crecía
# con ella; jax_memory_test ya tiene más de 1600 propuestas). Ahora `limit` con
# tope; ORDER BY created_at va por idx_created / idx_status_created.
LIMITE_PROPUESTAS_POR_DEFECTO = 200
LIMITE_PROPUESTAS_MAX = 500


def _sql_propuestas(con_status: bool) -> str:
    sql = (
        "SELECT id, facet_key, current_model_ref, proposed_model_ref, reason, "
        "detail, status, decided_by, decided_at, created_at FROM model_binding_proposal"
    )
    if con_status:
        sql += " WHERE status = %s"
    return sql + " ORDER BY created_at DESC LIMIT %s"


@router.get("/proposals")
async def list_proposals(
    status: str | None = None,
    limit: int = Query(LIMITE_PROPUESTAS_POR_DEFECTO, ge=1, le=LIMITE_PROPUESTAS_MAX),
    user: AuthUser = Depends(require_superadmin),
):
    pool = await get_pool()
    sql = _sql_propuestas(bool(status))
    params = ([status] if status else []) + [limit]

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
    "SELECT proposal_id, code, valor_despues, performed_by, performed_at, provider_id, model_id "
    "FROM model_catalog_audit "
    "WHERE action='binding_rechazado' AND proposal_id IN ({marcas}) ORDER BY proposal_id, id"
)


async def _ultimos_rechazos(cur, proposal_ids: list[int]) -> dict:
    if not proposal_ids:
        return {}
    marcas = ", ".join(["%s"] * len(proposal_ids))
    await cur.execute(_SQL_RECHAZOS_DE_PROPUESTAS.format(marcas=marcas), tuple(proposal_ids))
    ultimos = {}
    for proposal_id, code, detalle, performed_by, performed_at, provider_id, model_id in await cur.fetchall():
        # ORDER BY id: el último pisa a los anteriores.
        rechazo = fila_de_rechazo(code, detalle, performed_by, performed_at, provider_id, model_id)
        rechazo["proposal_id"] = proposal_id
        ultimos[proposal_id] = rechazo
    return ultimos


# Relectura de la propuesta DENTRO de la transaccion de approve, con FOR UPDATE
# por PRIMARY: el 'pending' que se leyo antes de abrirla pudo cambiar (dos
# approves simultaneos pasaban los dos y escribian dos binding_aplicado).
_SQL_PROPUESTA_PARA_ACTUALIZAR = (
    "SELECT status FROM model_binding_proposal WHERE id=%s FOR UPDATE"
)


async def _fetch_proposal(cur, proposal_id: int):
    await cur.execute(
        "SELECT facet_key, proposed_model_ref, status FROM model_binding_proposal WHERE id=%s",
        (proposal_id,),
    )
    return await cur.fetchone()


async def _actualizar_binding_aprobado(cur, *, facet_key: str, model_ref: int, approved_by: int) -> None:
    """Keep the retained identity columns aligned with the approved model.

    ``facet_binding.model_ref`` is the canonical pointer, but the legacy
    ``provider_id`` and ``model_id`` columns remain NOT NULL and are still
    read by compatibility paths. Derive all three values from one locked
    catalog row so an approval cannot leave a mixed identity behind.
    """
    await cur.execute(
        "UPDATE facet_binding AS fb JOIN model AS m ON m.id=%s "
        "SET fb.model_ref=m.id, fb.provider_id=m.provider_id, fb.model_id=m.model_id, "
        "fb.approved_by=%s, fb.approved_at=NOW() "
        "WHERE fb.facet_key=%s AND fb.role='primary'",
        (model_ref, approved_by, facet_key),
    )


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
                    cur, detalle, proposal_id, decided_by, user.email, ip_de(request))
                await conn.commit()
                raise HTTPException(status_code=409, detail=detalle)

            # 2026-10-04: el cambio, el estado de la propuesta y la auditoria
            # van en UNA transaccion. El pool es autocommit=True: sin BEGIN
            # explicito cada UPDATE se confirmaria solo y un fallo de la
            # auditoria dejaria el binding cambiado sin rastro. Va DESPUES del
            # guard: el camino del 409 (arriba) no cambia.
            # READ COMMITTED solo para esta transaccion: sin gap locks (ver el
            # mismo comentario en facet_bindings.py::update_facet_binding).
            await cur.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
            await conn.begin()
            try:
                # Carrera entre approves: se relee la propuesta bloqueandola. El
                # que llega segundo espera al primero y ve 'approved' -> el mismo
                # 409 que ya daba una propuesta no pendiente.
                await cur.execute(_SQL_PROPUESTA_PARA_ACTUALIZAR, (proposal_id,))
                relectura = await cur.fetchone()
                if relectura is None:
                    raise HTTPException(status_code=404, detail="Proposal no encontrada")
                if relectura[0] != "pending":
                    raise HTTPException(status_code=409, detail=f"Proposal ya esta '{relectura[0]}'")
                # El binding que se va a pisar, leido con FOR UPDATE (por
                # uk_facet_role) antes de escribir: es el "antes" de la auditoria.
                antes = await binding_de(cur, facet_key, "primary", para_actualizar=True)
                if antes is None:
                    # Sin binding 'primary' no hay nada que reemplazar: el UPDATE
                    # no aplicaria nada y la propuesta quedaria 'approved' (200)
                    # sin cambio ni auditoria. 409 y no 422: el pedido es valido,
                    # es el ESTADO de la faceta el que no lo admite; la propuesta
                    # sigue 'pending'.
                    raise HTTPException(
                        status_code=409,
                        detail={"code": "faceta_sin_binding_primary", "facet_key": facet_key},
                    )
                # El guard otra vez, DENTRO de la transaccion y con la fila de
                # `model` bloqueada (y el binding ya bloqueado: el proveedor que
                # compara es el vigente). Si ahora rompe, el mismo rechazo
                # auditado y el mismo 409 que el chequeo de arriba.
                detalle = await detalle_si_rompe_el_contrato(
                    cur, facet_key, proposed_model_ref, bloquear_modelo=True)
                if detalle is not None:
                    await registrar_rechazo_de_binding(
                        cur, detalle, proposal_id, decided_by, user.email, ip_de(request))
                    await conn.commit()
                    raise HTTPException(status_code=409, detail=detalle)
                await _actualizar_binding_aprobado(
                    cur, facet_key=facet_key, model_ref=proposed_model_ref, approved_by=decided_by)
                despues = await binding_de(cur, facet_key, "primary")
                await registrar_binding_aplicado(
                    cur, facet_key, "primary", antes, despues, proposal_id,
                    decided_by, user.email, ip_de(request))
                await cur.execute(
                    "UPDATE model_binding_proposal SET status='approved', decided_by=%s, decided_at=NOW() "
                    "WHERE id=%s",
                    (decided_by, proposal_id),
                )
                await conn.commit()
            except aiomysql.OperationalError as e:
                await conn.rollback()
                if es_conflicto_de_lock(e):
                    raise HTTPException(status_code=409, detail=DETALLE_CONFLICTO_CONCURRENTE) from e
                raise
            except BaseException:
                await conn.rollback()
                raise

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
