import math
import os
import re
import time
import uuid
from decimal import Decimal, InvalidOperation
from typing import Any

import ajustes
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from auth.middleware import get_current_user
from auth.models import AuthUser
from db.connection import get_pool
from http_client import get_http_client
from jax_engine.resource_manager import resource_manager
from jax_engine.state import engine_state
from jax_engine.schemas import PipelineState
from redaccion import recortar_redactado

router = APIRouter(prefix="/api/pipelines")

JACOBS_URL = os.getenv("JACOBS_URL", "http://127.0.0.1:7777/jacobs")

# _plan_builder.build() en Jacobs llama a un LLM real para armar el plan,
# incluso en dry_run (ver T1.c). Medido 2026-08-19: 17s, 23.4s, 27.6s,
# 29.6s en 4 corridas — la latencia de un cliente HTTP externo (probable
# grounding/web de hipatia) no tiene cota firme. 10s cortaba conexiones
# legítimas: jax-platform devolvía 502 mientras Jacobs seguía corriendo y
# persistía el pipeline sin que el cliente se enterara del pipeline_id
# (huérfano confirmado, sonda T1.a). Margen ~2x sobre el máximo medido.
JACOBS_PIPELINE_TIMEOUT = float(os.getenv("JACOBS_PIPELINE_TIMEOUT", "60.0"))

# `invoked_by` es el ROL de quien le pide a Jacobs, no una persona (tanda A,
# 2026-09-14, decisión de Fernando): "plataforma" = pedido de jax-platform en
# nombre de un usuario autenticado. La identidad viaja en user_id/tenant_id y
# la pone este backend; el rol también -- nunca se toma del cliente.
INVOKED_BY_PLATAFORMA = "plataforma"

# --- Respuestas de Jacobs (spec 2026-09-17 §6.1) ---------------------------
# Hasta hoy get/results/resume/cancel devolvían 200 con el cuerpo de error de
# Jacobs y el aviso de rechazo nunca aparecía. Un solo lugar decide qué hacer
# con cualquier respuesta: dict si salió bien; si no, la HTTPException con el
# MISMO status.
#
# Códigos propios que Jacobs devuelve con datos que la Mesa tiene que mostrar
# (desvío DV-6 del plan; lista ampliada por la ENMIENDA DE CONTRATO del
# controlador, 2026-09-17, ítem 3): pasan con su status y SÓLO los campos
# declarados. Cualquier otro rechazo es jacobs_rechazo con el motivo
# recortado y redactado.
CODIGOS_DE_JACOBS = frozenset({
    "prevuelo_rechazado", "costo_supera_lo_aceptado", "reasignacion_invalida",
    "prevuelo_no_disponible", "estado_no_continuable",
    "limite_de_activos", "plan_rechazado", "plan_inconsistente", "no_existe",
})
# ENMIENDA ítem 2: estado_no_continuable trae {code, status, mensaje} -- NO
# status_actual. "status" reemplaza a "status_actual"; se agregan "mensaje",
# "detalle", "hay_no_acotados", "sondeadas" y "ok" (ítems 2 y 4).
_CAMPOS_DE_CODIGO = (
    "violaciones", "costo_max_usd", "pasos_costo", "costo_max_aceptado_usd",
    "status", "mensaje", "detalle", "hay_no_acotados", "sondeadas", "ok",
)
MOTIVO_MAX = 200
DETALLE_MAX = 300


def _violaciones_redactadas(violaciones) -> list[dict]:
    """Sólo los cuatro campos del contrato; el detalle puede traer el error de
    una sonda y se redacta antes de salir hacia el navegador."""
    if not isinstance(violaciones, list):
        return []
    return [
        {"paso": v.get("paso"), "faceta": v.get("faceta"), "regla": v.get("regla"),
         "detalle": recortar_redactado(str(v.get("detalle") or ""), DETALLE_MAX)}
        for v in violaciones if isinstance(v, dict)
    ]


_CAMPOS_DE_PASO_COSTO = (
    "paso", "faceta", "modelo", "llamadas_max", "tokens_in_max", "tokens_out_max", "usd_max", "motivo",
)


