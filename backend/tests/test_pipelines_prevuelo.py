"""Pre-vuelo antes de gastar (spec 2026-09-17 §6.1): la Mesa pregunta a Jacobs
si se puede y cuánto costaría como máximo, pide consentimiento por encima del
umbral o con un paso sin precio, y la condición la hace cumplir quien gasta
(costo_max_aceptado_usd). Puros salvo el último (HTTP real con auth y ajuste)."""
import asyncio

import pytest
from fastapi import HTTPException

from api import pipelines as mod
from auth.models import AuthUser
from tests.identidades import cabeceras
from tests.jacobs_falso import JacobsFalso, paso_costo, preparar, respuesta, veredicto, violacion

USUARIO = AuthUser(user_id="5", tenant_id="1", role="operator")
PASOS = [{"facet": "kimi", "capability": "generate", "prompt": "x", "motor": "kimi"}]


def _correr(corutina):
    try:
        return asyncio.run(corutina)
    except HTTPException as exc:
        return exc


class _Pedido:
    def __init__(self, cuerpo):
        self._cuerpo = cuerpo

    async def json(self):
        return self._cuerpo


def _crear(cuerpo):
    return _correr(mod.create_pipeline(request=_Pedido(cuerpo), user=USUARIO))


def _prevuelo(falso_veredicto, status=200):
    return {("POST", "/preflight"): respuesta(status, falso_veredicto)}


# ---------------------------------------------------------------- /preflight

def test_preflight_reenvia_identidad_y_pasos_y_agrega_umbral(monkeypatch):
    falso = JacobsFalso(_prevuelo(veredicto(costo="0.10")))
    preparar(monkeypatch, falso)
    r = _correr(mod.preflight_pipeline(pedido=mod.PedidoDePrevuelo(steps=PASOS), user=USUARIO))
    assert falso.cuerpos("POST", "/preflight") == [
        {"invoked_by": "plataforma", "user_id": "5", "tenant_id": "1", "steps": PASOS}]
    assert (r["ok"], r["costo_max_usd"], r["umbral_usd"], r["requiere_confirmacion"]) == (True, "0.10", "0.50", False)


def test_preflight_pide_confirmacion_por_encima_del_umbral(monkeypatch):
    preparar(monkeypatch, JacobsFalso(_prevuelo(veredicto(costo="0.60"))))
    r = _correr(mod.preflight_pipeline(pedido=mod.PedidoDePrevuelo(steps=PASOS), user=USUARIO))
    assert r["requiere_confirmacion"] is True


def test_preflight_pide_confirmacion_con_un_paso_sin_precio(monkeypatch):
    v = veredicto(costo="0.10", pasos_costo=[paso_costo(usd="0.10"), paso_costo(paso=1, usd=None, motivo="sin_precio")])
    preparar(monkeypatch, JacobsFalso(_prevuelo(v)))
    r = _correr(mod.preflight_pipeline(pedido=mod.PedidoDePrevuelo(steps=PASOS), user=USUARIO))
    assert r["requiere_confirmacion"] is True


def test_preflight_sin_ok_es_prevuelo_no_disponible(monkeypatch):
    malo = veredicto()
    del malo["ok"]
    preparar(monkeypatch, JacobsFalso(_prevuelo(malo)))
    r = _correr(mod.preflight_pipeline(pedido=mod.PedidoDePrevuelo(steps=PASOS), user=USUARIO))
    assert isinstance(r, HTTPException)
    assert (r.status_code, r.detail) == (502, {"code": "prevuelo_no_disponible"})


def test_preflight_con_pasos_de_costo_ilegibles_es_prevuelo_no_disponible(monkeypatch):
    preparar(monkeypatch, JacobsFalso(_prevuelo(veredicto(pasos_costo=[paso_costo(usd="abc")]))))
    r = _correr(mod.preflight_pipeline(pedido=mod.PedidoDePrevuelo(steps=PASOS), user=USUARIO))
    assert (r.status_code, r.detail) == (502, {"code": "prevuelo_no_disponible"})


def test_preflight_503_de_jacobs_pasa_con_su_codigo(monkeypatch):
    preparar(monkeypatch, JacobsFalso(_prevuelo({"detail": {"code": "prevuelo_no_disponible"}}, status=503)))
    r = _correr(mod.preflight_pipeline(pedido=mod.PedidoDePrevuelo(steps=PASOS), user=USUARIO))
    assert (r.status_code, r.detail) == (503, {"code": "prevuelo_no_disponible"})


# ------------------------------------------------------ hay_no_acotados (ENMIENDA item 4)

def test_preflight_hay_no_acotados_declarado_true_pide_confirmacion_aunque_todos_tengan_precio(monkeypatch):
    v = veredicto(costo="0.10", hay_no_acotados=True)
    preparar(monkeypatch, JacobsFalso(_prevuelo(v)))
    r = _correr(mod.preflight_pipeline(pedido=mod.PedidoDePrevuelo(steps=PASOS), user=USUARIO))
    assert r["requiere_confirmacion"] is True


def test_preflight_hay_no_acotados_no_booleano_es_prevuelo_no_disponible(monkeypatch):
    v = veredicto(costo="0.10", hay_no_acotados="si")
    preparar(monkeypatch, JacobsFalso(_prevuelo(v)))
    r = _correr(mod.preflight_pipeline(pedido=mod.PedidoDePrevuelo(steps=PASOS), user=USUARIO))
    assert (r.status_code, r.detail) == (502, {"code": "prevuelo_no_disponible"})


# ---------------------------------------------------------------- creación

