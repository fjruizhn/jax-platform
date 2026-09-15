"""Task 6 S2 (2026-09-15): GET /api/state le mostraba a cualquier usuario
logueado los pipelines activos de TODOS los usuarios y tenants (nombre,
pasos, user_id, tenant_id), y la lista de sesiones conectadas.

La regla es la MISMA de los 4 endpoints por id de api/pipelines.py
(_require_pipeline_owner): el pipeline es tuyo si user_id Y tenant_id
coinciden con los del token. Esa regla no tiene excepcion para superadmin,
asi que aca tampoco.

Los tests puros llaman al handler directo (sin DB); el de HTTP pasa por la
autenticacion real y necesita jax_memory_test.
"""
import asyncio

import pytest

from api.state import get_ecosystem_state
from auth.models import AuthUser
from jax_engine.schemas import PipelineState, UserSession
from jax_engine.state import engine_state
from tests.identidades import cabeceras, uid

PID_A = "11111111-aaaa-4aaa-8aaa-000000000001"
PID_B = "22222222-bbbb-4bbb-8bbb-000000000002"


@pytest.fixture
def dos_pipelines(monkeypatch):
    """Un pipeline de (USUARIO-A, TENANT-A) y otro de (USUARIO-B, TENANT-B),
    mas las dos sesiones conectadas. Restaura el estado global al salir."""
    estado = engine_state.get_state()
    monkeypatch.setattr(estado, "active_pipelines", {
        PID_A: PipelineState(pipeline_id=PID_A, tenant_id="TENANT-A", user_id="USUARIO-A",
                             name="secreto de A", status="running"),
        PID_B: PipelineState(pipeline_id=PID_B, tenant_id="TENANT-B", user_id="USUARIO-B",
                             name="de B", status="running"),
    })
    monkeypatch.setattr(estado, "connected_users", {
        "USUARIO-A": UserSession(user_id="USUARIO-A", tenant_id="TENANT-A", role="operator"),
        "USUARIO-B": UserSession(user_id="USUARIO-B", tenant_id="TENANT-B", role="viewer"),
    })
    return estado


def _estado_para(user_id, tenant_id, role="viewer"):
    return asyncio.run(get_ecosystem_state(
        user=AuthUser(user_id=user_id, tenant_id=tenant_id, role=role)))


def test_un_viewer_de_otro_tenant_no_ve_el_pipeline_ajeno(dos_pipelines):
    body = _estado_para("USUARIO-B", "TENANT-B")
    assert list(body["active_pipelines"]) == [PID_B]
    assert "secreto de A" not in repr(body)
    assert "TENANT-A" not in repr(body)


def test_el_duenio_ve_su_pipeline(dos_pipelines):
    body = _estado_para("USUARIO-A", "TENANT-A", "operator")
    assert list(body["active_pipelines"]) == [PID_A]
    assert body["active_pipelines"][PID_A]["name"] == "secreto de A"


def test_mismo_user_id_en_otro_tenant_no_es_el_duenio(dos_pipelines):
    """La regla exige los DOS: user_id y tenant_id."""
    assert _estado_para("USUARIO-A", "TENANT-B")["active_pipelines"] == {}


def test_otro_usuario_del_mismo_tenant_no_lo_ve(dos_pipelines):
    assert _estado_para("USUARIO-C", "TENANT-A")["active_pipelines"] == {}


def test_superadmin_sigue_la_misma_regla_que_los_endpoints_por_id(dos_pipelines):
    """_require_pipeline_owner no tiene excepcion para superadmin: un
    superadmin que no es el duenio recibe 404 en /api/pipelines/{id}. /api/state
    no puede mostrarle mas de lo que esos endpoints le dejan leer."""
    assert _estado_para("ADMIN", "TENANT-A", "superadmin")["active_pipelines"] == {}


def test_las_sesiones_conectadas_de_otros_no_se_exponen(dos_pipelines):
    body = _estado_para("USUARIO-B", "TENANT-B")
    assert list(body["connected_users"]) == ["USUARIO-B"]


def test_lo_global_sigue_igual_para_todos(dos_pipelines):
    """facets y las_manos_alive son estado del ecosistema, no de un usuario:
    el frontend los necesita (useJaxStore.loadState)."""
    body = _estado_para("USUARIO-B", "TENANT-B")
    completo = dos_pipelines.model_dump()
    for clave in ("facets", "las_manos_alive", "last_health_check"):
        assert body[clave] == completo[clave]


def test_el_estado_global_no_se_modifica_al_filtrar(dos_pipelines):
    _estado_para("USUARIO-B", "TENANT-B")
    assert set(dos_pipelines.active_pipelines) == {PID_A, PID_B}
    assert set(dos_pipelines.connected_users) == {"USUARIO-A", "USUARIO-B"}


def test_por_http_un_viewer_de_otro_tenant_no_ve_el_pipeline(client, monkeypatch):
    estado = engine_state.get_state()
    duenio = uid(client, "t6-state-duenio", "operator")
    monkeypatch.setattr(estado, "active_pipelines", {
        PID_A: PipelineState(pipeline_id=PID_A, tenant_id="TENANT-A", user_id=duenio,
                             name="secreto de A", status="running"),
    })
    ajeno = client.get("/api/state", headers=cabeceras(client, "t6-state-ajeno", "viewer", tenant_id="TENANT-B"))
    assert ajeno.status_code == 200, ajeno.text
    assert ajeno.json()["active_pipelines"] == {}
    assert "secreto de A" not in ajeno.text

    propio = client.get("/api/state", headers=cabeceras(client, "t6-state-duenio", "operator", tenant_id="TENANT-A"))
    assert propio.status_code == 200, propio.text
    assert list(propio.json()["active_pipelines"]) == [PID_A]
