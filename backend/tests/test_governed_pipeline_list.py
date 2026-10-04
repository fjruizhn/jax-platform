"""Real authenticated pipeline-list -> F2-B -> F2-C -> F2-D pair tests."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest


JAX_REPO = Path(os.environ.get("F2E_TEST_JAX_REPO", "/home/fruiz/worktrees/f2e-structured-projection-jax"))


def _setup_pair(monkeypatch):
    monkeypatch.setenv("JAX_REPO_PATH", str(JAX_REPO))
    monkeypatch.setenv("JAX_DB_HOST", "127.0.0.1")
    monkeypatch.setenv("JAX_DB_PORT", "3308")
    monkeypatch.setenv("JAX_DB_NAME", "jax_memory_test")
    monkeypatch.setenv("JAX_ENVIRONMENT", "test")
    monkeypatch.setenv("JAX_JWT_SECRET", "test-secret-that-is-long-enough-for-load")
    if str(JAX_REPO) not in sys.path:
        sys.path.insert(0, str(JAX_REPO))
    import jacobs.models as models
    from jacobs import store
    from api import governed_pipeline_list as boundary
    return models, store, boundary


def _pipeline(models, *, owner="user-7", tenant="tenant-3", status="running"):
    return models.Pipeline(
        pipeline_id="pipeline-7", name="Pipeline seven", invoked_by="plataforma",
        mode="supervised", status=models.PipelineStatus(status), tenant_id=tenant,
        user_id=owner, updated_at=datetime.now(timezone.utc).timestamp())


def _user():
    from auth.models import AuthUser
    return AuthUser(user_id="user-7", tenant_id="tenant-3", role="operator")


class _Cursor:
    def __init__(self):
        self.query = ""
        self.params = None
        self.executed = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def execute(self, query, params):
        self.query, self.params = query, params
        self.executed.append((query, params))

    async def fetchall(self):
        if "axioma_usage" in self.query:
            return [("pipeline-7", 0.08)]
        return [("pipeline-7", "Pipeline seven", "running", 10.0, 12.5)]


class _Connection:
    def __init__(self, cursor):
        self._cursor = cursor

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    def cursor(self):
        return self._cursor


class _Pool:
    def __init__(self, cursor):
        self._cursor = cursor

    def acquire(self):
        return _Connection(self._cursor)


def test_authenticated_pipeline_list_crosses_real_producer_f2b_f2c_f2d_and_sends_bound_bytes(monkeypatch):
    models, store, boundary = _setup_pair(monkeypatch)
    canonical = _pipeline(models)

    async def pipeline_get(pipeline_id):
        assert pipeline_id == canonical.pipeline_id
        return canonical

    monkeypatch.setattr(store, "pipeline_get", pipeline_get)
    from api import pipelines
    cursor = _Cursor()
    async def get_pool():
        return _Pool(cursor)
    monkeypatch.setattr(pipelines, "get_pool", get_pool)
    user = _user()
    response = asyncio.run(pipelines.list_pipelines(
        user=user, limite=20, offset=0, estado=None, cursor=None))

    assert response.status_code == 200
    decoded = json.loads(response.body)
    assert decoded["pipelines"][0]["status"] == "running"
    assert decoded["pipelines"][0]["costo_usd"] == 0.08
    assert cursor.executed[0][1] == (user.user_id, user.tenant_id, 21, 0)
    assert b'"status":"running"' in response.body
    assert response._transport_unit.projection.field_claim_ids == {
        "/pipelines/0/status": response._transport_unit.projection.claim_ids[0]}

    sent = []

    async def send(message):
        sent.append(message)

    asyncio.run(response({}, None, send))
    assert sent[0]["type"] == "http.response.start"
    assert sent[1]["type"] == "http.response.body"
    assert sent[1]["body"] is response._transport_unit.canonical_bytes
    assert response.lifecycle_state.value == "OUTPUT_COMMITTED_TO_TRANSPORT"


def test_pipeline_list_wrong_canonical_owner_fails_closed_without_candidate_leak(monkeypatch):
    models, store, boundary = _setup_pair(monkeypatch)
    canonical = _pipeline(models, owner="other-user")

    async def pipeline_get(_pipeline_id):
        return canonical

    monkeypatch.setattr(store, "pipeline_get", pipeline_get)
    response = asyncio.run(boundary.govern_pipeline_list({
        "pipelines": [{"pipeline_id": "pipeline-7", "name": "private-name",
                       "status": "running"}], "has_more": False}, _user()))

    assert response.status_code == 503
    assert response.body == b'{"detail":{"code":"governed_output_unavailable"}}'
    assert b"private-name" not in response.body


def test_pipeline_list_transport_failure_records_unknown_without_ack(monkeypatch):
    models, store, boundary = _setup_pair(monkeypatch)
    canonical = _pipeline(models)

    async def pipeline_get(_pipeline_id):
        return canonical

    monkeypatch.setattr(store, "pipeline_get", pipeline_get)
    response = asyncio.run(boundary._govern_pipeline_list({
        "pipelines": [{"pipeline_id": "pipeline-7", "name": "Pipeline seven",
                       "status": "running"}], "has_more": False}, _user()))

    sends = 0

    async def failed_send(_message):
        nonlocal sends
        sends += 1
        if sends == 2:
            raise OSError("simulated transport failure")

    with pytest.raises(OSError):
        asyncio.run(response({}, None, failed_send))
    assert response.lifecycle_state.value == "TRANSPORT_OUTCOME_UNKNOWN"


@pytest.mark.parametrize(("renderer", "domain"), [
    ("f2-c.renderer.2", "f2-c.domain.2"),
    ("f2-c.renderer.4", "f2-c.domain.7"),
])
def test_unsupported_f2c_tuple_fails_closed(monkeypatch, renderer, domain):
    _, _, boundary = _setup_pair(monkeypatch)
    core = boundary._paired_core()
    monkeypatch.setattr(core[0], "GOVERNED_RENDERER_API_VERSION", renderer)
    monkeypatch.setattr(core[0], "GOVERNED_DOMAIN_SPEC_VERSION", domain)
    response = asyncio.run(boundary.govern_pipeline_list({
        "pipelines": [{"pipeline_id": "secret", "name": "private-name",
                       "status": "running"}], "has_more": False}, _user()))
    assert response.status_code == 503
    assert response.body == b'{"detail":{"code":"governed_output_unavailable"}}'
    assert b"private-name" not in response.body