def _pasos_costo_saneados(pasos) -> list[dict]:
    """Sólo los ocho campos del contrato por paso (Task 6 reutiliza este
    helper para /continue/preflight y /continue). motivo puede traer texto de
    un proveedor y se redacta antes de salir hacia el navegador. usd_max sale
    en punto fijo cuando es legible (fix round 2 ítem 5, `_monto_texto`) --
    un solo lugar formatea para el veredicto (ya fail-closed más arriba, acá
    nunca falla) Y para un rechazo de Jacobs (best-effort, sin la validación
    previa del veredicto: un usd_max ilegible en un paso de un rechazo se
    deja en null, nunca rompe el rechazo completo)."""
    if not isinstance(pasos, list):
        return []
    saneados = []
    for p in pasos:
        if not isinstance(p, dict):
            continue
        item = {campo: p.get(campo) for campo in _CAMPOS_DE_PASO_COSTO}
        if isinstance(item["motivo"], str):
            item["motivo"] = recortar_redactado(item["motivo"], MOTIVO_MAX)
        if item["usd_max"] is not None:
            try:
                item["usd_max"] = _monto_texto(_monto(item["usd_max"]))
            except ValueError:  # fail-soft: usd_max ilegible en un rechazo -- se deja null, no rompe el rechazo
                item["usd_max"] = None
        saneados.append(item)
    return saneados


def _sondeadas_saneadas(sondeadas) -> list[str]:
    """sondeadas es la lista de facetas que Jacobs efectivamente consultó;
    sólo strings pasan (defensa contra un elemento mal formado, no texto de
    proveedor que necesite redacción)."""
    if not isinstance(sondeadas, list):
        return []
    return [s for s in sondeadas if isinstance(s, str)]


def _rechazo_de_jacobs(status_code: int, cuerpo, texto: str) -> HTTPException:
    detalle_de_jacobs = cuerpo.get("detail") if isinstance(cuerpo, dict) else None
    if isinstance(detalle_de_jacobs, dict) and detalle_de_jacobs.get("code") in CODIGOS_DE_JACOBS:
        detalle = {"code": detalle_de_jacobs["code"]}
        for campo in _CAMPOS_DE_CODIGO:
            if campo not in detalle_de_jacobs:
                continue
            valor = detalle_de_jacobs[campo]
            if campo == "violaciones":
                valor = _violaciones_redactadas(valor)
            elif campo == "pasos_costo":
                valor = _pasos_costo_saneados(valor)
            elif campo == "sondeadas":
                valor = _sondeadas_saneadas(valor)
            elif campo in ("costo_max_usd", "costo_max_aceptado_usd"):
                # Ruling del controlador (fix round 2 ítem 5): mismo formato
                # de punto fijo que el veredicto; un monto ilegible en un
                # rechazo se OMITE, nunca rompe el rechazo completo.
                try:
                    valor = _monto_texto(_monto(valor))
                except ValueError:  # fail-soft: monto ilegible en un rechazo de Jacobs -- se omite el campo
                    continue
            elif campo == "detalle" and valor is not None:
                valor = recortar_redactado(str(valor), DETALLE_MAX)
            elif campo == "mensaje" and valor is not None:
                valor = recortar_redactado(str(valor), MOTIVO_MAX)
            detalle[campo] = valor
        return HTTPException(status_code=status_code, detail=detalle)
    if isinstance(detalle_de_jacobs, str):
        # Forma vieja/simple: {"detail": "texto"} (ej. el 409 de un doble
        # resume/approve, sin code -- FastAPI se lo pone así solo).
        crudo = detalle_de_jacobs
    elif isinstance(detalle_de_jacobs, dict):
        # Un code propio de Jacobs que NO está en CODIGOS_DE_JACOBS (ej.
        # kill_switch, enmienda ítem 1/6): el mejor texto disponible, nunca
        # el repr de Python del dict completo.
        crudo = str(detalle_de_jacobs.get("detalle") or detalle_de_jacobs.get("mensaje")
                    or detalle_de_jacobs.get("code") or "")
    else:
        # "detail" ausente, o no es ni string ni dict (la lista de un 422 de
        # validación de FastAPI, u otro tipo), o el cuerpo mismo no era un
        # dict (JSON no-objeto): nada estructurado que mostrar -- se usa el
        # texto crudo de la respuesta.
        crudo = texto
    detalle = {"code": "jacobs_rechazo", "status": status_code, "motivo": recortar_redactado(crudo, MOTIVO_MAX)}
    return HTTPException(status_code=status_code, detail=detalle)


