"""GET/POST /api/pipelines/{pipeline_id}(/results|/resume|/cancel) had no
ownership check -- any authenticated user who knew a pipeline_id could read
its results or resume/cancel it. Fixed the same way as GET
/api/command/{task_id} (see test_command_ownership.py): a durable owner
record written at creation time, since engine_state.active_pipelines (the
only in-memory ownership record) is evicted the moment a pipeline
completes -- exactly when /results is normally fetched.

Ronda 5 (2026-08-20, T1): el owner record vive ahora en la columna
owner_ack_at de jacobs_pipelines (DB compartida con Jacobs), no en un
sidecar file de filesystem -- ver api/pipelines.py. Los fixtures de este
archivo insertan/borran filas reales contra jax_memory_test (aislada de
producción por conftest.py, JAX_DB_NAME=jax_memory_test). Sigue el mismo
patrón que test_motor_migrations.py: `client.portal.call(async_fn, *args)`
para correr helpers async desde tests sync -- el pool de DB es un
singleton atado al event loop que arrancó `client`, y pytest-asyncio con
loops por-test lo rompe (confirmado: "RuntimeError: Event loop is closed"
al probar con fixtures async planas antes de este patrón)."""
import time
import uuid

import pytest
from fastapi import HTTPException

import http_client
from api.pipelines import (
    _require_pipeline_owner,
    cancel_pipeline,
    create_pipeline,
    get_pipeline,
    get_pipeline_results,
    resume_pipeline,
)
from auth.models import AuthUser
from db.connection import get_pool


class _FakeResponse:
    def __init__(self, json_data, status_code=200):
        self._json_data = json_data
        self.status_code = status_code

    def json(self):
        return self._json_data


class _FakeClient:
    def __init__(self, response):
        self._response = response

    async def get(self, url, **kwargs):
        return self._response

    async def post(self, url, **kwargs):
        return self._response


class _FakeRequest:
    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


async def _insert_pipeline_row(pipeline_id, user_id, tenant_id, owner_ack_at):
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO jacobs_pipelines "
                "(pipeline_id, name, invoked_by, mode, status, created_at, updated_at, "
                " user_id, tenant_id, owner_ack_at) "
                "VALUES (%s, 'test', 'hyde', 'supervised', 'running', %s, %s, %s, %s, %s)",
                (pipeline_id, time.time(), time.time(), user_id, tenant_id, owner_ack_at),
            )


async def _delete_pipeline_row(pipeline_id):
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM jacobs_pipelines WHERE pipeline_id=%s", (pipeline_id,))


async def _select_owner_ack_at(pipeline_id):
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT owner_ack_at FROM jacobs_pipelines WHERE pipeline_id=%s", (pipeline_id,)
            )
            row = await cur.fetchone()
    return row[0] if row else None


async def _call_require_owner(pipeline_id, user):
    await _require_pipeline_owner(pipeline_id, user)


async def _call_endpoint(fn, pipeline_id, user):
    await fn(pipeline_id=pipeline_id, user=user)


async def _call_create_pipeline(request, user):
    return await create_pipeline(request=request, user=user)


async def _call_get_results(pipeline_id, user):
    return await get_pipeline_results(pipeline_id=pipeline_id, user=user)


@pytest.fixture
def owned_pipeline(client):
    pipeline_id = str(uuid.uuid4())
    client.portal.call(_insert_pipeline_row, pipeline_id, "owner-user", "1", time.time())
    yield pipeline_id
    client.portal.call(_delete_pipeline_row, pipeline_id)


def test_owner_passes_the_check(client, owned_pipeline):
    owner = AuthUser(user_id="owner-user", tenant_id="1", role="operator")
    client.portal.call(_call_require_owner, owned_pipeline, owner)  # no debe lanzar


def test_different_user_is_rejected(client, owned_pipeline):
    attacker = AuthUser(user_id="attacker", tenant_id="1", role="operator")

    with pytest.raises(HTTPException) as exc:
        client.portal.call(_call_require_owner, owned_pipeline, attacker)

    assert exc.value.status_code == 404


def test_same_user_id_different_tenant_is_rejected(client, owned_pipeline):
    cross_tenant = AuthUser(user_id="owner-user", tenant_id="2", role="operator")

    with pytest.raises(HTTPException) as exc:
        client.portal.call(_call_require_owner, owned_pipeline, cross_tenant)

    assert exc.value.status_code == 404


def test_pipeline_with_no_row_is_rejected(client):
    # pipeline_id inexistente (nunca creado, o cosechado/borrado) -- debe
    # fallar cerrado.
    user = AuthUser(user_id="anyone", tenant_id="1", role="operator")

    with pytest.raises(HTTPException) as exc:
        client.portal.call(_call_require_owner, str(uuid.uuid4()), user)

    assert exc.value.status_code == 404


def test_pipeline_with_owner_ack_at_null_is_rejected(client):
    # fila real (Jacobs ya la creo) pero jax-platform todavia no confirmo
    # su propio bookkeeping -- mismo caso que "owner file ausente" antes.
    pipeline_id = str(uuid.uuid4())
    client.portal.call(_insert_pipeline_row, pipeline_id, "someone", "1", None)
    user = AuthUser(user_id="someone", tenant_id="1", role="operator")
    try:
        with pytest.raises(HTTPException) as exc:
            client.portal.call(_call_require_owner, pipeline_id, user)
        assert exc.value.status_code == 404
    finally:
        client.portal.call(_delete_pipeline_row, pipeline_id)


@pytest.mark.parametrize("endpoint", [get_pipeline, get_pipeline_results, resume_pipeline, cancel_pipeline])
def test_every_by_id_endpoint_enforces_ownership(client, endpoint):
    attacker = AuthUser(user_id="attacker", tenant_id="1", role="operator")

    with pytest.raises(HTTPException) as exc:
        client.portal.call(_call_endpoint, endpoint, str(uuid.uuid4()), attacker)

    assert exc.value.status_code == 404


def test_create_pipeline_acks_the_owner_row(client):
    user = AuthUser(user_id="creator", tenant_id="9", role="operator")
    fake_pipeline_id = str(uuid.uuid4())
    # Simula lo que Jacobs ya habria hecho antes de que jax-platform llame
    # a _record_pipeline_owner: la fila existe, sin ack todavia.
    client.portal.call(_insert_pipeline_row, fake_pipeline_id, "creator", "9", None)
    original = http_client._client
    http_client._client = _FakeClient(_FakeResponse({"pipeline_id": fake_pipeline_id}))
    try:
        client.portal.call(_call_create_pipeline, _FakeRequest({"name": "test"}), user)
        owner_ack_at = client.portal.call(_select_owner_ack_at, fake_pipeline_id)
        assert owner_ack_at is not None
    finally:
        http_client._client = original
        client.portal.call(_delete_pipeline_row, fake_pipeline_id)


def test_owner_reaches_the_proxy_call_past_the_ownership_check(client, owned_pipeline):
    owner = AuthUser(user_id="owner-user", tenant_id="1", role="operator")
    original = http_client._client
    http_client._client = _FakeClient(_FakeResponse({"steps": []}))
    try:
        result = client.portal.call(_call_get_results, owned_pipeline, owner)
    finally:
        http_client._client = original

    assert result == {"steps": []}
