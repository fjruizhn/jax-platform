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


# --- Fix round 2, finding 1: orden de cancel y el camino 2xx ---------------

CUERPO_OK = {"pipeline": {"id": PID}, "steps": []}


@pytest.mark.parametrize("nombre, metodo, sufijo", ENDPOINTS)
def test_un_2xx_devuelve_el_cuerpo_de_jacobs_tal_cual(monkeypatch, nombre, metodo, sufijo):
    falso = JacobsFalso({(metodo, f"/pipeline/{PID}{sufijo}"): respuesta(200, CUERPO_OK)})
    preparar(monkeypatch, falso)
    resultado = _correr(getattr(mod, nombre)(pipeline_id=PID, user=USUARIO))
    assert resultado == CUERPO_OK


def test_cancel_no_libera_ni_remueve_si_jacobs_rechaza(monkeypatch):
    falso = JacobsFalso({("POST", f"/pipeline/{PID}/cancel"): respuesta(409, {"detail": "ya finalizado"})})
    registro = preparar(monkeypatch, falso)
    resultado = _correr(mod.cancel_pipeline(pipeline_id=PID, user=USUARIO))
    assert isinstance(resultado, HTTPException), resultado
    assert registro.removidos == []
    assert registro.liberados == []


def test_cancel_no_libera_ni_remueve_si_jacobs_no_responde(monkeypatch):
    falso = JacobsFalso({("POST", f"/pipeline/{PID}/cancel"): respuesta(500, texto="boom")})
    registro = preparar(monkeypatch, falso)
    resultado = _correr(mod.cancel_pipeline(pipeline_id=PID, user=USUARIO))
    assert isinstance(resultado, HTTPException), resultado
    assert registro.removidos == []
    assert registro.liberados == []


def test_cancel_libera_y_remueve_solo_al_confirmar_con_jacobs(monkeypatch):
    falso = JacobsFalso({("POST", f"/pipeline/{PID}/cancel"): respuesta(200, CUERPO_OK)})
    registro = preparar(monkeypatch, falso)
    resultado = _correr(mod.cancel_pipeline(pipeline_id=PID, user=USUARIO))
    assert resultado == CUERPO_OK
    assert registro.removidos == [PID]
    assert registro.liberados == [(USUARIO.tenant_id, PID)]


# --- Fix round 2, finding 2: motivo de jacobs_rechazo con detail dict/lista

def test_motivo_de_un_codigo_ajeno_usa_su_detalle_redactado():
    """Un code fuera de CODIGOS_DE_JACOBS cae en jacobs_rechazo; el motivo sale
    de detalle/mensaje/code, no del repr de Python del dict completo.

    El ejemplo era `kill_switch` hasta que el Ruling R54 del plan J lo sumó a
    la lista (el pre-vuelo mira el freno y su 423 se muestra con su texto): el
    caso ajeno se prueba con un código que Jacobs no emite."""
    cuerpo = {"detail": {"code": "codigo_que_no_existe", "detalle": "corte de emergencia, api_key=sk-FAKE-kill fin"}}
    exc = mod._rechazo_de_jacobs(423, cuerpo, "")
    assert exc.detail == {"code": "jacobs_rechazo", "status": 423,
                          "motivo": "corte de emergencia, api_key=*** fin"}


def test_motivo_de_un_codigo_ajeno_sin_detalle_usa_mensaje():
    cuerpo = {"detail": {"code": "otro_code", "mensaje": "algo paso, api_key=sk-FAKE-otro fin"}}
    exc = mod._rechazo_de_jacobs(500, cuerpo, "")
    assert exc.detail["motivo"] == "algo paso, api_key=*** fin"


def test_motivo_de_un_codigo_ajeno_sin_detalle_ni_mensaje_usa_el_code():
    cuerpo = {"detail": {"code": "otro_code"}}
    exc = mod._rechazo_de_jacobs(500, cuerpo, "")
    assert exc.detail["motivo"] == "otro_code"


def test_motivo_con_detail_lista_usa_el_texto_crudo():
    """422 de validación de FastAPI: detail es una lista, no un dict."""
    cuerpo = {"detail": [{"loc": ["body", "x"], "msg": "field required"}]}
    exc = mod._rechazo_de_jacobs(422, cuerpo, "texto crudo de la respuesta")
    assert exc.detail["motivo"] == "texto crudo de la respuesta"


def test_motivo_con_body_dict_sin_detail_usa_el_texto_crudo():
    cuerpo = {"otra_cosa": "sin detail"}
    exc = mod._rechazo_de_jacobs(500, cuerpo, "texto crudo")
    assert exc.detail["motivo"] == "texto crudo"


# --- Fix round 2, finding 3: pasos_costo y sondeadas saneados --------------

