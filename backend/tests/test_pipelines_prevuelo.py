"""Pre-vuelo antes de gastar (spec 2026-09-17 §6.1): la Mesa pregunta a Jacobs
si se puede y cuánto costaría como máximo, pide consentimiento por encima del
umbral o con un paso sin precio, y la condición la hace cumplir quien gasta
(costo_max_aceptado_usd). Puros salvo el último (HTTP real con auth y ajuste)."""
import asyncio
from decimal import Decimal

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
        {"invoked_by": "plataforma", "user_id": "5", "tenant_id": "1", "steps": PASOS, "objective": ""}]
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


# ------------------------------------------- fix round 2 -------------------------------------------

# ítem 1: -0 y notación científica en la salida de _monto_texto

def test_monto_texto_normaliza_menos_cero():
    assert mod._monto_texto(Decimal("-0")) == "0"
    assert mod._monto_texto(Decimal("-0.00")) == "0.00"
    assert mod._monto_texto(Decimal("-0.0000001")) != mod._monto_texto(Decimal("0.0000001"))  # sanity: signo SÍ importa si no es cero
    assert mod._monto_texto(Decimal("0")) == "0"


def test_preflight_costo_de_jacobs_en_notacion_cientifica_sale_en_punto_fijo(monkeypatch):
    v = veredicto(costo="1E-7")
    preparar(monkeypatch, JacobsFalso(_prevuelo(v)))
    r = _correr(mod.preflight_pipeline(pedido=mod.PedidoDePrevuelo(steps=PASOS), user=USUARIO))
    assert r["costo_max_usd"] == "0.0000001"
    assert "E" not in r["costo_max_usd"] and "e" not in r["costo_max_usd"]


# ítem 2: comparación con Decimal, no con float (umbral de confirmación)

def test_preflight_costo_igual_al_umbral_numero_json_no_requiere_confirmacion(monkeypatch):
    preparar(monkeypatch, JacobsFalso(_prevuelo(veredicto(costo=0.5))))
    r = _correr(mod.preflight_pipeline(pedido=mod.PedidoDePrevuelo(steps=PASOS), user=USUARIO))
    assert r["requiere_confirmacion"] is False


def test_preflight_costo_igual_al_umbral_string_no_requiere_confirmacion(monkeypatch):
    preparar(monkeypatch, JacobsFalso(_prevuelo(veredicto(costo="0.50"))))
    r = _correr(mod.preflight_pipeline(pedido=mod.PedidoDePrevuelo(steps=PASOS), user=USUARIO))
    assert r["requiere_confirmacion"] is False


def test_evaluar_veredicto_compara_costo_y_umbral_con_decimal_no_con_float():
    """Prueba de mutación a mano (fix round 2 ítem 2, ver Fix round 2 en
    task-6-report.md para el comando/salida): cambiar `costo > umbral` por
    `float(costo) > float(umbral)` hace FALLAR este test -- 21 dígitos
    decimales no entran en un float de 64 bits, la comparación en punto
    flotante redondea a igualdad y se saltaría una confirmación que sí hace
    falta."""
    crudo = veredicto(costo="0.30000000000000000001")
    r = mod._evaluar_veredicto(crudo, Decimal("0.3"))
    assert r["requiere_confirmacion"] is True


# ítem 3: PedidoDePrevuelo.steps acepta cualquier JSON, decide _exigir_pasos_validos

def test_preflight_por_http_con_steps_no_lista_es_pasos_requeridos(client):
    r = client.post("/api/pipelines/preflight", json={"steps": "x"},
                     headers=cabeceras(client, "prevuelo-steps-no-lista"))
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == {"code": "pasos_requeridos"}


def test_preflight_por_http_sin_steps_es_pasos_requeridos(client):
    r = client.post("/api/pipelines/preflight", json={},
                     headers=cabeceras(client, "prevuelo-sin-steps"))
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == {"code": "pasos_requeridos"}


def test_preflight_por_http_con_steps_como_dict_es_pasos_requeridos(client):
    r = client.post("/api/pipelines/preflight", json={"steps": {}},
                     headers=cabeceras(client, "prevuelo-steps-dict"))
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == {"code": "pasos_requeridos"}


# ítem 5 (ruling del controlador): la respuesta 200 de creación también sale saneada

def test_crear_200_con_costo_como_numero_json_y_paso_con_clave_extra(monkeypatch):
    pid = "44444444-4444-4444-4444-444444444444"
    falso = JacobsFalso({**_prevuelo(veredicto(costo="0.10")),
                         ("POST", "/pipeline"): respuesta(200, {
                             "pipeline_id": pid, "costo_max_usd": 0.1,
                             "pasos_costo": [{**paso_costo(usd=0.1), "clave_extra": "no_pasa"}],
                         })})
    preparar(monkeypatch, falso)
    r = _crear({"name": "x", "steps": PASOS})
    assert r["pipeline_id"] == pid
    assert r["costo_max_usd"] == "0.1"
    assert r["pasos_costo"][0]["usd_max"] == "0.1"
    assert "clave_extra" not in r["pasos_costo"][0]


