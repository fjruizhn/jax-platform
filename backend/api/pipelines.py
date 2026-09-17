import json
import math
import os
import re
import time
import uuid
from collections import defaultdict
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
    # Ruling B (Task 7): el 503 prevuelo_no_disponible de Jacobs trae motivo.
    "motivo",
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
    """Sólo los ocho campos del contrato por paso. Usado hoy por el
    veredicto (`_evaluar_veredicto`), un rechazo de Jacobs
    (`_rechazo_de_jacobs`, también el `motivo` de /continue/preflight vía
    `_detalle_declarado`) y el 200 de crear y de continuar (`_costos_saneados`).
    motivo puede traer texto de un proveedor y se redacta antes de salir
    hacia el navegador. usd_max sale en punto fijo cuando es legible (fix
    round 2 ítem 5, `_monto_texto`): un solo lugar formatea para los tres
    caminos de arriba.

    En el veredicto, `_evaluar_veredicto` ya probó cada usd_max legible más
    arriba (fail-closed: si no lo era, ni siquiera se llega acá), así que
    acá nunca falla. En un rechazo de Jacobs o en el 200 de creación NO hay
    esa validación previa -- ninguno de los dos es el camino del veredicto
    ni el de `_exigir_consentimiento` (que sólo mira lo que ya devolvió
    `_evaluar_veredicto`), así que un usd_max ilegible en esos dos caminos
    se deja en `null` (lectura conservadora: "no acotado"/sin precio, fix
    round 3), nunca revienta el rechazo o la respuesta de creación."""
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
            except ValueError:  # fail-soft: usd_max ilegible en un rechazo o en el 200 de creación -- null, conservador, no rompe la respuesta
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


def _texto_o_nada(valor, limite: int) -> str | None:
    return recortar_redactado(valor, limite) if isinstance(valor, str) else None


def _items_de_detalle(items: list) -> list[dict]:
    """`detalle` como lista (plan J, jacobs/continuar.py `analizar`): una
    reasignación inválida manda {paso, motivo}; un fallo de clean-room o de
    capability manda PlanViolation.to_dict() {step_index, facet, motor,
    capability, reason}. Las dos formas salen como {paso, faceta, motivo}:
    sólo esos campos, textos redactados, elementos que no son objeto fuera.
    paso puede ser str (el índice que mandó el cliente no era un entero)."""
    normalizados = []
    for item in items:
        if not isinstance(item, dict):
            continue
        paso = item.get("paso", item.get("step_index"))
        if isinstance(paso, bool) or not isinstance(paso, (int, str)):
            paso = None
        elif isinstance(paso, str):
            paso = recortar_redactado(paso, MOTIVO_MAX)
        motivo = item.get("motivo", item.get("reason"))
        normalizados.append({
            "paso": paso,
            "faceta": _texto_o_nada(item.get("faceta", item.get("facet")), MOTIVO_MAX),
            "motivo": recortar_redactado(motivo, MOTIVO_MAX) if isinstance(motivo, str) else "",
        })
    return normalizados


def _detalle_declarado(detalle_de_jacobs: dict) -> dict:
    """Un solo saneador para el `{code, **campos}` de Jacobs: lo usan los
    rechazos (`_rechazo_de_jacobs`) y el `motivo` de continue/preflight
    (`_continuable`). Sale `code` y SÓLO los campos declarados, cada uno
    saneado; lo que no se puede sanear se omite, nunca rompe la respuesta."""
    detalle = {"code": detalle_de_jacobs.get("code")}
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
        elif campo == "detalle":
            # Texto o lista (Ruling B del controlador, Task 7); otro tipo
            # (dict, número, null) no tiene forma declarada y se omite.
            if isinstance(valor, str):
                valor = recortar_redactado(valor, DETALLE_MAX)
            elif isinstance(valor, list):
                valor = _items_de_detalle(valor)
            else:
                continue
        elif campo in ("status", "mensaje", "motivo"):
            # Fix round 1 ítem 3: texto o null; otro tipo se omite (nunca
            # str() de un objeto).
            if valor is None:
                pass
            elif isinstance(valor, str):
                valor = recortar_redactado(valor, MOTIVO_MAX)
            else:
                continue
        elif campo in ("ok", "hay_no_acotados") and not isinstance(valor, bool):
            continue
        detalle[campo] = valor
    return detalle


