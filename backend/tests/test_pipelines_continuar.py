"""Continuar un pipeline abortado o vencido desde la Mesa (spec 2026-09-17
§5.1, §6.1, §6.2): dueño, cupo, pre-vuelo de los pasos pendientes, confirmación
de costo, y el panel sabe por qué se detuvo (causa).

Formas de Jacobs según la ENMIENDA de contrato y el plan J (jax,
jacobs/continuar.py): `motivo` de continue/preflight es un dict
{code, **campos} o null, y `detalle` puede ser texto o lista (reasignación
inválida: {paso, motivo}; clean-room/capability: PlanViolation)."""
import asyncio
import json
import time
import typing
import uuid

import pytest
from fastapi import HTTPException

from api import pipelines as mod
from auth.models import AuthUser
from jax_engine.schemas import EventType, PipelineState
from tests.identidades import cabeceras, sql, uid
from tests.jacobs_falso import (JacobsFalso, invalida, motivo, paso_costo, preparar, previsualizacion,
                                respuesta, veredicto, violacion, violacion_de_plan)

USUARIO = AuthUser(user_id="5", tenant_id="1", role="operator")
PID = "22222222-2222-2222-2222-222222222222"
RUTA_PREVIA = f"/pipeline/{PID}/continue/preflight"
RUTA = f"/pipeline/{PID}/continue"


def _correr(corutina):
    try:
        return asyncio.run(corutina)
    except HTTPException as exc:
        return exc


def _previa(v=None, motivo_=None, **kw):
    return {("POST", RUTA_PREVIA): respuesta(200, previsualizacion(v=v, motivo_=motivo_, **kw))}


def _previa_cruda(cuerpo):
    return {("POST", RUTA_PREVIA): respuesta(200, cuerpo)}


def _preflight(pedido=None):
    return mod.continue_preflight(pipeline_id=PID, pedido=pedido or mod.PedidoDeContinuarPrevuelo(), user=USUARIO)


def _continuar(pedido=None):
    return mod.continue_pipeline(pipeline_id=PID, pedido=pedido or mod.PedidoDeContinuar(), user=USUARIO)


# ---------------------------------------------------------------- /continue/preflight

def test_continue_preflight_reenvia_identidad_y_reasignacion(monkeypatch):
    falso = JacobsFalso(_previa(v=veredicto(costo="0.30")))
    preparar(monkeypatch, falso)
    r = _correr(_preflight(mod.PedidoDeContinuarPrevuelo(reasignar={"4": "ada"})))
    assert falso.cuerpos("POST", RUTA_PREVIA) == [
        {"invoked_by": "plataforma", "user_id": "5", "tenant_id": "1", "reasignar": {"4": "ada"}}]
    assert (r["continuable"], r["motivo"], r["pasos_a_correr"], r["pasos_reusados"]) == (True, None, [4, 5], [0, 1, 2, 3])
    assert (r["veredicto"]["costo_max_usd"], r["veredicto"]["umbral_usd"], r["veredicto"]["requiere_confirmacion"]) == (
        "0.30", "0.50", False)


def test_continue_preflight_no_continuable_trae_el_motivo_dict_sin_veredicto(monkeypatch):
    m = motivo("estado_no_continuable", status="completed", mensaje="solo aborted, api_key=sk-FAKE-estado fin",
               extra="no pasa")
    preparar(monkeypatch, JacobsFalso(_previa(motivo_=m)))
    r = _correr(_preflight())
    assert r == {"continuable": False,
                 "motivo": {"code": "estado_no_continuable", "status": "completed",
                            "mensaje": "solo aborted, api_key=*** fin"},
                 "pasos_a_correr": [], "pasos_reusados": [], "veredicto": None}


@pytest.mark.parametrize("code", ["prevuelo_rechazado", "limite_de_activos"])
def test_continue_preflight_no_continuable_con_veredicto_lo_evalua(monkeypatch, code):
    v = veredicto(ok=code != "prevuelo_rechazado", costo="0.60",
                  violaciones=[violacion(detalle="sonda api_key=sk-FAKE-v fin")] if code == "prevuelo_rechazado" else ())
    preparar(monkeypatch, JacobsFalso(_previa(v=v, motivo_=motivo(code, "Ya hay 3 pipelines activos"))))
    r = _correr(_preflight())
    assert (r["continuable"], r["motivo"], r["pasos_reusados"]) == (
        False, {"code": code, "detalle": "Ya hay 3 pipelines activos"}, [0, 1, 2, 3])
    assert (r["veredicto"]["costo_max_usd"], r["veredicto"]["requiere_confirmacion"]) == ("0.60", True)
    if code == "prevuelo_rechazado":
        assert r["veredicto"]["violaciones"][0]["detalle"] == "sonda api_key=*** fin"


