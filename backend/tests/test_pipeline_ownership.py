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

from api.pipelines import (
    _pipeline_owner_file,
    _require_pipeline_owner,
    cancel_pipeline,
    get_pipeline,
    get_pipeline_results,
    resume_pipeline,
)
from auth.models import AuthUser


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


@pytest.mark.parametrize(
    "endpoint,kwargs",
    [
        (get_pipeline, {}),
        (get_pipeline_results, {}),
        (resume_pipeline, {}),
        (cancel_pipeline, {}),
    ],
)
async def test_every_by_id_endpoint_enforces_ownership(endpoint, kwargs):
    attacker = AuthUser(user_id="attacker", tenant_id="1", role="operator")

    with pytest.raises(HTTPException) as exc:
        await endpoint(pipeline_id=str(uuid.uuid4()), user=attacker, **kwargs)

    assert exc.value.status_code == 404
