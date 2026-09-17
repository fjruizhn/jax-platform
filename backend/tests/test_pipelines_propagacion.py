"""Propagación de los rechazos de Jacobs (spec 2026-09-17 §6.1): get, results,
resume y cancel devolvían 200 con el cuerpo de error de Jacobs, así que el
aviso de rechazo nunca aparecía; y un cuerpo no-JSON con status >= 400 se
reportaba como jacobs_no_responde. Puros: Jacobs falso y sin DB.

CODIGOS_DE_JACOBS y _CAMPOS_DE_CODIGO siguen la ENMIENDA DE CONTRATO del
controlador (task-5-brief.md, sección final): "status" en vez de
"status_actual", más "mensaje"/"detalle"/"hay_no_acotados"/"sondeadas"/"ok",
y los 4 códigos nuevos (limite_de_activos, plan_rechazado, plan_inconsistente,
no_existe)."""
import asyncio

import pytest
from fastapi import HTTPException

from api import pipelines as mod
from auth.models import AuthUser
from tests.jacobs_falso import JacobsFalso, preparar, respuesta, violacion

USUARIO = AuthUser(user_id="5", tenant_id="1", role="operator")
PID = "00000000-0000-0000-0000-00000000abcd"
ENDPOINTS = [
    ("get_pipeline", "GET", ""),
    ("get_pipeline_results", "GET", "/results"),
    ("resume_pipeline", "POST", "/resume"),
    ("cancel_pipeline", "POST", "/cancel"),
]


def _correr(corutina):
    try:
        return asyncio.run(corutina)
    except HTTPException as exc:
        return exc


@pytest.mark.parametrize("nombre, metodo, sufijo", ENDPOINTS)
def test_un_4xx_de_jacobs_sale_con_el_mismo_status_y_codigo(monkeypatch, nombre, metodo, sufijo):
    falso = JacobsFalso({(metodo, f"/pipeline/{PID}{sufijo}"): respuesta(409, {"detail": "ya finalizado"})})
    preparar(monkeypatch, falso)
    resultado = _correr(getattr(mod, nombre)(pipeline_id=PID, user=USUARIO))
    assert isinstance(resultado, HTTPException), resultado
    assert (resultado.status_code, resultado.detail) == (
        409, {"code": "jacobs_rechazo", "status": 409, "motivo": "ya finalizado"})


@pytest.mark.parametrize("nombre, metodo, sufijo", ENDPOINTS)
def test_un_error_no_json_de_jacobs_es_rechazo_con_texto_redactado(monkeypatch, nombre, metodo, sufijo):
    texto = "Internal Server Error api_key=sk-FAKE-propagacion fin"
    falso = JacobsFalso({(metodo, f"/pipeline/{PID}{sufijo}"): respuesta(500, texto=texto)})
    preparar(monkeypatch, falso)
    resultado = _correr(getattr(mod, nombre)(pipeline_id=PID, user=USUARIO))
    assert isinstance(resultado, HTTPException), resultado
    assert (resultado.status_code, resultado.detail["code"], resultado.detail["status"]) == (500, "jacobs_rechazo", 500)
    assert "sk-FAKE-propagacion" not in resultado.detail["motivo"]
    assert resultado.detail["motivo"].startswith("Internal Server Error")


def test_un_codigo_propio_de_jacobs_pasa_con_sus_datos_declarados_y_redactados():
    cuerpo = {"detail": {
        "code": "prevuelo_rechazado", "costo_max_usd": "1.00", "pasos_costo": [],
        "violaciones": [violacion(detalle="sonda: api_key=sk-FAKE-violacion fin")], "otro": "no pasa",
    }}
    exc = mod._rechazo_de_jacobs(422, cuerpo, "")
    assert exc.status_code == 422
    assert exc.detail == {
        "code": "prevuelo_rechazado", "costo_max_usd": "1.00", "pasos_costo": [],
        "violaciones": [{"paso": 4, "faceta": "kimi", "regla": "tope_insuficiente",
                         "detalle": "sonda: api_key=*** fin"}],
    }


def test_un_2xx_que_no_es_un_objeto_json_es_jacobs_no_responde():
    exc = _correr(_json_async(respuesta(200, texto="<html>proxy</html>")))
    assert isinstance(exc, HTTPException)
    assert (exc.status_code, exc.detail) == (502, {"code": "jacobs_no_responde", "motivo": "<html>proxy</html>"})


async def _json_async(r):
    return mod._json_de_jacobs(r)


def test_un_codigo_nuevo_de_la_enmienda_pasa_con_su_detalle_redactado():
    """limite_de_activos (enmienda ítem 3, 429 {code, detalle}) no estaba en
    los 5 códigos del brief original."""
    cuerpo = {"detail": {
        "code": "limite_de_activos", "detalle": "tope global, api_key=sk-FAKE-limite fin",
    }}
    exc = mod._rechazo_de_jacobs(429, cuerpo, "")
    assert exc.status_code == 429
    assert exc.detail == {"code": "limite_de_activos", "detalle": "tope global, api_key=*** fin"}


def test_estado_no_continuable_pasa_status_y_mensaje_no_status_actual():
    """Enmienda ítem 2: estado_no_continuable trae {code, status, mensaje},
    NO status_actual. mensaje se redacta y recorta con MOTIVO_MAX."""
    cuerpo = {"detail": {
        "code": "estado_no_continuable", "status": "completed",
        "mensaje": "ya termino, api_key=sk-FAKE-mensaje fin", "status_actual": "completed",
    }}
    exc = mod._rechazo_de_jacobs(409, cuerpo, "")
    assert exc.status_code == 409
    assert exc.detail == {
        "code": "estado_no_continuable", "status": "completed",
        "mensaje": "ya termino, api_key=*** fin",
    }
    assert "status_actual" not in exc.detail


def test_estado_no_continuable_con_status_null_pasa_none():
    cuerpo = {"detail": {"code": "estado_no_continuable", "status": None, "mensaje": "sin estado previo"}}
    exc = mod._rechazo_de_jacobs(409, cuerpo, "")
    assert exc.detail == {"code": "estado_no_continuable", "status": None, "mensaje": "sin estado previo"}


def test_campos_no_declarados_se_descartan():
    cuerpo = {"detail": {
        "code": "plan_inconsistente", "detalle": "el plan no coincide", "status_actual": "corriendo",
        "extra_que_no_pasa": "x", "otro_mas": 123,
    }}
    exc = mod._rechazo_de_jacobs(409, cuerpo, "")
    assert exc.detail == {"code": "plan_inconsistente", "detalle": "el plan no coincide"}
    assert "status_actual" not in exc.detail
    assert "extra_que_no_pasa" not in exc.detail
    assert "otro_mas" not in exc.detail