def test_continue_preflight_reasignacion_invalida_con_lista_paso_motivo(monkeypatch):
    detalle = [invalida(paso="x", motivo="el índice de paso no es un entero"),
               {**invalida(paso=2, motivo="faceta desconocida: 'api_key=sk-FAKE-faceta'"), "otro": "no pasa"},
               "no es un objeto"]
    preparar(monkeypatch, JacobsFalso(_previa(motivo_=motivo("reasignacion_invalida", detalle))))
    r = _correr(_preflight(mod.PedidoDeContinuarPrevuelo(reasignar={"x": "ada", "2": "api_key=sk-FAKE-faceta"})))
    assert (r["continuable"], r["veredicto"]) == (False, None)
    assert r["motivo"] == {"code": "reasignacion_invalida", "detalle": [
        {"paso": "x", "faceta": None, "motivo": "el índice de paso no es un entero"},
        {"paso": 2, "faceta": None, "motivo": "faceta desconocida: 'api_key=***'"},
    ]}


@pytest.mark.parametrize("code", ["reasignacion_invalida", "plan_rechazado"])
def test_continue_preflight_violaciones_de_plan_se_normalizan(monkeypatch, code):
    detalle = [violacion_de_plan(step_index=4, facet="ada", reason="clean-room api_key=sk-FAKE-plan fin")]
    preparar(monkeypatch, JacobsFalso(_previa(motivo_=motivo(code, detalle))))
    r = _correr(_preflight())
    assert r["motivo"] == {"code": code, "detalle": [{"paso": 4, "faceta": "ada", "motivo": "clean-room api_key=*** fin"}]}


@pytest.mark.parametrize("cuerpo", [
    {**previsualizacion(), "motivo": "status completed", "continuable": False},     # motivo texto (forma vieja)
    {**previsualizacion(), "motivo": {"detalle": "sin code"}, "continuable": False},
    {**previsualizacion(), "continuable": False, "motivo": None},                    # no continuable sin motivo
    {**previsualizacion(v=veredicto()), "motivo": motivo("kill_switch", "x")},       # continuable con motivo
    previsualizacion(v=None),                                                        # continuable sin veredicto
    {**previsualizacion(v=veredicto()), "continuable": "true"},
    {**previsualizacion(v=veredicto()), "pasos_a_correr": "4,5"},
    {**previsualizacion(v=veredicto()), "pasos_reusados": [0, "1"]},
    {**previsualizacion(v=veredicto()), "veredicto": {"ok": True}},                  # veredicto mal formado
])
def test_continue_preflight_forma_invalida_es_fail_closed(monkeypatch, cuerpo):
    preparar(monkeypatch, JacobsFalso(_previa_cruda(cuerpo)))
    r = _correr(_preflight())
    assert (r.status_code, r.detail) == (502, {"code": "prevuelo_no_disponible"})


def test_continue_preflight_404_de_jacobs_pasa_con_su_status(monkeypatch):
    cuerpo = {"detail": motivo("no_existe", "Pipeline 'x' no encontrado")}
    preparar(monkeypatch, JacobsFalso({("POST", RUTA_PREVIA): respuesta(404, cuerpo)}))
    r = _correr(_preflight())
    assert (r.status_code, r.detail) == (404, {"code": "no_existe", "detalle": "Pipeline 'x' no encontrado"})


def test_continue_preflight_503_de_jacobs_pasa_con_el_motivo_redactado(monkeypatch):
    cuerpo = {"detail": {"code": "prevuelo_no_disponible", "motivo": "RuntimeError: api_key=sk-FAKE-503 fin"}}
    preparar(monkeypatch, JacobsFalso({("POST", RUTA_PREVIA): respuesta(503, cuerpo)}))
    r = _correr(_preflight())
    assert (r.status_code, r.detail) == (
        503, {"code": "prevuelo_no_disponible", "motivo": "RuntimeError: api_key=*** fin"})


# ---------------------------------------------------------------- detalle en los rechazos

def test_rechazo_con_detalle_lista_de_reasignacion_se_normaliza():
    cuerpo = {"detail": motivo("reasignacion_invalida", [invalida(paso=1, motivo="api_key=sk-FAKE-r fin"), 7])}
    exc = mod._rechazo_de_jacobs(422, cuerpo, "")
    assert (exc.status_code, exc.detail) == (422, {"code": "reasignacion_invalida", "detalle": [
        {"paso": 1, "faceta": None, "motivo": "api_key=*** fin"}]})


def test_rechazo_con_detalle_lista_de_violaciones_de_plan_se_normaliza():
    cuerpo = {"detail": motivo("plan_rechazado", [violacion_de_plan(step_index=True, facet=3, reason=None)])}
    exc = mod._rechazo_de_jacobs(422, cuerpo, "")
    assert exc.detail == {"code": "plan_rechazado", "detalle": [{"paso": None, "faceta": None, "motivo": ""}]}


@pytest.mark.parametrize("detalle", [{"anidado": "no"}, 12, None])
def test_rechazo_con_detalle_de_otro_tipo_lo_descarta(detalle):
    cuerpo = {"detail": {"code": "plan_inconsistente", "detalle": detalle}}
    assert mod._rechazo_de_jacobs(409, cuerpo, "").detail == {"code": "plan_inconsistente"}


def test_rechazo_de_un_codigo_ajeno_con_detalle_lista_usa_el_code_no_el_repr():
    cuerpo = {"detail": motivo("invocador_no_autorizado", [violacion_de_plan()])}
    exc = mod._rechazo_de_jacobs(403, cuerpo, "")
    assert exc.detail == {"code": "jacobs_rechazo", "status": 403, "motivo": "invocador_no_autorizado"}