def _json_de_jacobs(r) -> dict:
    """La respuesta de Jacobs como dict. status >= 400 -> _rechazo_de_jacobs
    con el mismo status (también si el cuerpo no es JSON). Un 2xx/3xx que no
    es un objeto JSON -> 502 jacobs_no_responde con el texto redactado."""
    try:
        cuerpo = r.json()
    except ValueError:
        cuerpo = None  # no-JSON: se decide abajo por status, nunca se traga
    if r.status_code >= 400:
        raise _rechazo_de_jacobs(r.status_code, cuerpo, r.text)
    if not isinstance(cuerpo, dict):
        detalle = {"code": "jacobs_no_responde", "motivo": recortar_redactado(r.text, MOTIVO_MAX)}
        raise HTTPException(status_code=502, detail=detalle)
    return cuerpo


# --- Pre-vuelo (spec 2026-09-17 §6.1) --------------------------------------
def _monto(valor) -> Decimal:
    """Monto de Jacobs: string decimal o número JSON, finito y >= 0. Más
    permisivo que `_monto_cliente` (notación científica, guiones bajos, etc.
    de un `Decimal(str(...))` normal) porque Jacobs es un servicio interno
    de confianza, no el cliente HTTP."""
    if isinstance(valor, bool) or not isinstance(valor, (str, int, float)):
        raise ValueError(valor)
    try:
        monto = Decimal(str(valor))
    except InvalidOperation as exc:
        raise ValueError(valor) from exc
    if not monto.is_finite() or monto < 0:
        raise ValueError(valor)
    return monto


_MONTO_CLIENTE_CANONICO = re.compile(r"(0|[1-9][0-9]*)(\.[0-9]+)?")


def _monto_cliente(valor) -> Decimal:
    """Monto que manda el cliente (`costo_confirmado_usd`): más estricto que
    `_monto` -- un número JSON finito (no bool), o un string en forma
    CANÓNICA únicamente (sin notación científica, sin espacios, sin guiones
    bajos, sin signo). Fix round 1 ítem 3: "1e-7", "1e3", "-0", " 0.6 " y
    "1_000" como string se rechazan acá aunque `Decimal()` los entendiera."""
    if isinstance(valor, bool):
        raise ValueError(valor)
    if isinstance(valor, str):
        if not valor.isascii() or not _MONTO_CLIENTE_CANONICO.fullmatch(valor):
            raise ValueError(valor)
        monto = Decimal(valor)
    elif isinstance(valor, float):
        if not math.isfinite(valor):
            raise ValueError(valor)
        monto = Decimal(str(valor))
    elif isinstance(valor, int):
        monto = Decimal(valor)
    else:
        raise ValueError(valor)
    if not monto.is_finite() or monto < 0:
        raise ValueError(valor)
    return monto


def _monto_texto(monto: Decimal) -> str:
    """Salida SIEMPRE en punto fijo, nunca notación científica: un monto
    que Jacobs mandó como "1E-7" sale como "0.0000001" (`format(monto, "f")`
    reescribe cualquier exponente a la forma decimal completa). -0 (y -0.0,
    -0.00, ...) se normaliza a la forma positiva: fix round 1 ítem 1. Se usa
    para costo_max_usd, umbral_usd, cada usd_max de paso, y
    costo_max_aceptado_usd que se manda a Jacobs."""
    texto = format(monto, "f")
    if texto.startswith("-") and monto == 0:
        texto = texto[1:]
    return texto