def test_pasos_costo_pasa_saneado_y_motivo_redactado():
    cuerpo = {"detail": {
        "code": "prevuelo_rechazado", "costo_max_usd": "1.00",
        "pasos_costo": [{
            "paso": 0, "faceta": "jekyll", "modelo": "m", "llamadas_max": 1,
            "tokens_in_max": 100, "tokens_out_max": 1000, "usd_max": "0.10",
            "motivo": "sonda: api_key=sk-FAKE-paso fin", "extra_que_no_pasa": "x",
        }],
        "sondeadas": ["jekyll", 123, "kimi"],
        "violaciones": [],
    }}
    exc = mod._rechazo_de_jacobs(422, cuerpo, "")
    assert exc.detail["pasos_costo"] == [{
        "paso": 0, "faceta": "jekyll", "modelo": "m", "llamadas_max": 1,
        "tokens_in_max": 100, "tokens_out_max": 1000, "usd_max": "0.10",
        "motivo": "sonda: api_key=*** fin",
    }]
    assert "extra_que_no_pasa" not in exc.detail["pasos_costo"][0]
    assert exc.detail["sondeadas"] == ["jekyll", "kimi"]


def test_pasos_costo_no_lista_cae_a_lista_vacia():
    cuerpo = {"detail": {"code": "prevuelo_rechazado", "costo_max_usd": "1.00", "pasos_costo": "no es lista"}}
    exc = mod._rechazo_de_jacobs(422, cuerpo, "")
    assert exc.detail["pasos_costo"] == []


# --- Fix round 2, ítem 5: montos de un rechazo también salen en punto fijo ---

def test_rechazo_con_montos_como_numero_json_sale_en_punto_fijo():
    """costo_max_usd/costo_max_aceptado_usd/usd_max de un rechazo de Jacobs
    pasan por el mismo `_monto_texto` que el veredicto -- nunca salen como
    float ni notación científica, aunque Jacobs los mande como número JSON."""
    cuerpo = {"detail": {
        "code": "costo_supera_lo_aceptado", "costo_max_usd": 0.7, "costo_max_aceptado_usd": 0.6,
        "pasos_costo": [{"paso": 0, "faceta": "jekyll", "modelo": "m", "llamadas_max": 1,
                         "tokens_in_max": 100, "tokens_out_max": 1000, "usd_max": 0.1, "motivo": None}],
    }}
    exc = mod._rechazo_de_jacobs(409, cuerpo, "")
    assert exc.detail["costo_max_usd"] == "0.7"
    assert exc.detail["costo_max_aceptado_usd"] == "0.6"
    assert exc.detail["pasos_costo"][0]["usd_max"] == "0.1"


def test_rechazo_con_costo_max_usd_ilegible_omite_el_campo_sin_romper():
    cuerpo = {"detail": {"code": "prevuelo_rechazado", "costo_max_usd": "no-es-un-monto",
                         "pasos_costo": [], "violaciones": []}}
    exc = mod._rechazo_de_jacobs(422, cuerpo, "")
    assert exc.detail["code"] == "prevuelo_rechazado"
    assert "costo_max_usd" not in exc.detail


def test_rechazo_con_usd_max_de_un_paso_ilegible_lo_deja_null_sin_romper():
    cuerpo = {"detail": {"code": "prevuelo_rechazado", "costo_max_usd": "1.00", "violaciones": [],
                         "pasos_costo": [{"paso": 0, "faceta": "jekyll", "modelo": "m", "llamadas_max": 1,
                                         "tokens_in_max": 100, "tokens_out_max": 1000,
                                         "usd_max": "no-es-un-monto", "motivo": None}]}}
    exc = mod._rechazo_de_jacobs(422, cuerpo, "")
    assert exc.detail["pasos_costo"][0]["usd_max"] is None


# Plan J, Ruling de la sesión principal (2026-09-17): resume también corre el
# pre-vuelo en Jacobs. Sus dos rechazos propios salen con su status y sus datos
# declarados (control del contrato: el helper único ya los deja pasar).
def test_resume_propaga_el_rechazo_del_prevuelo_con_sus_violaciones(monkeypatch):
    v = violacion()
    falso = JacobsFalso({("POST", f"/pipeline/{PID}/resume"): respuesta(422, {"detail": {
        "code": "prevuelo_rechazado", "violaciones": [v], "costo_max_usd": "0.000000", "pasos_costo": [],
    }})})
    preparar(monkeypatch, falso)
    resultado = _correr(mod.resume_pipeline(pipeline_id=PID, user=USUARIO))
    assert isinstance(resultado, HTTPException), resultado
    assert resultado.status_code == 422
    assert resultado.detail["code"] == "prevuelo_rechazado"
    assert resultado.detail["violaciones"] == [v]


def test_resume_propaga_prevuelo_no_disponible(monkeypatch):
    falso = JacobsFalso({("POST", f"/pipeline/{PID}/resume"): respuesta(503, {"detail": {
        "code": "prevuelo_no_disponible", "motivo": "base caída"}})})
    preparar(monkeypatch, falso)
    resultado = _correr(mod.resume_pipeline(pipeline_id=PID, user=USUARIO))
    assert isinstance(resultado, HTTPException), resultado
    assert (resultado.status_code, resultado.detail["code"]) == (503, "prevuelo_no_disponible")