# ---------------------------------------------------------------- /continue

def test_continuar_un_estado_no_continuable_es_409_con_sus_campos_y_no_continua(monkeypatch):
    falso = JacobsFalso(_previa(motivo_=motivo("estado_no_continuable", status="completed", mensaje="solo aborted")))
    preparar(monkeypatch, falso)
    r = _correr(_continuar())
    assert (r.status_code, r.detail) == (
        409, {"code": "estado_no_continuable", "status": "completed", "mensaje": "solo aborted"})
    assert falso.cuerpos("POST", RUTA) == []


def test_continuar_con_prevuelo_rechazado_es_422_del_veredicto(monkeypatch):
    v = veredicto(ok=False, costo="0.20", violaciones=[violacion()])
    falso = JacobsFalso(_previa(v=v, motivo_=motivo("prevuelo_rechazado", "ver veredicto")))
    preparar(monkeypatch, falso)
    r = _correr(_continuar())
    assert (r.status_code, r.detail) == (422, {
        "code": "prevuelo_rechazado", "violaciones": [violacion()], "costo_max_usd": "0.20",
        "pasos_costo": [paso_costo(usd="0.20")]})
    assert falso.cuerpos("POST", RUTA) == []


def test_continuar_prevuelo_rechazado_sin_veredicto_es_502(monkeypatch):
    cuerpo = {**previsualizacion(), "continuable": False, "motivo": motivo("prevuelo_rechazado", "x")}
    falso = JacobsFalso(_previa_cruda(cuerpo))
    preparar(monkeypatch, falso)
    r = _correr(_continuar())
    assert (r.status_code, r.detail) == (502, {"code": "prevuelo_no_disponible"})


def test_continuar_con_el_limite_de_activos_de_jacobs_es_429(monkeypatch):
    falso = JacobsFalso(_previa(v=veredicto(), motivo_=motivo("limite_de_activos", "Ya hay 3 pipelines activos")))
    registro = preparar(monkeypatch, falso)
    r = _correr(_continuar())
    assert (r.status_code, r.detail) == (429, {"code": "limite_de_activos", "detalle": "Ya hay 3 pipelines activos"})
    assert falso.cuerpos("POST", RUTA) == [] and registro.admitidos == []


@pytest.mark.parametrize("code, detalle, esperado", [
    ("reasignacion_invalida", [invalida(paso=0)],
     [{"paso": 0, "faceta": None, "motivo": "solo se reasignan pasos a correr, no los reusados"}]),
    ("plan_rechazado", [violacion_de_plan()], [{"paso": 4, "faceta": "ada", "motivo": "clean-room: ada no puede web_search"}]),
])
def test_continuar_con_plan_o_reasignacion_invalida_es_422(monkeypatch, code, detalle, esperado):
    falso = JacobsFalso(_previa(motivo_=motivo(code, detalle)))
    preparar(monkeypatch, falso)
    r = _correr(_continuar())
    assert (r.status_code, r.detail) == (422, {"code": code, "detalle": esperado})
    assert falso.cuerpos("POST", RUTA) == []


@pytest.mark.parametrize("m, motivo_esperado", [
    (motivo("kill_switch", "Kill switch activo — api_key=sk-FAKE-ks"), "Kill switch activo — api_key=***"),
    (motivo("kill_switch", mensaje="corte"), "corte"),
    (motivo("kill_switch"), "kill_switch"),
])
def test_continuar_con_kill_switch_es_423_jacobs_rechazo(monkeypatch, m, motivo_esperado):
    falso = JacobsFalso(_previa(motivo_=m))
    preparar(monkeypatch, falso)
    r = _correr(_continuar())
    assert (r.status_code, r.detail) == (423, {"code": "jacobs_rechazo", "status": 423, "motivo": motivo_esperado})
    assert falso.cuerpos("POST", RUTA) == []


def test_continuar_con_otro_codigo_conocido_es_409_con_sus_campos(monkeypatch):
    falso = JacobsFalso(_previa(motivo_=motivo("plan_inconsistente", "los pasos no son 0..N-1", extra="no pasa")))
    preparar(monkeypatch, falso)
    r = _correr(_continuar())
    assert (r.status_code, r.detail) == (409, {"code": "plan_inconsistente", "detalle": "los pasos no son 0..N-1"})
    assert falso.cuerpos("POST", RUTA) == []


def test_continuar_con_un_codigo_desconocido_es_409_estado_no_continuable(monkeypatch):
    falso = JacobsFalso(_previa(motivo_=motivo("invento_nuevo", "algo api_key=sk-FAKE-nuevo")))
    preparar(monkeypatch, falso)
    r = _correr(_continuar())
    assert (r.status_code, r.detail) == (409, {"code": "estado_no_continuable", "mensaje": "algo api_key=***"})
    assert falso.cuerpos("POST", RUTA) == []