def _prevuelo_no_disponible() -> HTTPException:
    detalle = {"code": "prevuelo_no_disponible"}
    return HTTPException(status_code=502, detail=detalle)


def _exigir_pasos_validos(steps) -> None:
    """Un solo criterio para /preflight y la creación (fix round 1 ítem 5):
    lista no vacía de objetos. Nunca llama a Jacobs si no se cumple."""
    if not isinstance(steps, list) or not steps or any(not isinstance(s, dict) for s in steps):
        raise HTTPException(status_code=422, detail={"code": "pasos_requeridos"})


def _evaluar_veredicto(crudo, umbral: Decimal) -> dict:
    """Fail-closed: un veredicto sin `ok` booleano, sin costo legible, con
    pasos de costo mal formados, con una violación que no es un objeto, o
    con `sondeadas` presente y no una lista de strings, NO es un pre-vuelo
    aprobado (fix round 1 ítem 4 -- antes esas formas se descartaban en
    silencio en vez de fallar cerrado). Un paso con usd_max null es "no
    acotado" (sin precio): siempre pide confirmación.

    ENMIENDA ítem 4 (2026-09-17): si Jacobs manda `hay_no_acotados`, tiene
    que ser bool (si no, fail-closed 502) y se OR-ea con la recomputación
    local desde usd_max -- nunca se confía en un `false` que contradiga un
    paso con usd_max null."""
    try:
        ok = crudo["ok"]
        costo = _monto(crudo["costo_max_usd"])
        pasos = crudo["pasos_costo"]
        violaciones = crudo["violaciones"]
        if not isinstance(ok, bool) or not isinstance(pasos, list) or not isinstance(violaciones, list):
            raise ValueError("forma del veredicto")
        if any(not isinstance(v, dict) for v in violaciones):
            raise ValueError("violacion mal formada")
        if "sondeadas" in crudo:
            sondeadas_crudas = crudo["sondeadas"]
            if not isinstance(sondeadas_crudas, list) or any(not isinstance(s, str) for s in sondeadas_crudas):
                raise ValueError("sondeadas mal formada")
        else:
            sondeadas_crudas = []
        no_acotado = False
        for paso in pasos:
            if paso["usd_max"] is None:
                no_acotado = True
            else:
                _monto(paso["usd_max"])
        if "hay_no_acotados" in crudo:
            declarado = crudo["hay_no_acotados"]
            if not isinstance(declarado, bool):
                raise ValueError("hay_no_acotados no booleano")
            no_acotado = no_acotado or declarado
    except (KeyError, TypeError, ValueError):
        raise _prevuelo_no_disponible() from None
    # Ruling del controlador (Task 6): sólo los campos declarados llegan al
    # navegador -- se reutilizan los saneadores de Task 5, no los crudos de
    # Jacobs. usd_max ya se validó arriba: `_pasos_costo_saneados` lo
    # reformatea a punto fijo (fix round 1 ítem 1, movido a un único lugar
    # compartido en fix round 2 ítem 5), no puede fallar acá porque ya se
    # probó legible en el loop de arriba.
    return {
        "ok": ok,
        "violaciones": _violaciones_redactadas(violaciones),
        "costo_max_usd": _monto_texto(costo),
        "pasos_costo": _pasos_costo_saneados(pasos),
        "sondeadas": _sondeadas_saneadas(sondeadas_crudas),
        "umbral_usd": _monto_texto(umbral),
        "requiere_confirmacion": costo > umbral or no_acotado,
    }


async def _prevuelo(client, steps: list, user: AuthUser, umbral: Decimal) -> dict:
    r = await client.post(
        f"{JACOBS_URL}/preflight",
        json={"invoked_by": INVOKED_BY_PLATAFORMA, "user_id": user.user_id,
              "tenant_id": user.tenant_id, "steps": steps},
        # Puede sondear facetas (en paralelo, con timeout propio en Jacobs).
        timeout=JACOBS_PIPELINE_TIMEOUT,
    )
    return _evaluar_veredicto(_json_de_jacobs(r), umbral)