def _rechazo_de_jacobs(status_code: int, cuerpo, texto: str) -> HTTPException:
    detalle_de_jacobs = cuerpo.get("detail") if isinstance(cuerpo, dict) else None
    if isinstance(detalle_de_jacobs, dict) and detalle_de_jacobs.get("code") in CODIGOS_DE_JACOBS:
        detalle = _detalle_declarado(detalle_de_jacobs)
        return HTTPException(status_code=status_code, detail=detalle)
    if isinstance(detalle_de_jacobs, str):
        # Forma vieja/simple: {"detail": "texto"} (ej. el 409 de un doble
        # resume/approve, sin code -- FastAPI se lo pone así solo).
        crudo = detalle_de_jacobs
    elif isinstance(detalle_de_jacobs, dict):
        # Un code propio de Jacobs que NO está en CODIGOS_DE_JACOBS (ej.
        # kill_switch, enmienda ítem 1/6): el mejor texto disponible, nunca
        # el repr de Python del dict completo (ni de un `detalle` lista, Task 7).
        textos = [detalle_de_jacobs.get(c) for c in ("detalle", "mensaje", "code")]
        crudo = next((t for t in textos if isinstance(t, str) and t), "")
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


def _costos_saneados(data: dict) -> dict:
    """El 200 de crear y de continuar (Ruling E, Task 7). Ruling del
    controlador (fix round 2 ítem 5): mismo formato de punto fijo que el
    veredicto y el rechazo -- el pipeline YA se creó o ya se continuó, así
    que un costo_max_usd ilegible se omite (no se rompe la respuesta por
    eso); pasos_costo pasa por el mismo saneador (campos declarados, usd_max
    formateado)."""
    if "costo_max_usd" in data:
        try:
            data["costo_max_usd"] = _monto_texto(_monto(data["costo_max_usd"]))
        except ValueError:  # fail-soft: costo_max_usd ilegible en un 200 de Jacobs -- el pipeline ya corre, se omite el campo
            del data["costo_max_usd"]
    if "pasos_costo" in data:
        data["pasos_costo"] = _pasos_costo_saneados(data["pasos_costo"])
    return data


def _confirmado_del_cliente(crudo) -> Decimal | None:
    try:
        return None if crudo is None else _monto_cliente(crudo)
    except ValueError:
        raise HTTPException(status_code=422, detail={"code": "costo_confirmado_invalido"}) from None


class PedidoDePrevuelo(BaseModel):
    # `Any`, no `list` (fix round 2 ítem 3): con `list`, pydantic seguía
    # respondiendo SU PROPIO 422 (forma distinta a `{"code":
    # "pasos_requeridos"}`) para `{"steps": "x"}`/`{"steps": {}}`, aunque ya
    # dejara pasar `[]`/`[1]` -- el comentario del fix round 1 decía "un
    # solo criterio" pero no lo era del todo. Con `Any`, CUALQUIER JSON
    # llega intacto y `_exigir_pasos_validos` es el ÚNICO lugar que decide,
    # exactamente igual en /preflight y en la creación.
    steps: Any = None


# --- Continuar (spec 2026-09-17 §5.1, §6.1, §6.2) --------------------------
class PedidoDeContinuarPrevuelo(BaseModel):
    reasignar: dict[str, str] | None = None


class PedidoDeContinuar(PedidoDeContinuarPrevuelo):
    # `Any` y `_monto_cliente` (Ruling E, Task 7), no `Decimal`: pydantic
    # acepta "1e3" o " 0.6 " como Decimal; el criterio estricto es el mismo
    # que en la creación.
    costo_confirmado_usd: Any = None


def _cuerpo_de_continuar(user: AuthUser, reasignar: dict[str, str] | None) -> dict:
    cuerpo = {"invoked_by": INVOKED_BY_PLATAFORMA, "user_id": user.user_id, "tenant_id": user.tenant_id}
    if reasignar:
        cuerpo["reasignar"] = reasignar
    return cuerpo


def _lista_de_pasos(valor) -> bool:
    return isinstance(valor, list) and all(isinstance(p, int) and not isinstance(p, bool) for p in valor)


