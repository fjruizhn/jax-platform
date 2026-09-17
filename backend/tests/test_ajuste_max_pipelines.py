"""max_pipelines manda (spec 2026-09-16 §C): cupo de pipelines activos POR
TENANT, leído por request desde el ajuste -- subirlo en Admin libera el cupo
sin reiniciar. Tope 3 = el candado global de Jacobs (ajustes.MAX_PARALLEL_PIPELINES)."""
import pytest
from fastapi import HTTPException

import http_client
from api.pipelines import create_pipeline
from auth.models import AuthUser
from jax_engine.resource_manager import ResourceManager, resource_manager
from tests.identidades import cabeceras

TENANT = "ajuste-max-pipelines"
USUARIO = AuthUser(user_id="ajuste-mp-user", tenant_id=TENANT, role="operator")


class _FakeResponse:
    def __init__(self, json_data, status_code):
        self._json_data = json_data
        self.status_code = status_code

    def json(self):
        return self._json_data


class _FakeClient:
    def __init__(self, response):
        self._response = response

    async def post(self, url, **kwargs):
        return self._response


class _FakeRequest:
    async def json(self):
        return {"name": "carga-de-cupo"}


async def test_el_cupo_lo_decide_el_limite_que_se_pasa():
    rm = ResourceManager()
    await rm.admit_pipeline("t", "p1")
    await rm.admit_pipeline("t", "p2")
    assert await rm.can_start_pipeline("t", 2) is True
    assert await rm.can_start_pipeline("t", 3) is True
    assert await rm.can_start_pipeline("otro", 1) is True


async def _crear():
    try:
        return await create_pipeline(request=_FakeRequest(), user=USUARIO)
    except HTTPException as exc:
        return exc


@pytest.fixture
def un_pipeline_activo(client):
    client.portal.call(resource_manager.admit_pipeline, TENANT, "ocupado-1")
    original = http_client._client
    # Si el cupo deja pasar, "Jacobs" responde 422: prueba que se llegó a él sin crear nada.
    http_client._client = _FakeClient(_FakeResponse({"detail": "jacobs_dijo_que_no"}, 422))
    yield
    http_client._client = original
    client.portal.call(resource_manager.release_pipeline, TENANT, "ocupado-1")


def test_con_el_ajuste_en_uno_el_segundo_se_rechaza_con_codigo(client, ajustes_en_db, un_pipeline_activo):
    ajustes_en_db.poner(**{**ajustes_en_db.validos, "max_pipelines": "1"})
    exc = client.portal.call(_crear)
    assert isinstance(exc, HTTPException)
    assert (exc.status_code, exc.detail) == (429, {"code": "limite_de_pipelines", "max": 1})


def test_subir_el_ajuste_desde_admin_libera_el_cupo_sin_reiniciar(client, ajustes_en_db, un_pipeline_activo):
    ajustes_en_db.poner(**{**ajustes_en_db.validos, "max_pipelines": "1"})
    assert client.portal.call(_crear).status_code == 429
    r = client.put("/api/admin/config", json=[{"key": "max_pipelines", "value": "2"}],
                   headers=cabeceras(client, "ajustes-mp", role="superadmin"))
    assert r.status_code == 200
    exc = client.portal.call(_crear)
    # Pasó el cupo y llegó a Jacobs: su 422 vuelve con el código del frente A.
    assert (exc.status_code, exc.detail["code"]) == (422, "jacobs_rechazo")