def _exigir_consentimiento(veredicto: dict, confirmado: Decimal | None) -> None:
    """422 si el pre-vuelo rechazó; 409 si hace falta confirmar y no se
    confirmó al menos el costo máximo. Nada de esto crea ni gasta."""
    if not veredicto["ok"]:
        detalle = {"code": "prevuelo_rechazado", "violaciones": veredicto["violaciones"],
                   "costo_max_usd": veredicto["costo_max_usd"], "pasos_costo": veredicto["pasos_costo"]}
        raise HTTPException(status_code=422, detail=detalle)
    if veredicto["requiere_confirmacion"] and (
            confirmado is None or confirmado < Decimal(veredicto["costo_max_usd"])):
        detalle = {"code": "confirmacion_de_costo", "costo_max_usd": veredicto["costo_max_usd"],
                   "pasos_costo": veredicto["pasos_costo"], "umbral_usd": veredicto["umbral_usd"]}
        raise HTTPException(status_code=409, detail=detalle)


class PedidoDePrevuelo(BaseModel):
    # `Any`, no `list` (fix round 2 ítem 3): con `list`, pydantic seguía
    # respondiendo SU PROPIO 422 (forma distinta a `{"code":
    # "pasos_requeridos"}`) para `{"steps": "x"}`/`{"steps": {}}`, aunque ya
    # dejara pasar `[]`/`[1]` -- el comentario del fix round 1 decía "un
    # solo criterio" pero no lo era del todo. Con `Any`, CUALQUIER JSON
    # llega intacto y `_exigir_pasos_validos` es el ÚNICO lugar que decide,
    # exactamente igual en /preflight y en la creación.
    steps: Any = None


# engine_state.active_pipelines (memoria) se descarta apenas la pipeline
# termina -- justo cuando normalmente se pide /results. owner_ack_at en
# jacobs_pipelines (misma DB fisica jax_memory que ya comparten ambos
# servicios) es la fuente de ownership que sobrevive a eso.
#
# Ronda 5 (2026-08-20, T1): reemplaza el owner file en
# ~/jax/pipelines/{id}_owner.json (deuda con dientes documentada en
# jacobs/reaper.py de jax -- el reaper cruzaba de repo leyendo ese
# archivo). UPDATE/SELECT directos contra jacobs_pipelines, mismo pool
# de DB que ya usa este servicio para capability/motor -- no un
# request HTTP a Jacobs por cada chequeo de ownership (eso hubiera
# agregado un salto de red a cada GET/resume/cancel, y de cualquier
# forma esas rutas ya dependen de que Jacobs este arriba para el
# reenvio real).
async def _record_pipeline_owner(pipeline_id: str, tenant_id: str, user_id: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE jacobs_pipelines SET owner_ack_at=%s "
                "WHERE pipeline_id=%s AND user_id=%s AND tenant_id=%s",
                (time.time(), pipeline_id, user_id, tenant_id),
            )


def es_del_usuario(user_id: str, tenant_id: str, user: AuthUser) -> bool:
    """LA regla de pertenencia de un pipeline: user_id Y tenant_id coinciden
    con los del token. Sin excepcion por rol (un superadmin que no es el
    dueño recibe 404 igual). La usan _require_pipeline_owner (4 endpoints por
    id) y GET /api/state (Task 6 S2, 2026-09-15) -- una sola regla, no dos."""
    return user_id == user.user_id and tenant_id == user.tenant_id


async def _require_pipeline_owner(pipeline_id: str, user: AuthUser):
    # 404 (no 403) para no confirmarle a un no-dueño que el pipeline_id
    # existe. Pipelines creadas antes de esta migración no tienen
    # owner_ack_at poblado y también devuelven 404 -- costo único de la
    # migración, no un bug (mismo criterio que regia con el owner file).
    try:
        uuid.UUID(pipeline_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="pipeline_id_invalido")
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT user_id, tenant_id, owner_ack_at FROM jacobs_pipelines WHERE pipeline_id=%s",
                (pipeline_id,),
            )
            row = await cur.fetchone()
    if row is None or row[2] is None or not es_del_usuario(row[0], row[1], user):
        raise HTTPException(status_code=404, detail="pipeline_no_encontrado")