def test_crear_sin_pasos_es_422_y_no_llama_a_jacobs(monkeypatch):
    falso = JacobsFalso()
    preparar(monkeypatch, falso)
    r = _crear({"name": "x", "objective": "x"})
    assert (r.status_code, r.detail) == (422, {"code": "pasos_requeridos"})
    assert falso.llamadas == []


def test_crear_con_violaciones_es_422_y_no_crea(monkeypatch):
    falso = JacobsFalso(_prevuelo(veredicto(ok=False, violaciones=[violacion()])))
    preparar(monkeypatch, falso)
    r = _crear({"name": "x", "steps": PASOS})
    assert r.status_code == 422
    assert r.detail["code"] == "prevuelo_rechazado"
    assert r.detail["violaciones"][0]["regla"] == "tope_insuficiente"
    assert falso.cuerpos("POST", "/pipeline") == []


def test_crear_caro_sin_confirmar_es_409_con_el_costo_y_el_umbral(monkeypatch):
    falso = JacobsFalso(_prevuelo(veredicto(costo="0.60")))
    preparar(monkeypatch, falso)
    r = _crear({"name": "x", "steps": PASOS})
    assert r.status_code == 409
    assert (r.detail["code"], r.detail["costo_max_usd"], r.detail["umbral_usd"]) == ("confirmacion_de_costo", "0.60", "0.50")
    assert falso.cuerpos("POST", "/pipeline") == []


def test_crear_con_una_confirmacion_menor_al_costo_es_409(monkeypatch):
    falso = JacobsFalso(_prevuelo(veredicto(costo="0.60")))
    preparar(monkeypatch, falso)
    r = _crear({"name": "x", "steps": PASOS, "costo_confirmado_usd": "0.59"})
    assert (r.status_code, r.detail["code"]) == (409, "confirmacion_de_costo")
    assert falso.cuerpos("POST", "/pipeline") == []


def test_crear_confirmado_manda_el_costo_aceptado_a_jacobs(monkeypatch):
    pid = "11111111-1111-1111-1111-111111111111"
    falso = JacobsFalso({**_prevuelo(veredicto(costo="0.60")),
                         ("POST", "/pipeline"): respuesta(200, {"pipeline_id": pid, "costo_max_usd": "0.60"})})
    registro = preparar(monkeypatch, falso)
    r = _crear({"name": "x", "steps": PASOS, "costo_confirmado_usd": "0.60"})
    (cuerpo,) = falso.cuerpos("POST", "/pipeline")
    assert cuerpo["costo_max_aceptado_usd"] == "0.60"
    assert "costo_confirmado_usd" not in cuerpo
    assert (cuerpo["invoked_by"], cuerpo["user_id"], cuerpo["tenant_id"]) == ("plataforma", "5", "1")
    assert r["pipeline_id"] == pid
    assert registro.admitidos == [("1", pid)]


def test_crear_sin_confirmacion_requerida_acepta_hasta_el_umbral(monkeypatch):
    falso = JacobsFalso({**_prevuelo(veredicto(costo="0.10")),
                         ("POST", "/pipeline"): respuesta(200, {"pipeline_id": None})})
    preparar(monkeypatch, falso)
    _crear({"name": "x", "steps": PASOS})
    (cuerpo,) = falso.cuerpos("POST", "/pipeline")
    assert cuerpo["costo_max_aceptado_usd"] == "0.50"


def test_crear_costo_supera_lo_aceptado_pasa_con_sus_datos(monkeypatch):
    rechazo = {"detail": {"code": "costo_supera_lo_aceptado", "costo_max_usd": "0.70", "costo_max_aceptado_usd": "0.60"}}
    falso = JacobsFalso({**_prevuelo(veredicto(costo="0.60")), ("POST", "/pipeline"): respuesta(409, rechazo)})
    preparar(monkeypatch, falso)
    r = _crear({"name": "x", "steps": PASOS, "costo_confirmado_usd": "0.60"})
    assert (r.status_code, r.detail) == (409, rechazo["detail"])


def test_crear_con_error_no_json_de_jacobs_es_rechazo_con_su_status(monkeypatch):
    falso = JacobsFalso({**_prevuelo(veredicto()), ("POST", "/pipeline"): respuesta(500, texto="boom")})
    preparar(monkeypatch, falso)
    r = _crear({"name": "x", "steps": PASOS})
    assert (r.status_code, r.detail) == (500, {"code": "jacobs_rechazo", "status": 500, "motivo": "boom"})


def test_crear_con_costo_confirmado_invalido_es_422(monkeypatch):
    falso = JacobsFalso()
    preparar(monkeypatch, falso)
    r = _crear({"name": "x", "steps": PASOS, "costo_confirmado_usd": "-1"})
    assert (r.status_code, r.detail) == (422, {"code": "costo_confirmado_invalido"})
    assert falso.llamadas == []


# ---------------------------------------------------------------- HTTP real

def test_preflight_por_http_usa_el_umbral_del_ajuste(client, ajustes_en_db, monkeypatch):
    ajustes_en_db.poner(**{**ajustes_en_db.validos, "pipeline_confirmar_usd": "0.05"})
    falso = JacobsFalso(_prevuelo(veredicto(costo="0.10")))

    async def cliente():
        return falso

    monkeypatch.setattr(mod, "get_http_client", cliente)
    monkeypatch.setattr(mod, "JACOBS_URL", "http://jacobs.test/jacobs")
    r = client.post("/api/pipelines/preflight", json={"steps": PASOS}, headers=cabeceras(client, "prevuelo-http"))
    assert r.status_code == 200, r.text
    assert (r.json()["umbral_usd"], r.json()["requiere_confirmacion"]) == ("0.05", True)