def test_continuar_caro_sin_confirmar_es_409(monkeypatch):
    falso = JacobsFalso(_previa(v=veredicto(costo="0.60")))
    preparar(monkeypatch, falso)
    r = _correr(_continuar())
    assert (r.status_code, r.detail["code"]) == (409, "confirmacion_de_costo")
    assert falso.cuerpos("POST", RUTA) == []


@pytest.mark.parametrize("crudo", ["1e3", "-0", " 0.6 ", "1_000", True, "abc"])
def test_continuar_con_costo_confirmado_invalido_es_422_sin_llamar_a_jacobs(monkeypatch, crudo):
    falso = JacobsFalso()
    preparar(monkeypatch, falso)
    r = _correr(_continuar(mod.PedidoDeContinuar(costo_confirmado_usd=crudo)))
    assert (r.status_code, r.detail) == (422, {"code": "costo_confirmado_invalido"})
    assert falso.llamadas == []


def test_continuar_confirmado_continua_admite_y_publica(monkeypatch):
    ok = {"pipeline_id": PID, "status": "running", "run_epoch": 2, "pasos_a_correr": [4, 5],
          "pasos_reusados": [0, 1, 2, 3], "costo_max_usd": "0.60", "pasos_costo": []}
    falso = JacobsFalso({**_previa(v=veredicto(costo="0.60")), ("POST", RUTA): respuesta(200, ok)})
    registro = preparar(monkeypatch, falso)
    pedido = mod.PedidoDeContinuar(reasignar={"4": "ada"}, costo_confirmado_usd="0.60")
    r = _correr(_continuar(pedido))
    assert r == ok
    assert falso.cuerpos("POST", RUTA) == [{"invoked_by": "plataforma", "user_id": "5", "tenant_id": "1",
                                            "reasignar": {"4": "ada"}, "costo_max_aceptado_usd": "0.60"}]
    assert registro.admitidos == [("1", PID)]
    assert registro.publicados == [("pipeline_continued", PID, {"run_epoch": 2, "pasos_reusados": [0, 1, 2, 3]})]


def test_continuar_sin_confirmar_manda_el_umbral_y_sanea_el_200(monkeypatch):
    ok = {"pipeline_id": PID, "status": "running", "run_epoch": 3, "pasos_a_correr": [5], "pasos_reusados": [0],
          "costo_max_usd": "1E-1", "pasos_costo": [{**paso_costo(usd="1E-1", motivo="api_key=sk-FAKE-200"), "x": 1}]}
    falso = JacobsFalso({**_previa(v=veredicto(costo="0.10")), ("POST", RUTA): respuesta(200, ok)})
    preparar(monkeypatch, falso, umbral="0.5")
    r = _correr(_continuar())
    assert falso.cuerpos("POST", RUTA)[0]["costo_max_aceptado_usd"] == "0.5"
    assert (r["costo_max_usd"], r["pasos_costo"]) == ("0.1", [paso_costo(usd="0.1", motivo="api_key=***")])


def test_continuar_rechazado_por_jacobs_sale_con_su_status_y_no_admite(monkeypatch):
    falso = JacobsFalso({**_previa(v=veredicto()), ("POST", RUTA): respuesta(423, {"detail": "kill switch activo"})})
    registro = preparar(monkeypatch, falso)
    r = _correr(_continuar())
    assert (r.status_code, r.detail) == (423, {"code": "jacobs_rechazo", "status": 423, "motivo": "kill switch activo"})
    assert registro.admitidos == [] and registro.publicados == []


def test_continuar_carrera_reasignacion_invalida_en_continue_normaliza_el_detalle(monkeypatch):
    rechazo = {"detail": motivo("reasignacion_invalida", [violacion_de_plan(reason="api_key=sk-FAKE-carrera")])}
    falso = JacobsFalso({**_previa(v=veredicto()), ("POST", RUTA): respuesta(422, rechazo)})
    registro = preparar(monkeypatch, falso)
    r = _correr(_continuar())
    assert (r.status_code, r.detail) == (422, {"code": "reasignacion_invalida", "detalle": [
        {"paso": 4, "faceta": "ada", "motivo": "api_key=***"}]})
    assert registro.admitidos == [] and registro.publicados == []


def test_continuar_con_el_cupo_lleno_es_429_sin_llamar_a_jacobs(monkeypatch):
    falso = JacobsFalso()
    preparar(monkeypatch, falso, cupo_libre=False, maximo=2)
    r = _correr(_continuar())
    assert (r.status_code, r.detail) == (429, {"code": "limite_de_pipelines", "max": 2})
    assert falso.llamadas == []


def test_pipeline_continued_es_un_evento_del_ws():
    assert "pipeline_continued" in typing.get_args(EventType)


def test_continuar_pipeline_publica_el_estado_con_la_continuacion(monkeypatch):
    from jax_engine import state as estado_mod
    publicados = []

    async def publicar(evento):
        publicados.append(evento)

    monkeypatch.setattr(estado_mod.event_bus, "publish", publicar)
    pid = str(uuid.uuid4())
    pipeline = PipelineState(pipeline_id=pid, tenant_id="1", user_id="5", name="n", status="running")
    try:
        asyncio.run(estado_mod.engine_state.continuar_pipeline(pipeline, "1", "5", {"run_epoch": 2, "pasos_reusados": [0]}))
        assert estado_mod.engine_state._state.active_pipelines[pid] is pipeline
        ((evento,),) = [(e,) for e in publicados]
        assert (evento.event_type, evento.tenant_id, evento.user_id) == ("pipeline_continued", "1", "5")
        assert evento.payload == {**pipeline.model_dump(), "run_epoch": 2, "pasos_reusados": [0]}
    finally:
        estado_mod.engine_state.remove_pipeline(pid)