# T6-5a (2026-09-15): la lista sale de jacobs_pipelines con la MISMA regla de
# dueño que los endpoints por id (user_id Y tenant_id del token, y
# owner_ack_at poblado). Antes hacía proxy de GET {JACOBS_URL}/pipeline, que
# Jacobs no tiene (405 devuelto como 200) y que no filtraba nada: el día que
# existiera, entregaba todos los pipelines a cualquier sesión.
# Índice idx_jacobs_pipelines_duenio (user_id, tenant_id, created_at), creado
# en db/migrations.py; EXPLAIN en tests/test_t6_seguimiento.py.
SQL_PIPELINES_DEL_USUARIO = (
    "SELECT pipeline_id, name, status, created_at, updated_at FROM jacobs_pipelines "
    "WHERE user_id=%s AND tenant_id=%s AND owner_ack_at IS NOT NULL "
    "ORDER BY created_at DESC LIMIT %s"
)
LISTA_PIPELINES_MAX = int(os.getenv("JAX_LISTA_PIPELINES_MAX", "50"))


@router.get("")
async def list_pipelines(user: AuthUser = Depends(get_current_user)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(SQL_PIPELINES_DEL_USUARIO,
                              (user.user_id, user.tenant_id, LISTA_PIPELINES_MAX))
            filas = await cur.fetchall()
    return {"pipelines": [
        {"pipeline_id": pid, "name": name, "status": st, "created_at": c, "updated_at": u}
        for pid, name, st, c, u in filas
    ]}


@router.post("/preflight")
async def preflight_pipeline(pedido: PedidoDePrevuelo, user: AuthUser = Depends(get_current_user)):
    _exigir_pasos_validos(pedido.steps)
    # El ajuste se lee FUERA del try: un ajuste ilegible es 503 ajuste_ilegible
    # (handler de main.py), no un 502 de Jacobs.
    umbral = await ajustes.valor(ajustes.CONFIRMAR_USD)
    client = await get_http_client()
    try:
        return await _prevuelo(client, pedido.steps, user, umbral)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail={"code": "jacobs_no_responde", "motivo": recortar_redactado(str(e), MOTIVO_MAX)})