async def _continuable(client, pipeline_id: str, user: AuthUser,
                       reasignar: dict[str, str] | None, umbral: Decimal) -> dict:
    """Fail-closed (plan J, jacobs/continuar.py `previsualizar`): continuable
    es bool; los pasos, listas de enteros; continuable=True exige motivo
    null y veredicto; continuable=False exige motivo {code: str, ...}. El
    veredicto se evalúa siempre que venga (Jacobs lo manda también con
    prevuelo_rechazado y limite_de_activos). Cualquier otra forma no es un
    pre-vuelo: 502 prevuelo_no_disponible."""
    r = await client.post(f"{JACOBS_URL}/pipeline/{pipeline_id}/continue/preflight",
                          json=_cuerpo_de_continuar(user, reasignar), timeout=JACOBS_PIPELINE_TIMEOUT)
    data = _json_de_jacobs(r)
    try:
        continuable = data["continuable"]
        a_correr = data["pasos_a_correr"]
        reusados = data["pasos_reusados"]
        motivo = data["motivo"]
        crudo = data["veredicto"]
        if not isinstance(continuable, bool) or not _lista_de_pasos(a_correr) or not _lista_de_pasos(reusados):
            raise ValueError("forma de continue/preflight")
        if continuable and (motivo is not None or crudo is None):
            raise ValueError("continuable sin veredicto o con motivo")
        if not continuable and (not isinstance(motivo, dict) or not isinstance(motivo.get("code"), str)):
            raise ValueError("no continuable sin motivo")
    except (KeyError, ValueError):
        raise _prevuelo_no_disponible() from None
    return {
        "continuable": continuable,
        "motivo": _motivo_permitido(_detalle_declarado(motivo)) if motivo is not None else None,
        "pasos_a_correr": a_correr,
        "pasos_reusados": reusados,
        "veredicto": _evaluar_veredicto(crudo, umbral) if crudo is not None else None,
    }


# Fix round 1 ítem 2: los code que la Mesa sabe mostrar en el motivo de
# continuar. kill_switch no es un rechazo con campos (va por jacobs_rechazo en
# /continue) pero el modal lo muestra con su propio texto.
CODIGOS_DE_MOTIVO = CODIGOS_DE_JACOBS | {"kill_switch"}


def _motivo_permitido(motivo: dict) -> dict:
    """Un code fuera de CODIGOS_DE_MOTIVO sale como estado_no_continuable con
    el mejor texto redactado -- igual en /continue/preflight y en /continue
    (que lo recibe ya normalizado de `_continuable`)."""
    if motivo["code"] in CODIGOS_DE_MOTIVO:
        return motivo
    textos = [motivo.get(c) for c in ("detalle", "mensaje", "code")]
    mejor = next((t for t in textos if isinstance(t, str) and t), None)
    # Fix round 2 ítem 2: sin ningún texto no vacío no se inventa un mensaje
    # vacío; el frontend muestra el texto genérico de estado_no_continuable.
    if mejor is None:
        return {"code": "estado_no_continuable"}
    return {"code": "estado_no_continuable", "mensaje": recortar_redactado(mejor, MOTIVO_MAX)}


def _no_continuable(estado: dict) -> HTTPException:
    """ENMIENDA ítem 6 + Ruling D (Task 7): el rechazo de /continue cuando el
    pre-vuelo dice continuable=false, por motivo.code -- nunca se inventa
    estado_no_continuable para todo. Nada de esto llama a /continue."""
    motivo = estado["motivo"]
    code = motivo["code"]
    if code == "prevuelo_rechazado":
        veredicto = estado["veredicto"]
        if veredicto is None:
            return _prevuelo_no_disponible()
        detalle = {"code": "prevuelo_rechazado", "violaciones": veredicto["violaciones"],
                   "costo_max_usd": veredicto["costo_max_usd"], "pasos_costo": veredicto["pasos_costo"]}
        return HTTPException(status_code=422, detail=detalle)
    texto = motivo.get("detalle") if isinstance(motivo.get("detalle"), str) else None
    if code == "limite_de_activos":
        detalle = {"code": "limite_de_activos", "detalle": texto}
        return HTTPException(status_code=429, detail=detalle)
    if code in ("reasignacion_invalida", "plan_rechazado"):
        detalle = {"code": code, "detalle": motivo.get("detalle")}
        return HTTPException(status_code=422, detail=detalle)
    if code == "kill_switch":
        mejor_texto = texto or motivo.get("mensaje") or code
        detalle = {"code": "jacobs_rechazo", "status": 423, "motivo": recortar_redactado(mejor_texto, MOTIVO_MAX)}
        return HTTPException(status_code=423, detail=detalle)
    # `_continuable` ya dejó sólo CODIGOS_DE_MOTIVO (un code desconocido llega
    # como estado_no_continuable): el resto es un code de Jacobs con sus campos.
    return HTTPException(status_code=409, detail=motivo)


