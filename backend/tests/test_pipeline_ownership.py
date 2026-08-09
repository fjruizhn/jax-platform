"""GET/POST /api/pipelines/{pipeline_id}(/results|/resume|/cancel) had no
ownership check -- any authenticated user who knew a pipeline_id could read
its results or resume/cancel it. Fixed the same way as GET
/api/command/{task_id} (see test_command_ownership.py): a durable owner
record written at creation time, since engine_state.active_pipelines (the
only in-memory ownership record) is evicted the moment a pipeline
completes -- exactly when /results is normally fetched.
"""
import json
import uuid

import pytest
from fastapi import HTTPException

import http_client
from api.pipelines import (
    _pipeline_owner_file,
    _require_pipeline_owner,
    cancel_pipeline,
    create_pipeline,
    get_pipeline,
    get_pipeline_results,
    resume_pipeline,
)
from auth.models import AuthUser


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


@pytest.fixture(autouse=True)
def _isolated_pipelines_dir(monkeypatch, tmp_path):
    # PIPELINES_DIR defaults to ~/jax/pipelines -- keep tests off real disk state.
    monkeypatch.setattr("api.pipelines.PIPELINES_DIR", tmp_path)


@pytest.fixture
def owned_pipeline():
    pipeline_id = str(uuid.uuid4())
    _pipeline_owner_file(pipeline_id).write_text(json.dumps({"tenant_id": "1", "user_id": "owner-user"}))
    return pipeline_id


def test_owner_passes_the_check(owned_pipeline):
    owner = AuthUser(user_id="owner-user", tenant_id="1", role="operator")

    _require_pipeline_owner(owned_pipeline, owner)  # no debe lanzar


def test_different_user_is_rejected(owned_pipeline):
    attacker = AuthUser(user_id="attacker", tenant_id="1", role="operator")

    with pytest.raises(HTTPException) as exc:
        _require_pipeline_owner(owned_pipeline, attacker)

    assert exc.value.status_code == 404


def test_same_user_id_different_tenant_is_rejected(owned_pipeline):
    cross_tenant = AuthUser(user_id="owner-user", tenant_id="2", role="operator")

    with pytest.raises(HTTPException) as exc:
        _require_pipeline_owner(owned_pipeline, cross_tenant)

    assert exc.value.status_code == 404


def test_pipeline_with_no_owner_file_is_rejected():
    # pipeline creada antes de este fix, o completada y cuyo owner file se
    # perdió -- debe fallar cerrado.
    user = AuthUser(user_id="anyone", tenant_id="1", role="operator")

    with pytest.raises(HTTPException) as exc:
        _require_pipeline_owner(str(uuid.uuid4()), user)

    assert exc.value.status_code == 404


def test_owner_file_with_non_dict_json_is_rejected():
    pipeline_id = str(uuid.uuid4())
    _pipeline_owner_file(pipeline_id).write_text(json.dumps(["not", "a", "dict"]))
    user = AuthUser(user_id="anyone", tenant_id="1", role="operator")

    with pytest.raises(HTTPException) as exc:
        _require_pipeline_owner(pipeline_id, user)

    assert exc.value.status_code == 404


@pytest.mark.parametrize("endpoint", [get_pipeline, get_pipeline_results, resume_pipeline, cancel_pipeline])
async def test_every_by_id_endpoint_enforces_ownership(endpoint):
    attacker = AuthUser(user_id="attacker", tenant_id="1", role="operator")

    with pytest.raises(HTTPException) as exc:
        await endpoint(pipeline_id=str(uuid.uuid4()), user=attacker)

    assert exc.value.status_code == 404


async def test_create_pipeline_writes_the_owner_file():
    user = AuthUser(user_id="creator", tenant_id="9", role="operator")
    fake_pipeline_id = str(uuid.uuid4())
    original = http_client._client
    http_client._client = _FakeClient(_FakeResponse({"pipeline_id": fake_pipeline_id}))
    try:
        await create_pipeline(request=_FakeRequest({"name": "test"}), user=user)
    finally:
        http_client._client = original

    owner = json.loads(_pipeline_owner_file(fake_pipeline_id).read_text())
    assert owner == {"tenant_id": "9", "user_id": "creator"}


async def test_owner_reaches_the_proxy_call_past_the_ownership_check(owned_pipeline):
    owner = AuthUser(user_id="owner-user", tenant_id="1", role="operator")
    original = http_client._client
    http_client._client = _FakeClient(_FakeResponse({"steps": []}))
    try:
        result = await get_pipeline_results(pipeline_id=owned_pipeline, user=owner)
    finally:
        http_client._client = original

    assert result == {"steps": []}