@router.post("")
async def create_pipeline(request: Request, user: AuthUser = Depends(get_current_user)):
    limite = await ajustes.valor(ajustes.MAX_PIPELINES)
    if not await resource_manager.can_start_pipeline(user.tenant_id, limite):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": "limite_de_pipelines", "max": limite},
        )
    umbral = await ajustes.valor(ajustes.CONFIRMAR_USD)
    body = await request.json()
    steps = body.get("steps") if isinstance(body, dict) else None
    # Sin pasos no hay pre-vuelo ni costo que confirmar (desvío DV-8 del
    # plan); mismo criterio que /preflight (fix round 1 ítem 5).
    _exigir_pasos_validos(steps)
    try:
        crudo = body.pop("costo_confirmado_usd", None)
        confirmado = None if crudo is None else _monto_cliente(crudo)
    except ValueError:
        raise HTTPException(status_code=422, detail={"code": "costo_confirmado_invalido"}) from None
    body["user_id"] = user.user_id
    body["tenant_id"] = user.tenant_id
    body["invoked_by"] = INVOKED_BY_PLATAFORMA
    client = await get_http_client()
    try:
        veredicto = await _prevuelo(client, steps, user, umbral)
        _exigir_consentimiento(veredicto, confirmado)
        # Siempre (desvío DV-7): el confirmado, o el umbral como consentimiento
        # previo del admin. Jacobs responde 409 costo_supera_lo_aceptado sin
        # crear si su pre-vuelo interno da más.
        body["costo_max_aceptado_usd"] = _monto_texto(confirmado if confirmado is not None else umbral)
        r = await client.post(f"{JACOBS_URL}/pipeline", json=body, timeout=JACOBS_PIPELINE_TIMEOUT)
        data = _json_de_jacobs(r)
        # Ruling del controlador (fix round 2 ítem 5): mismo formato de
        # punto fijo que el veredicto y el rechazo -- el pipeline YA se creó,
        # así que un costo_max_usd ilegible se omite (no se rompe la
        # respuesta por eso); pasos_costo pasa por el mismo saneador
        # (campos declarados, usd_max formateado).
        if "costo_max_usd" in data:
            try:
                data["costo_max_usd"] = _monto_texto(_monto(data["costo_max_usd"]))
            except ValueError:  # fail-soft: costo_max_usd ilegible en la respuesta de creación -- se omite el campo
                del data["costo_max_usd"]
        if "pasos_costo" in data:
            data["pasos_costo"] = _pasos_costo_saneados(data["pasos_costo"])
        pipeline_id = data.get("pipeline_id")
        if pipeline_id:
            # Antes de admitir el recurso o publicar el evento de WS
            # (que ya revela pipeline_id al dueño) — así un fallo acá
            # aborta limpio, sin slot de tenant huérfano ni owner file
            # faltante para un id que el cliente ya recibió.
            await _record_pipeline_owner(pipeline_id, user.tenant_id, user.user_id)
            await resource_manager.admit_pipeline(user.tenant_id, pipeline_id)
            initial = PipelineState(
                pipeline_id=pipeline_id,
                tenant_id=user.tenant_id,
                user_id=user.user_id,
                name=body.get("name", "Pipeline"),
                status="running",
            )
            await engine_state.upsert_pipeline(initial, user.tenant_id, user.user_id)
        return data
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail={"code": "jacobs_no_responde", "motivo": recortar_redactado(str(e), MOTIVO_MAX)})


@router.get("/{pipeline_id}/results")
async def get_pipeline_results(pipeline_id: str, user: AuthUser = Depends(get_current_user)):
    await _require_pipeline_owner(pipeline_id, user)
    client = await get_http_client()
    try:
        r = await client.get(f"{JACOBS_URL}/pipeline/{pipeline_id}/results", timeout=10.0)
        return _json_de_jacobs(r)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail={"code": "jacobs_no_responde", "motivo": recortar_redactado(str(e), 200)})


@router.get("/{pipeline_id}")
async def get_pipeline(pipeline_id: str, user: AuthUser = Depends(get_current_user)):
    await _require_pipeline_owner(pipeline_id, user)
    client = await get_http_client()
    try:
        r = await client.get(f"{JACOBS_URL}/pipeline/{pipeline_id}", timeout=5.0)
        return _json_de_jacobs(r)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail={"code": "jacobs_no_responde", "motivo": recortar_redactado(str(e), 200)})


@router.post("/{pipeline_id}/resume")
async def resume_pipeline(
    pipeline_id: str,
    user: AuthUser = Depends(get_current_user),
):
    await _require_pipeline_owner(pipeline_id, user)
    client = await get_http_client()
    try:
        r = await client.post(
            f"{JACOBS_URL}/pipeline/{pipeline_id}/resume",
            json={"invoked_by": INVOKED_BY_PLATAFORMA, "user_id": user.user_id, "tenant_id": user.tenant_id},
            timeout=10.0,
        )
        return _json_de_jacobs(r)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail={"code": "jacobs_no_responde", "motivo": recortar_redactado(str(e), 200)})


@router.post("/{pipeline_id}/cancel")
async def cancel_pipeline(
    pipeline_id: str,
    user: AuthUser = Depends(get_current_user),
):
    await _require_pipeline_owner(pipeline_id, user)
    client = await get_http_client()
    try:
        r = await client.post(f"{JACOBS_URL}/pipeline/{pipeline_id}/cancel", timeout=10.0)
        data = _json_de_jacobs(r)
        engine_state.remove_pipeline(pipeline_id)
        await resource_manager.release_pipeline(user.tenant_id, pipeline_id)
        return data
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail={"code": "jacobs_no_responde", "motivo": recortar_redactado(str(e), 200)})
