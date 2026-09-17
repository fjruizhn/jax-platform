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


def test_preflight_con_ajuste_ilegible_es_503_y_no_llama_a_jacobs(client, ajustes_en_db, monkeypatch):
    """El ajuste se lee FUERA del try de preflight_pipeline: un
    pipeline_confirmar_usd ilegible es 503 ajuste_ilegible (handler de
    main.py), nunca un 502 de Jacobs -- y Jacobs no se llega a llamar
    (fix round 1 ítem 2)."""
    ajustes_en_db.poner(**{**ajustes_en_db.validos, "pipeline_confirmar_usd": "no-es-un-monto"})
    falso = JacobsFalso()  # ninguna ruta declarada: si se llama, AssertionError

    async def cliente():
        return falso

    monkeypatch.setattr(mod, "get_http_client", cliente)
    monkeypatch.setattr(mod, "JACOBS_URL", "http://jacobs.test/jacobs")
    r = client.post("/api/pipelines/preflight", json={"steps": PASOS},
                     headers=cabeceras(client, "prevuelo-ajuste-ilegible"))
    assert r.status_code == 503, r.text
    assert r.json()["detail"] == {"code": "ajuste_ilegible", "clave": "pipeline_confirmar_usd"}
    assert falso.llamadas == []


# ------------------------------------------- montos: forma fija, no float (fix round 1 ítem 1)

def test_preflight_acepta_montos_de_jacobs_como_numero_json_y_devuelve_string(monkeypatch):
    v = veredicto(costo=0.6, pasos_costo=[paso_costo(usd=0.1)])
    preparar(monkeypatch, JacobsFalso(_prevuelo(v)))
    r = _correr(mod.preflight_pipeline(pedido=mod.PedidoDePrevuelo(steps=PASOS), user=USUARIO))
    # 0.6 > 0.50 (umbral por defecto de `preparar`): Decimal, no float -- una
    # comparación en punto flotante de estos valores también daría True acá,
    # pero el punto es que costo_max_usd/usd_max NUNCA salen como float.
    assert (r["costo_max_usd"], r["pasos_costo"][0]["usd_max"], r["requiere_confirmacion"]) == ("0.6", "0.1", True)
    assert isinstance(r["costo_max_usd"], str) and isinstance(r["pasos_costo"][0]["usd_max"], str)


@pytest.mark.parametrize("costo_malo", ["no-es-un-monto", "-1", True])
def test_preflight_costo_max_usd_invalido_es_prevuelo_no_disponible(monkeypatch, costo_malo):
    v = veredicto()
    v["costo_max_usd"] = costo_malo
    preparar(monkeypatch, JacobsFalso(_prevuelo(v)))
    r = _correr(mod.preflight_pipeline(pedido=mod.PedidoDePrevuelo(steps=PASOS), user=USUARIO))
    assert (r.status_code, r.detail) == (502, {"code": "prevuelo_no_disponible"})


# ------------------------------------------- violaciones/sondeadas mal formadas (fix round 1 ítem 4)

def test_preflight_violaciones_no_lista_es_prevuelo_no_disponible(monkeypatch):
    v = veredicto()
    v["violaciones"] = "no-es-lista"
    preparar(monkeypatch, JacobsFalso(_prevuelo(v)))
    r = _correr(mod.preflight_pipeline(pedido=mod.PedidoDePrevuelo(steps=PASOS), user=USUARIO))
    assert (r.status_code, r.detail) == (502, {"code": "prevuelo_no_disponible"})


def test_preflight_violaciones_con_item_no_dict_es_prevuelo_no_disponible(monkeypatch):
    v = veredicto(violaciones=[1])
    preparar(monkeypatch, JacobsFalso(_prevuelo(v)))
    r = _correr(mod.preflight_pipeline(pedido=mod.PedidoDePrevuelo(steps=PASOS), user=USUARIO))
    assert (r.status_code, r.detail) == (502, {"code": "prevuelo_no_disponible"})


def test_preflight_sondeadas_no_lista_es_prevuelo_no_disponible(monkeypatch):
    v = veredicto()
    v["sondeadas"] = "no-es-lista"
    preparar(monkeypatch, JacobsFalso(_prevuelo(v)))
    r = _correr(mod.preflight_pipeline(pedido=mod.PedidoDePrevuelo(steps=PASOS), user=USUARIO))
    assert (r.status_code, r.detail) == (502, {"code": "prevuelo_no_disponible"})