@pytest.mark.parametrize("eventos, esperado", [
    ([(1, "STEP_FAILED", json.dumps({"step_index": 4, "error": "api_key=sk-FAKE-causa cortado"})),
      (2, "PIPELINE_ABORTED", json.dumps({"at_wave": 3}))],
     {"tipo": "fallo", "paso": 4, "detalle": "api_key=*** cortado"}),
    ([(5, "PIPELINE_CANCELLED", json.dumps({"by": "API request"}))], {"tipo": "cancelado"}),
    ([(1, "STEP_FAILED", json.dumps({"step_index": 0, "error": "x"})), (9, "KILL_SWITCH_ABORTED", "{}")],
     {"tipo": "kill_switch"}),
    ([(3, "REAPED", json.dumps({"prev_status": "running", "reason": "sin avance"}))], {"tipo": "expirado"}),
    ([], {"tipo": "desconocida"}),
    # Fix round 1 ítem 1: un STEP_FAILED de un paso skip_on_fail DESPUÉS del
    # real; PIPELINE_ABORTED nombra el que abortó (failed_steps, errores).
    ([(1, "STEP_FAILED", json.dumps({"step_index": 3, "error": "api_key=sk-FAKE-real cortado"})),
      (2, "STEP_FAILED", json.dumps({"step_index": 5, "error": "opcional"})),
      (3, "PIPELINE_ABORTED", json.dumps({"at_wave": 2, "failed_steps": [4, 3],
                                          "errores": {"3": "api_key=sk-FAKE-real cortado", "4": "otro"}}))],
     {"tipo": "fallo", "paso": 3, "detalle": "api_key=*** cortado"}),
    ([(3, "PIPELINE_ABORTED", json.dumps({"failed_steps": [2], "errores": {"2": None}}))],
     {"tipo": "fallo", "paso": 2}),
    ([(3, "PIPELINE_ABORTED", json.dumps({"failed_steps": [2]})),
      (1, "STEP_FAILED", json.dumps({"step_index": 5, "error": "de otro paso"}))],
     {"tipo": "fallo", "paso": 2}),
    ([(1, "STEP_FAILED", json.dumps({"step_index": 3, "error": "real"})),
      (2, "STEP_FAILED", json.dumps({"step_index": 5, "error": "opcional"})),
      (3, "PIPELINE_ABORTED", json.dumps({"failed_steps": "3", "errores": {}}))],
     {"tipo": "fallo", "paso": 5, "detalle": "opcional"}),
    ([(2, "STEP_FAILED", json.dumps({"step_index": 5, "error": "opcional"})),
      (3, "PIPELINE_ABORTED", json.dumps({"failed_steps": [True, "1"]}))],
     {"tipo": "fallo", "paso": 5, "detalle": "opcional"}),
    ([(2, "STEP_FAILED", json.dumps({"step_index": 5, "error": "opcional"})),
      (3, "PIPELINE_ABORTED", json.dumps({"failed_steps": []}))],
     {"tipo": "fallo", "paso": 5, "detalle": "opcional"}),
    ([(2, "PIPELINE_ABORTED", "{no json"), (1, "STEP_FAILED", "[1]")], {"tipo": "fallo"}),
])
def test_la_causa_es_la_del_ultimo_evento(eventos, esperado):
    assert mod.causa_de(eventos) == esperado


# Revisión final, menor 7b: sólo un error de texto es detalle; nunca str() de
# un objeto (un dict o una lista saldría como su repr de Python).
@pytest.mark.parametrize("error", [{"anidado": "api_key=sk-x"}, ["a", "b"], 42, True])
def test_la_causa_con_error_no_texto_sale_sin_detalle(error):
    eventos = [(1, "STEP_FAILED", json.dumps({"step_index": 2, "error": error}))]
    assert mod.causa_de(eventos) == {"tipo": "fallo", "paso": 2}


# Revisión final, menor 7a: reasignar con tope (20 pasos, el límite duro de
# Jacobs) y claves/valores de texto acotados; si no, 422 propio sin llamar.
@pytest.mark.parametrize("reasignar", [
    {str(i): "ada" for i in range(21)},
    {"4": "x" * 51},
    {"x" * 51: "ada"},
    {"4": 7},
    ["4", "ada"],
    "4=ada",
])
@pytest.mark.parametrize("modelo, llamar", [
    (mod.PedidoDeContinuarPrevuelo, _preflight), (mod.PedidoDeContinuar, _continuar)])
