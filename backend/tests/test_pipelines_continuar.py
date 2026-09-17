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
    ([(2, "PIPELINE_ABORTED", "{no json"), (1, "STEP_FAILED", "[1]")], {"tipo": "fallo"}),
])
def test_la_causa_es_la_del_ultimo_evento(eventos, esperado):
    assert mod.causa_de(eventos) == esperado


# ---------------------------------------------------------------- con DB

@pytest.mark.parametrize("nombre", ["continue_preflight", "continue_pipeline"])
def test_continuar_exige_ser_el_duenio(client, nombre):
    funcion = getattr(mod, nombre)

    async def llamar():
        try:
            await funcion(pipeline_id=str(uuid.uuid4()), pedido=mod.PedidoDeContinuar(),
                          user=AuthUser(user_id="intruso", tenant_id="1", role="operator"))
        except HTTPException as exc:
            return exc.status_code
        return None

    assert client.portal.call(llamar) == 404


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
        ((_id, _sel, tabla, _tipo, _posibles, clave, _largo, _ref, _filas, extra),) = [tuple(f) for f in filas]
        assert tabla == "jacobs_events"
        assert clave == "idx_events_pipeline", filas
        assert "filesort" not in (extra or "") and "temporary" not in (extra or ""), filas
    finally:
        for pid in relleno:
            client.portal.call(sql, "DELETE FROM jacobs_events WHERE pipeline_id = %s", (pid,))