def test_preflight_sondeadas_con_item_no_str_es_prevuelo_no_disponible(monkeypatch):
    v = veredicto()
    v["sondeadas"] = ["kimi", 5]
    preparar(monkeypatch, JacobsFalso(_prevuelo(v)))
    r = _correr(mod.preflight_pipeline(pedido=mod.PedidoDePrevuelo(steps=PASOS), user=USUARIO))
    assert (r.status_code, r.detail) == (502, {"code": "prevuelo_no_disponible"})


# ------------------------------------------- pasos inválidos: un solo criterio (fix round 1 ítem 5)

@pytest.mark.parametrize("steps_malos", [[], [1]])
def test_preflight_pasos_invalidos_es_422_y_no_llama_a_jacobs(monkeypatch, steps_malos):
    falso = JacobsFalso()
    preparar(monkeypatch, falso)
    r = _correr(mod.preflight_pipeline(pedido=mod.PedidoDePrevuelo(steps=steps_malos), user=USUARIO))
    assert (r.status_code, r.detail) == (422, {"code": "pasos_requeridos"})
    assert falso.llamadas == []


@pytest.mark.parametrize("steps_malos", [[], [1]])
def test_crear_pasos_invalidos_es_422_y_no_llama_a_jacobs(monkeypatch, steps_malos):
    falso = JacobsFalso()
    preparar(monkeypatch, falso)
    r = _crear({"name": "x", "steps": steps_malos})
    assert (r.status_code, r.detail) == (422, {"code": "pasos_requeridos"})
    assert falso.llamadas == []


# ------------------------------------------- costo_confirmado_usd: JSON number y forma estricta (fix round 1 ítems 2 y 3)

def test_crear_costo_confirmado_como_numero_json_es_aceptado(monkeypatch):
    pid = "22222222-2222-2222-2222-222222222222"
    falso = JacobsFalso({**_prevuelo(veredicto(costo="0.60")),
                         ("POST", "/pipeline"): respuesta(200, {"pipeline_id": pid})})
    preparar(monkeypatch, falso)
    r = _crear({"name": "x", "steps": PASOS, "costo_confirmado_usd": 0.6})
    (cuerpo,) = falso.cuerpos("POST", "/pipeline")
    assert cuerpo["costo_max_aceptado_usd"] == "0.6"
    assert r["pipeline_id"] == pid


def test_crear_confirmado_al_costo_exacto_con_paso_sin_precio_deja_pasar(monkeypatch):
    """requiere_confirmacion puede venir SÓLO de un paso sin precio (no del
    umbral); confirmar al menos el costo_max_usd alcanza igual (fix round 1
    ítem 2)."""
    v = veredicto(costo="0.10", pasos_costo=[paso_costo(usd="0.10"), paso_costo(paso=1, usd=None, motivo="sin_precio")])
    pid = "33333333-3333-3333-3333-333333333333"
    falso = JacobsFalso({**_prevuelo(v), ("POST", "/pipeline"): respuesta(200, {"pipeline_id": pid})})
    preparar(monkeypatch, falso)
    r = _crear({"name": "x", "steps": PASOS, "costo_confirmado_usd": "0.10"})
    (cuerpo,) = falso.cuerpos("POST", "/pipeline")
    assert cuerpo["costo_max_aceptado_usd"] == "0.10"
    assert r["pipeline_id"] == pid


@pytest.mark.parametrize("crudo", ["1e-7", "1e3", "-0", " 0.6 ", "1_000"])
def test_crear_costo_confirmado_string_no_canonico_es_422(monkeypatch, crudo):
    """`Decimal()` entiende estas formas -- el cliente no puede mandarlas: el
    string tiene que ser canónico (fix round 1 ítem 3)."""
    falso = JacobsFalso()
    preparar(monkeypatch, falso)
    r = _crear({"name": "x", "steps": PASOS, "costo_confirmado_usd": crudo})
    assert (r.status_code, r.detail) == (422, {"code": "costo_confirmado_invalido"})
    assert falso.llamadas == []