def test_reasignar_fuera_de_forma_es_422_sin_llamar_a_jacobs(monkeypatch, reasignar, modelo, llamar):
    falso = JacobsFalso()
    preparar(monkeypatch, falso)
    r = _correr(llamar(modelo(reasignar=reasignar)))
    assert (r.status_code, r.detail) == (422, {"code": "reasignacion_fuera_de_forma",
                                               "max_pasos": mod.REASIGNAR_MAX_PASOS, "max_largo": mod.FACETA_MAX})
    assert falso.llamadas == []


def test_reasignar_en_el_tope_pasa_a_jacobs(monkeypatch):
    reasignar = {str(i): "a" * mod.FACETA_MAX for i in range(mod.REASIGNAR_MAX_PASOS)}
    falso = JacobsFalso(_previa(v=veredicto(costo="0.30")))
    preparar(monkeypatch, falso)
    _correr(_preflight(mod.PedidoDeContinuarPrevuelo(reasignar=reasignar)))
    assert falso.cuerpos("POST", RUTA_PREVIA)[0]["reasignar"] == reasignar


def test_reasignar_por_http_con_valor_no_texto_es_el_codigo_propio(client):
    r = client.post(f"/api/pipelines/{PID}/continue/preflight", json={"reasignar": {"4": 7}},
                    headers=cabeceras(client, "continuar-reasignar-forma"))
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["code"] == "reasignacion_fuera_de_forma"


# ---------------------------------------------------------------- fix round 1

def test_codigo_desconocido_sale_igual_en_preflight_y_en_continue(monkeypatch):
    """Ítem 2: allowlist = CODIGOS_DE_JACOBS ∪ {kill_switch}; la misma
    respuesta de Jacobs da el mismo motivo en los dos endpoints."""
    m = motivo("invento_nuevo", "algo api_key=sk-FAKE-nuevo", status="x")
    esperado = {"code": "estado_no_continuable", "mensaje": "algo api_key=***"}
    preparar(monkeypatch, JacobsFalso(_previa(motivo_=m)))
    r = _correr(_preflight())
    assert (r["continuable"], r["motivo"], r["veredicto"]) == (False, esperado, None)
    falso = JacobsFalso(_previa(motivo_=m))
    preparar(monkeypatch, falso)
    r = _correr(_continuar())
    assert (r.status_code, r.detail) == (409, esperado)
    assert falso.cuerpos("POST", RUTA) == []


@pytest.mark.parametrize("m, mensaje", [
    (motivo("otro", mensaje="api_key=sk-FAKE-m"), "api_key=***"),
    (motivo("otro"), "otro"),
    (motivo("otro", [invalida()]), "otro"),
])
def test_codigo_desconocido_en_preflight_usa_el_mejor_texto(monkeypatch, m, mensaje):
    preparar(monkeypatch, JacobsFalso(_previa(motivo_=m)))
    assert _correr(_preflight())["motivo"] == {"code": "estado_no_continuable", "mensaje": mensaje}


def test_code_sin_ningun_texto_omite_el_mensaje_en_los_dos_endpoints(monkeypatch):
    """Fix round 2 ítem 2: sin texto no vacío, estado_no_continuable sale sin
    mensaje (el frontend muestra el texto genérico)."""
    m = motivo("", "", mensaje="")
    preparar(monkeypatch, JacobsFalso(_previa(motivo_=m)))
    assert _correr(_preflight())["motivo"] == {"code": "estado_no_continuable"}
    preparar(monkeypatch, JacobsFalso(_previa(motivo_=m)))
    r = _correr(_continuar())
    assert (r.status_code, r.detail) == (409, {"code": "estado_no_continuable"})


def test_kill_switch_pasa_en_preflight_con_su_code(monkeypatch):
    preparar(monkeypatch, JacobsFalso(_previa(motivo_=motivo("kill_switch", "Kill switch activo"))))
    assert _correr(_preflight())["motivo"] == {"code": "kill_switch", "detalle": "Kill switch activo"}


@pytest.mark.parametrize("campos, esperado", [
    ({"status": 3, "mensaje": ["x"], "motivo": {"a": 1}, "ok": "true", "hay_no_acotados": 1}, {}),
    ({"status": None, "mensaje": None, "motivo": None}, {"status": None, "mensaje": None, "motivo": None}),
    ({"status": "aborted", "mensaje": "m", "motivo": "r", "ok": False, "hay_no_acotados": True},
     {"status": "aborted", "mensaje": "m", "motivo": "r", "ok": False, "hay_no_acotados": True}),
])
def test_detalle_declarado_exige_el_tipo_de_cada_campo(campos, esperado):
    """Ítem 3: status/mensaje/motivo str o null; ok/hay_no_acotados bool; si
    no, se omiten (nunca el repr)."""
    exc = mod._rechazo_de_jacobs(409, {"detail": {"code": "estado_no_continuable", **campos}}, "")
    assert exc.detail == {"code": "estado_no_continuable", **esperado}