def test_crear_200_con_costo_max_usd_ilegible_omite_el_campo_sin_romper(monkeypatch):
    pid = "66666666-6666-6666-6666-666666666666"
    falso = JacobsFalso({**_prevuelo(veredicto(costo="0.10")),
                         ("POST", "/pipeline"): respuesta(200, {"pipeline_id": pid, "costo_max_usd": "no-es-un-monto"})})
    preparar(monkeypatch, falso)
    r = _crear({"name": "x", "steps": PASOS})
    assert r["pipeline_id"] == pid
    assert "costo_max_usd" not in r


# ---------------------------------------------------------------- objective (revisión final, crítico 1)
# Jacobs cuenta el objetivo en los tokens de entrada de cada paso
# (jacobs/executor.py `_build_context_input`/`_enrich_prompt`) y su pre-vuelo
# interno de POST /jacobs/pipeline lo recibe: el pre-vuelo de la Mesa tiene que
# mandarlo también, si no subestima el costo que Jacobs aplica al crear.

def test_preflight_reenvia_el_objetivo_a_jacobs(monkeypatch):
    falso = JacobsFalso(_prevuelo(veredicto(costo="0.10")))
    preparar(monkeypatch, falso)
    _correr(mod.preflight_pipeline(pedido=mod.PedidoDePrevuelo(steps=PASOS, objective="investigar X"), user=USUARIO))
    (cuerpo,) = falso.cuerpos("POST", "/preflight")
    assert cuerpo["objective"] == "investigar X"


def test_crear_reenvia_el_objetivo_al_prevuelo_interno(monkeypatch):
    falso = JacobsFalso({**_prevuelo(veredicto(costo="0.10")),
                         ("POST", "/pipeline"): respuesta(200, {"pipeline_id": None})})
    preparar(monkeypatch, falso)
    _crear({"name": "x", "objective": "investigar X", "steps": PASOS})
    (prevuelo,) = falso.cuerpos("POST", "/preflight")
    (creacion,) = falso.cuerpos("POST", "/pipeline")
    assert prevuelo["objective"] == creacion["objective"] == "investigar X"


@pytest.mark.parametrize("cuerpo_extra", [{"objective": None}, {}], ids=["objective-null", "objective-ausente"])
def test_crear_con_objective_null_o_ausente_normaliza_a_string_vacio_en_preflight_y_pipeline(monkeypatch, cuerpo_extra):
    """Bug real: `_objetivo_valido` deja pasar el pre-vuelo con "" cuando el
    body trae `objective: null` (o ni lo trae), pero el body ORIGINAL --sin
    normalizar-- es el que se reenviaba tal cual a POST /jacobs/pipeline:
    Jacobs exige `objective: str` y devuelve 422 sobre un `null`. El valor
    que llega al pre-vuelo y el que llega a la creación tienen que ser EL
    MISMO (create_pipeline en api/pipelines.py)."""
    falso = JacobsFalso({**_prevuelo(veredicto(costo="0.10")),
                         ("POST", "/pipeline"): respuesta(200, {"pipeline_id": None})})
    preparar(monkeypatch, falso)
    _crear({"name": "x", "steps": PASOS, **cuerpo_extra})
    (prevuelo,) = falso.cuerpos("POST", "/preflight")
    (creacion,) = falso.cuerpos("POST", "/pipeline")
    assert prevuelo["objective"] == ""
    assert creacion["objective"] == ""


@pytest.mark.parametrize("objetivo_malo", [5, ["x"], {"a": 1}, True])
def test_preflight_objetivo_no_texto_es_422_y_no_llama_a_jacobs(monkeypatch, objetivo_malo):
    falso = JacobsFalso()
    preparar(monkeypatch, falso)
    r = _correr(mod.preflight_pipeline(pedido=mod.PedidoDePrevuelo(steps=PASOS, objective=objetivo_malo), user=USUARIO))
    assert (r.status_code, r.detail) == (422, {"code": "objetivo_invalido", "max": mod.OBJETIVO_MAX})
    assert falso.llamadas == []


def test_preflight_objetivo_demasiado_largo_es_422_y_el_tope_exacto_pasa(monkeypatch):
    falso = JacobsFalso(_prevuelo(veredicto(costo="0.10")))
    preparar(monkeypatch, falso)
    r = _correr(mod.preflight_pipeline(
        pedido=mod.PedidoDePrevuelo(steps=PASOS, objective="x" * (mod.OBJETIVO_MAX + 1)), user=USUARIO))
    assert (r.status_code, r.detail) == (422, {"code": "objetivo_invalido", "max": mod.OBJETIVO_MAX})
    assert falso.llamadas == []
    r = _correr(mod.preflight_pipeline(
        pedido=mod.PedidoDePrevuelo(steps=PASOS, objective="x" * mod.OBJETIVO_MAX), user=USUARIO))
    assert r["ok"] is True