# Fix round 1 ítem 4: el 200 de /continue con SÓLO las claves del contrato.
def _respuesta_de_continuar(data: dict) -> dict:
    """Jacobs ya lanzó el pipeline: un campo mal formado se omite (no se
    rompe la respuesta) y tampoco llega al evento de WS."""
    data = _costos_saneados(data)
    validos = {
        "pipeline_id": lambda v: isinstance(v, str),
        "status": lambda v: isinstance(v, str),
        "run_epoch": lambda v: isinstance(v, int) and not isinstance(v, bool),
        "pasos_a_correr": _lista_de_pasos,
        "pasos_reusados": _lista_de_pasos,
        "costo_max_usd": lambda v: True,   # ya saneado u omitido por _costos_saneados
        "pasos_costo": lambda v: True,     # idem
    }
    return {clave: data[clave] for clave, valido in validos.items() if clave in data and valido(data[clave])}


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


async def _require_pipeline_owner(pipeline_id: str, user: AuthUser) -> str | None:
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
                "SELECT user_id, tenant_id, owner_ack_at, name FROM jacobs_pipelines WHERE pipeline_id=%s",
                (pipeline_id,),
            )
            row = await cur.fetchone()
    if row is None or row[2] is None or not es_del_usuario(row[0], row[1], user):
        raise HTTPException(status_code=404, detail="pipeline_no_encontrado")
    # El nombre lo usa continue para el estado del panel (sin otra consulta).
    return row[3]


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


# Causa de un pipeline detenido (desvío DV-9 del plan): el último de estos
# eventos de jacobs_events manda el tipo. paso/detalle (fix round 1 ítem 1):
# si el último es PIPELINE_ABORTED con `failed_steps` legible (lista no vacía
# de enteros; jax jacobs/executor.py lo escribe con {at_wave, failed_steps,
# errores}), el paso es el menor de failed_steps y el detalle su `errores`.
# Si no, el último STEP_FAILED -- que puede ser de un paso skip_on_fail que
# NO causó el aborto, por eso es sólo el respaldo.
EVENTOS_DE_CAUSA = {
    "STEP_FAILED": "fallo",
    "PIPELINE_ABORTED": "fallo",
    "PIPELINE_CANCELLED": "cancelado",
    "KILL_SWITCH_ABORTED": "kill_switch",
    "REAPED": "expirado",
}
ESTADOS_CONTINUABLES = ("aborted", "expired")


def sql_eventos_de_causa(n_ids: int) -> str:
    """Una consulta por lista, por idx_events_pipeline (jax/jacobs/store.py).
    Sin ORDER BY a propósito: el orden por id se hace en Python sobre pocas
    filas y así el plan no necesita filesort (EXPLAIN en
    tests/test_pipelines_continuar.py)."""
    ids = ", ".join(["%s"] * n_ids)
    tipos = ", ".join(["%s"] * len(EVENTOS_DE_CAUSA))
    return (f"SELECT pipeline_id, id, event_type, payload FROM jacobs_events "
            f"WHERE pipeline_id IN ({ids}) AND event_type IN ({tipos})")


def _payload(crudo) -> dict:
    if isinstance(crudo, dict):
        return crudo
    try:
        datos = json.loads(crudo) if crudo else {}
    except ValueError:
        return {}  # payload ilegible: la causa sale sin paso ni detalle; nunca se inventan
    return datos if isinstance(datos, dict) else {}


def causa_de(eventos: list[tuple[int, str, str | None]]) -> dict:
    """eventos: (id, event_type, payload) de UN pipeline."""
    if not eventos:
        return {"tipo": "desconocida"}
    ordenados = sorted(eventos, key=lambda e: e[0])
    causa = {"tipo": EVENTOS_DE_CAUSA[ordenados[-1][1]]}
    if ordenados[-1][1] == "PIPELINE_ABORTED":
        datos = _payload(ordenados[-1][2])
        fallidos = datos.get("failed_steps")
        if (isinstance(fallidos, list) and fallidos
                and all(isinstance(i, int) and not isinstance(i, bool) for i in fallidos)):
            causa["paso"] = min(fallidos)
            errores = datos.get("errores")
            if isinstance(errores, dict):
                error = errores.get(str(causa["paso"]), errores.get(causa["paso"]))
                if isinstance(error, str) and error:
                    causa["detalle"] = recortar_redactado(error, MOTIVO_MAX)
            return causa
    if causa["tipo"] == "fallo":
        fallos = [e for e in ordenados if e[1] == "STEP_FAILED"]
        if fallos:
            datos = _payload(fallos[-1][2])
            paso = datos.get("step_index")
            if isinstance(paso, int) and not isinstance(paso, bool):
                causa["paso"] = paso
            if datos.get("error"):
                causa["detalle"] = recortar_redactado(str(datos["error"]), MOTIVO_MAX)
    return causa