@pytest.mark.parametrize("cambio, fuera", [
    ({}, None),
    ({"run_epoch": True}, "run_epoch"),
    ({"run_epoch": "2"}, "run_epoch"),
    ({"pasos_reusados": [0, "1"]}, "pasos_reusados"),
    ({"pasos_a_correr": "4,5"}, "pasos_a_correr"),
    ({"pipeline_id": 22}, "pipeline_id"),
    ({"status": ["running"]}, "status"),
])
def test_el_200_de_continuar_solo_trae_claves_declaradas_y_validas(monkeypatch, cambio, fuera):
    """Ítem 4: Jacobs ya lanzó el pipeline -- la respuesta no se rompe, pero
    un campo mal formado no sale ni llega al evento de WS."""
    ok = {"pipeline_id": PID, "status": "running", "run_epoch": 2, "pasos_a_correr": [4, 5],
          "pasos_reusados": [0, 1, 2, 3], "costo_max_usd": "0.10", "pasos_costo": [], "secreto": "api_key=sk-FAKE-x",
          **cambio}
    falso = JacobsFalso({**_previa(v=veredicto()), ("POST", RUTA): respuesta(200, ok)})
    registro = preparar(monkeypatch, falso)
    r = _correr(_continuar())
    declaradas = {"pipeline_id", "status", "run_epoch", "pasos_a_correr", "pasos_reusados", "costo_max_usd", "pasos_costo"}
    assert set(r) == declaradas - {fuera}
    assert registro.admitidos == [("1", PID)]
    ((_evento, _pid, continuacion),) = registro.publicados
    esperado_ws = {"run_epoch": 2, "pasos_reusados": [0, 1, 2, 3]}
    esperado_ws.pop(fuera, None)
    assert continuacion == esperado_ws


# ---------------------------------------------------------------- con DB

@pytest.fixture
def abortado_con_eventos(client):
    duenio = uid(client, "continuar-causa", "operator")
    ahora = time.time()
    abortado, corriendo = str(uuid.uuid4()), str(uuid.uuid4())
    for pid, estado in ((abortado, "aborted"), (corriendo, "running")):
        client.portal.call(
            sql,
            "INSERT INTO jacobs_pipelines (pipeline_id, name, invoked_by, mode, status, created_at, updated_at, "
            "user_id, tenant_id, owner_ack_at) VALUES (%s, %s, 'plataforma', 'supervised', %s, %s, %s, %s, 'TENANT-CONT', %s)",
            (pid, f"causa {estado}", estado, ahora, ahora, duenio, ahora))
    client.portal.call(sql, "INSERT INTO jacobs_events (pipeline_id, step_id, event_type, payload, ts) "
                            "VALUES (%s, NULL, 'STEP_FAILED', %s, %s)",
                       (abortado, json.dumps({"step_index": 4, "error": "Salida cortada por max_tokens"}), ahora))
    client.portal.call(sql, "INSERT INTO jacobs_events (pipeline_id, step_id, event_type, payload, ts) "
                            "VALUES (%s, NULL, 'PIPELINE_ABORTED', %s, %s)",
                       (abortado, json.dumps({"at_wave": 3}), ahora + 1))
    yield abortado, corriendo
    for pid in (abortado, corriendo):
        client.portal.call(sql, "DELETE FROM jacobs_events WHERE pipeline_id = %s", (pid,))
        client.portal.call(sql, "DELETE FROM jacobs_pipelines WHERE pipeline_id = %s", (pid,))


@pytest.mark.parametrize("nombre", ["continue_preflight", "continue_pipeline"])
@pytest.mark.parametrize("intruso", [AuthUser(user_id="intruso", tenant_id="TENANT-CONT", role="operator"),
                                     "mismo usuario, otro tenant"])
def test_continuar_exige_ser_el_duenio(client, monkeypatch, abortado_con_eventos, nombre, intruso):
    """Un pipeline QUE EXISTE y es de otro: 404, sin llamar a Jacobs y sin
    consultar el cupo (el dueño va primero)."""
    abortado, _ = abortado_con_eventos
    if isinstance(intruso, str):
        intruso = AuthUser(user_id=uid(client, "continuar-causa", "operator"), tenant_id="OTRO", role="operator")
    duenio_real = mod._require_pipeline_owner
    falso = JacobsFalso()
    preparar(monkeypatch, falso)
    monkeypatch.setattr(mod, "_require_pipeline_owner", duenio_real)
    cupos = []

    async def cupo(tenant, limite):
        cupos.append(tenant)
        return True

    monkeypatch.setattr(mod.resource_manager, "can_start_pipeline", cupo)
    funcion = getattr(mod, nombre)

    async def llamar():
        try:
            await funcion(pipeline_id=abortado, pedido=mod.PedidoDeContinuar(), user=intruso)
        except HTTPException as exc:
            return exc.status_code, exc.detail
        return None

    assert client.portal.call(llamar) == (404, "pipeline_no_encontrado")
    assert falso.llamadas == [] and cupos == []


def test_el_duenio_real_devuelve_el_nombre(client, abortado_con_eventos):
    abortado, _ = abortado_con_eventos
    duenio = AuthUser(user_id=uid(client, "continuar-causa", "operator"), tenant_id="TENANT-CONT", role="operator")
    assert client.portal.call(mod._require_pipeline_owner, abortado, duenio) == "causa aborted"