@pytest.mark.parametrize("objetivo_malo", [5, ["x"]])
def test_crear_objetivo_invalido_es_422_y_no_llama_a_jacobs(monkeypatch, objetivo_malo):
    falso = JacobsFalso()
    preparar(monkeypatch, falso)
    r = _crear({"name": "x", "objective": objetivo_malo, "steps": PASOS})
    assert (r.status_code, r.detail) == (422, {"code": "objetivo_invalido", "max": mod.OBJETIVO_MAX})
    assert falso.llamadas == []


def test_crear_objetivo_demasiado_largo_es_422_y_no_llama_a_jacobs(monkeypatch):
    falso = JacobsFalso()
    preparar(monkeypatch, falso)
    r = _crear({"name": "x", "objective": "x" * (mod.OBJETIVO_MAX + 1), "steps": PASOS})
    assert (r.status_code, r.detail["code"]) == (422, "objetivo_invalido")
    assert falso.llamadas == []


def test_preflight_por_http_con_objetivo_no_texto_es_objetivo_invalido(client):
    r = client.post("/api/pipelines/preflight", json={"steps": PASOS, "objective": 5},
                    headers=cabeceras(client, "prevuelo-objetivo-numero"))
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["code"] == "objetivo_invalido"


# ---------------------------------------------------------------- contrato actual de Jacobs (revisión final, importante 2)
# Plan J R18: todo monto de Jacobs es string con 6 decimales; R17: los
# rechazos de /jacobs/preflight son dict {code, detalle} con detalle string.

def test_preflight_con_montos_de_6_decimales_los_devuelve_iguales_y_compara_exacto(monkeypatch):
    pasos = [paso_costo(paso=0, usd="0.600000"), paso_costo(paso=1, usd="0.000000", motivo="local")]
    preparar(monkeypatch, JacobsFalso(_prevuelo(veredicto(costo="0.600000", pasos_costo=pasos))), umbral="0.60")
    r = _correr(mod.preflight_pipeline(pedido=mod.PedidoDePrevuelo(steps=PASOS), user=USUARIO))
    assert r["costo_max_usd"] == "0.600000"
    assert [p["usd_max"] for p in r["pasos_costo"]] == ["0.600000", "0.000000"]
    assert r["requiere_confirmacion"] is False  # 0.600000 == 0.60: no supera el umbral


def test_preflight_con_un_millonesimo_sobre_el_umbral_pide_confirmacion(monkeypatch):
    preparar(monkeypatch, JacobsFalso(_prevuelo(veredicto(costo="0.600001"))), umbral="0.60")
    r = _correr(mod.preflight_pipeline(pedido=mod.PedidoDePrevuelo(steps=PASOS), user=USUARIO))
    assert (r["costo_max_usd"], r["requiere_confirmacion"]) == ("0.600001", True)


def test_preflight_costo_cero_de_6_decimales_no_pide_confirmacion(monkeypatch):
    preparar(monkeypatch, JacobsFalso(_prevuelo(veredicto(costo="0.000000"))), umbral="0.00")
    r = _correr(mod.preflight_pipeline(pedido=mod.PedidoDePrevuelo(steps=PASOS), user=USUARIO))
    assert (r["costo_max_usd"], r["umbral_usd"], r["requiere_confirmacion"]) == ("0.000000", "0.00", False)


def test_preflight_403_invocador_no_autorizado_con_detalle_texto_es_jacobs_rechazo_con_ese_detalle(monkeypatch):
    rechazo = {"detail": {"code": "invocador_no_autorizado",
                          "detalle": "invoked_by 'plataforma' no autorizado api_key=sk-secreto123456"}}
    preparar(monkeypatch, JacobsFalso(_prevuelo(rechazo, status=403)))
    r = _correr(mod.preflight_pipeline(pedido=mod.PedidoDePrevuelo(steps=PASOS), user=USUARIO))
    assert r.status_code == 403
    assert r.detail["code"] == "jacobs_rechazo" and r.detail["status"] == 403
    assert r.detail["motivo"].startswith("invoked_by 'plataforma' no autorizado")
    assert "sk-secreto123456" not in r.detail["motivo"]
    assert set(r.detail) == {"code", "status", "motivo"}


# Ruling R54 del plan J (2026-09-17): /jacobs/preflight pasa a mirar el kill
# switch, porque su sonda es una llamada paga. La Mesa tiene que mostrar ese
# 423 con su texto, no como un rechazo genérico.
def test_el_prevuelo_propaga_el_kill_switch_con_su_codigo(monkeypatch):
    falso = JacobsFalso({("POST", "/preflight"): respuesta(
        423, {"detail": {"code": "kill_switch", "detalle": "freno puesto"}})})
    preparar(monkeypatch, falso)
    resultado = _correr(mod.preflight_pipeline(pedido=mod.PedidoDePrevuelo(steps=PASOS), user=USUARIO))
    assert isinstance(resultado, HTTPException), resultado
    assert (resultado.status_code, resultado.detail["code"]) == (423, "kill_switch")