@router.get("")
async def list_pipelines(user: AuthUser = Depends(get_current_user)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(SQL_PIPELINES_DEL_USUARIO,
                              (user.user_id, user.tenant_id, LISTA_PIPELINES_MAX))
            filas = await cur.fetchall()
            detenidos = [pid for pid, _n, st, _c, _u in filas if st in ESTADOS_CONTINUABLES]
            eventos = defaultdict(list)
            if detenidos:
                await cur.execute(sql_eventos_de_causa(len(detenidos)), (*detenidos, *EVENTOS_DE_CAUSA))
                for pid, evento_id, tipo, payload in await cur.fetchall():
                    eventos[pid].append((evento_id, tipo, payload))
    return {"pipelines": [
        {"pipeline_id": pid, "name": name, "status": st, "created_at": c, "updated_at": u,
         "causa": causa_de(eventos[pid]) if st in ESTADOS_CONTINUABLES else None}
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
    confirmado = _confirmado_del_cliente(body.pop("costo_confirmado_usd", None))
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
        data = _costos_saneados(_json_de_jacobs(r))
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


@router.post("/{pipeline_id}/continue/preflight")
async def continue_preflight(pipeline_id: str, pedido: PedidoDeContinuarPrevuelo,
                             user: AuthUser = Depends(get_current_user)):
    await _require_pipeline_owner(pipeline_id, user)
    umbral = await ajustes.valor(ajustes.CONFIRMAR_USD)
    client = await get_http_client()
    try:
        return await _continuable(client, pipeline_id, user, pedido.reasignar, umbral)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail={"code": "jacobs_no_responde", "motivo": recortar_redactado(str(e), MOTIVO_MAX)})


@router.post("/{pipeline_id}/continue")
async def continue_pipeline(pipeline_id: str, pedido: PedidoDeContinuar,
                            user: AuthUser = Depends(get_current_user)):
    nombre = await _require_pipeline_owner(pipeline_id, user)
    # Continuar ocupa un cupo del tenant, igual que crear (desvío DV-11).
    limite = await ajustes.valor(ajustes.MAX_PIPELINES)
    if not await resource_manager.can_start_pipeline(user.tenant_id, limite):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": "limite_de_pipelines", "max": limite},
        )
    confirmado = _confirmado_del_cliente(pedido.costo_confirmado_usd)
    umbral = await ajustes.valor(ajustes.CONFIRMAR_USD)
    client = await get_http_client()
    try:
        estado = await _continuable(client, pipeline_id, user, pedido.reasignar, umbral)
        if not estado["continuable"]:
            raise _no_continuable(estado)
        _exigir_consentimiento(estado["veredicto"], confirmado)
        cuerpo = _cuerpo_de_continuar(user, pedido.reasignar)
        # Siempre (desvío DV-7), igual que crear: Jacobs responde 409
        # costo_supera_lo_aceptado sin continuar si su pre-vuelo da más.
        cuerpo["costo_max_aceptado_usd"] = _monto_texto(confirmado if confirmado is not None else umbral)
        r = await client.post(f"{JACOBS_URL}/pipeline/{pipeline_id}/continue", json=cuerpo,
                              timeout=JACOBS_PIPELINE_TIMEOUT)
        data = _respuesta_de_continuar(_json_de_jacobs(r))
        await resource_manager.admit_pipeline(user.tenant_id, pipeline_id)
        await engine_state.continuar_pipeline(
            PipelineState(pipeline_id=pipeline_id, tenant_id=user.tenant_id, user_id=user.user_id,
                          name=nombre or "", status="running"),
            user.tenant_id, user.user_id,
            {clave: data[clave] for clave in ("run_epoch", "pasos_reusados") if clave in data},
        )
        return data
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail={"code": "jacobs_no_responde", "motivo": recortar_redactado(str(e), MOTIVO_MAX)})