def test_la_lista_trae_la_causa_de_los_abortados(client, abortado_con_eventos):
    abortado, corriendo = abortado_con_eventos
    r = client.get("/api/pipelines", headers=cabeceras(client, "continuar-causa", "operator", tenant_id="TENANT-CONT"))
    assert r.status_code == 200, r.text
    por_id = {p["pipeline_id"]: p for p in r.json()["pipelines"]}
    assert por_id[abortado]["causa"] == {"tipo": "fallo", "paso": 4, "detalle": "Salida cortada por max_tokens"}
    assert por_id[corriendo]["causa"] is None


def test_la_consulta_de_causa_usa_el_indice_de_eventos(client, abortado_con_eventos):
    relleno = [str(uuid.uuid4()) for _ in range(60)]
    for pid in relleno:
        client.portal.call(sql, "INSERT INTO jacobs_events (pipeline_id, step_id, event_type, payload, ts) "
                                "VALUES (%s, NULL, 'STEP_STARTED', '{}', 1)", (pid,))
    try:
        client.portal.call(sql, "ANALYZE TABLE jacobs_events", (), True)
        filas = client.portal.call(sql, "EXPLAIN " + mod.sql_eventos_de_causa(2),
                                   (*abortado_con_eventos, *mod.EVENTOS_DE_CAUSA), True)
        _exigir_plan_por_indice_de_eventos(filas)
    finally:
        for pid in relleno:
            client.portal.call(sql, "DELETE FROM jacobs_events WHERE pipeline_id = %s", (pid,))


# Revisión final, importante 3: el plan J (R20) agrega idx_events_pipeline_tipo
# ON jacobs_events (pipeline_id, event_type) y conserva idx_events_pipeline.
# Cuando exista, el optimizador puede elegirlo: los dos nombres son válidos,
# lo que no se acepta es un plan sin índice o con filesort/temporary.
INDICES_DE_EVENTOS = ("idx_events_pipeline_tipo", "idx_events_pipeline")


def _exigir_plan_por_indice_de_eventos(filas):
    ((_id, _sel, tabla, _tipo, _posibles, clave, _largo, _ref, _filas, extra),) = [tuple(f) for f in filas]
    assert tabla == "jacobs_events"
    assert clave in INDICES_DE_EVENTOS, filas
    assert "filesort" not in (extra or "") and "temporary" not in (extra or ""), filas
    return clave


async def _explain_con_el_indice_compuesto(ids):
    """EXPLAIN de la consulta real contra una TABLA TEMPORARIA con el esquema
    de jacobs_events más el índice compuesto del plan J. La temporaria tapa a
    la real sólo en ESTA conexión: la base compartida jax_memory_test no se
    toca (otras instancias la usan)."""
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            # MariaDB no deja `CREATE TEMPORARY TABLE t LIKE t` con el mismo
            # nombre (1066): se copia la definición real de SHOW CREATE TABLE.
            await cur.execute("SHOW CREATE TABLE jacobs_events")
            ((_tabla, definicion),) = await cur.fetchall()
            await cur.execute(definicion.replace("CREATE TABLE", "CREATE TEMPORARY TABLE", 1))
            try:
                # Cuando el esquema de Jacobs ya trae el índice (jax con el plan J
                # mergeado, o una base donde corrió su init_tables), la copia de
                # SHOW CREATE TABLE ya lo tiene: agregarlo de nuevo es 1061.
                if "idx_events_pipeline_tipo" not in definicion:
                    await cur.execute("ALTER TABLE jacobs_events ADD INDEX idx_events_pipeline_tipo (pipeline_id, event_type)")
                # La forma del peor caso medido (Task 12): muchos eventos que
                # NO son de causa por pipeline; con ellos el compuesto es el
                # más selectivo y el optimizador lo elige.
                relleno = [(str(uuid.uuid4()), "STEP_STARTED") for _ in range(80)]
                ruido = [(pid, tipo) for pid in ids for _ in range(20) for tipo in ("STEP_STARTED", "STEP_DONE")]
                objetivo = [(pid, tipo) for pid in ids for tipo in ("STEP_FAILED", "PIPELINE_ABORTED")]
                await cur.executemany(
                    "INSERT INTO jacobs_events (pipeline_id, step_id, event_type, payload, ts) VALUES (%s, NULL, %s, '{}', 1)",
                    relleno + ruido + objetivo)
                await cur.execute("ANALYZE TABLE jacobs_events")
                await cur.fetchall()
                await cur.execute("EXPLAIN " + mod.sql_eventos_de_causa(len(ids)), (*ids, *mod.EVENTOS_DE_CAUSA))
                return await cur.fetchall()
            finally:
                await cur.execute("DROP TEMPORARY TABLE IF EXISTS jacobs_events")


def test_la_consulta_de_causa_acepta_el_indice_compuesto_del_plan_j(client):
    ids = [str(uuid.uuid4()), str(uuid.uuid4())]
    filas = client.portal.call(_explain_con_el_indice_compuesto, ids)
    # Con el índice del plan J presente, ES el que se usa: el test viejo
    # (clave == "idx_events_pipeline") se rompía acá.
    assert _exigir_plan_por_indice_de_eventos(filas) == "idx_events_pipeline_tipo", filas
