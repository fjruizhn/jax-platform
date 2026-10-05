"""Real authenticated pipeline-list -> F2-B -> F2-C -> F2-D pair tests."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest


JAX_REPO = Path(os.environ["JAX_REPO_PATH"])


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


def _status_snapshot(store, pipeline):
    return store.PipelineStatusSnapshot(
        pipeline_id=pipeline.pipeline_id, tenant_id=pipeline.tenant_id,
        user_id=pipeline.user_id, status=pipeline.status,
        observed_at=datetime.now(timezone.utc))


def _active_row(pipeline_id, name, status):
    return {
        "pipeline_id": pipeline_id, "name": name, "status": status,
        "created_at": 10.0, "updated_at": 12.5, "duracion_s": None,
        "costo_usd": None, "causa": None,
    }


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

    async def pipeline_status_snapshots(pipeline_ids):
        assert pipeline_ids == (canonical.pipeline_id,)
        return {canonical.pipeline_id: _status_snapshot(store, canonical)}

    monkeypatch.setattr(store, "pipeline_status_snapshots", pipeline_status_snapshots)
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

    resent = []
    with pytest.raises(boundary.GovernedPipelineListUnavailable):
        asyncio.run(response({}, None, resent.append))
    assert resent == []


def test_pipeline_list_wrong_canonical_owner_fails_closed_without_candidate_leak(monkeypatch):
    models, store, boundary = _setup_pair(monkeypatch)
    canonical = _pipeline(models, owner="other-user")

    async def pipeline_status_snapshots(pipeline_ids):
        assert pipeline_ids == (canonical.pipeline_id,)
        return {canonical.pipeline_id: _status_snapshot(store, canonical)}

    monkeypatch.setattr(store, "pipeline_status_snapshots", pipeline_status_snapshots)
    response = asyncio.run(boundary.govern_pipeline_list({
        "pipelines": [_active_row("pipeline-7", "private-name", "running")],
        "has_more": False}, _user()))

    assert response.status_code == 503
    assert response.body == b'{"detail":{"code":"governed_output_unavailable"}}'
    assert b"private-name" not in response.body


def test_pipeline_list_uses_one_ordered_evidence_batch_and_keeps_individual_claims(monkeypatch):
    models, store, boundary = _setup_pair(monkeypatch)
    canonical = {
        "pipeline-7": _pipeline(models, status="running"),
        "pipeline-8": models.Pipeline(
            pipeline_id="pipeline-8", name="Pipeline eight", invoked_by="plataforma",
            mode="supervised", status=models.PipelineStatus("completed"), tenant_id="tenant-3",
            user_id="user-7", updated_at=datetime.now(timezone.utc).timestamp()),
    }

    calls = []

    async def pipeline_status_snapshots(pipeline_ids):
        calls.append(pipeline_ids)
        return {pipeline_id: _status_snapshot(store, canonical[pipeline_id]) for pipeline_id in pipeline_ids}

    monkeypatch.setattr(store, "pipeline_status_snapshots", pipeline_status_snapshots)
    response = asyncio.run(boundary.govern_pipeline_list({
        "pipelines": [
            _active_row("pipeline-7", "Pipeline seven", "running"),
            _active_row("pipeline-8", "Pipeline eight", "completed"),
            ], "has_more": False}, _user()))

    assert response.status_code == 200
    assert calls == [("pipeline-7", "pipeline-8")]
    decoded = json.loads(response.body)
    assert [row["status"] for row in decoded["pipelines"]] == ["running", "completed"]
    assert len(response._transport_unit.projection.claim_ids) == 2


@pytest.mark.parametrize("payload", [
    {"pipelines": [], "has_more": False},
    {"pipelines": [], "has_more": False, "cursor_siguiente": None},
])
def test_empty_pipeline_page_is_canonical_without_a_status_query(monkeypatch, payload):
    _, _, boundary = _setup_pair(monkeypatch)
    core = boundary._paired_core()
    calls = 0

    class BatchResolver:
        async def evidence_many(self, _arguments_seq, _scope):
            nonlocal calls
            calls += 1
            raise AssertionError("empty pages must not query canonical pipeline status")

    monkeypatch.setattr(core[4], "JacobsPipelineStatusResolver", BatchResolver)
    monkeypatch.setattr(boundary, "_paired_core", lambda: core)
    response = asyncio.run(boundary.govern_pipeline_list(payload, _user()))

    assert response.status_code == 200
    assert json.loads(response.body) == payload
    assert calls == 0
    assert not response._transport_unit.projection.claim_ids


@pytest.mark.parametrize("payload", [
    {"has_more": False},
    {"pipelines": {}, "has_more": False},
    {"pipelines": [], "has_more": False, "unexpected": True},
])
def test_pipeline_list_rejects_invalid_top_level_before_status_query(monkeypatch, payload):
    _, _, boundary = _setup_pair(monkeypatch)
    core = boundary._paired_core()
    calls = 0

    class BatchResolver:
        async def evidence_many(self, _arguments_seq, _scope):
            nonlocal calls
            calls += 1
            return ()

    monkeypatch.setattr(core[4], "JacobsPipelineStatusResolver", BatchResolver)
    monkeypatch.setattr(boundary, "_paired_core", lambda: core)
    response = asyncio.run(boundary.govern_pipeline_list(payload, _user()))

    assert response.status_code == 503
    assert response.body == b'{"detail":{"code":"governed_output_unavailable"}}'
    assert calls == 0


@pytest.mark.parametrize("batch", [
    (),
    (object(), object()),
])
def test_pipeline_list_rejects_evidence_batch_with_wrong_cardinality(monkeypatch, batch):
    _, _, boundary = _setup_pair(monkeypatch)
    core = boundary._paired_core()

    class BatchResolver:
        async def evidence_many(self, _arguments_seq, _scope):
            return batch

    monkeypatch.setattr(core[4], "JacobsPipelineStatusResolver", BatchResolver)
    monkeypatch.setattr(boundary, "_paired_core", lambda: core)
    response = asyncio.run(boundary.govern_pipeline_list({
        "pipelines": [{"pipeline_id": "pipeline-7", "name": "private-name", "status": "running"}],
        "has_more": False}, _user()))

    assert response.status_code == 503
    assert response.body == b'{"detail":{"code":"governed_output_unavailable"}}'
    assert b"private-name" not in response.body


def test_pipeline_list_rejects_wrong_scope_evidence_before_candidate(monkeypatch):
    _, _, boundary = _setup_pair(monkeypatch)
    core = boundary._paired_core()

    class BatchResolver:
        async def evidence_many(self, _arguments_seq, scope):
            class Evidence:
                observation_scope = type("OtherScope", (), {"scope_digest": scope.scope_digest + "-other"})()
            return (Evidence(),)

    monkeypatch.setattr(core[4], "JacobsPipelineStatusResolver", BatchResolver)
    monkeypatch.setattr(boundary, "_paired_core", lambda: core)
    response = asyncio.run(boundary.govern_pipeline_list({
        "pipelines": [{"pipeline_id": "pipeline-7", "name": "private-name", "status": "running"}],
        "has_more": False}, _user()))

    assert response.status_code == 503
    assert response.body == b'{"detail":{"code":"governed_output_unavailable"}}'
    assert b"private-name" not in response.body


def test_pipeline_list_rejects_evidence_batch_in_wrong_order(monkeypatch):
    models, store, boundary = _setup_pair(monkeypatch)
    canonical = {
        "pipeline-7": _pipeline(models, status="running"),
        "pipeline-8": models.Pipeline(
            pipeline_id="pipeline-8", name="Pipeline eight", invoked_by="plataforma",
            mode="supervised", status=models.PipelineStatus("completed"), tenant_id="tenant-3",
            user_id="user-7", updated_at=datetime.now(timezone.utc).timestamp()),
    }

    async def pipeline_status_snapshots(pipeline_ids):
        return {pipeline_id: _status_snapshot(store, canonical[pipeline_id]) for pipeline_id in pipeline_ids}

    monkeypatch.setattr(store, "pipeline_status_snapshots", pipeline_status_snapshots)
    core = boundary._paired_core()
    original_resolver = core[4].JacobsPipelineStatusResolver

    class ReversedBatchResolver:
        async def evidence_many(self, arguments_seq, scope):
            return tuple(reversed(await original_resolver().evidence_many(arguments_seq, scope)))

    monkeypatch.setattr(core[4], "JacobsPipelineStatusResolver", ReversedBatchResolver)
    monkeypatch.setattr(boundary, "_paired_core", lambda: core)
    response = asyncio.run(boundary.govern_pipeline_list({
        "pipelines": [
            {"pipeline_id": "pipeline-7", "name": "private-name", "status": "running"},
            {"pipeline_id": "pipeline-8", "name": "private-name", "status": "completed"},
        ], "has_more": False}, _user()))

    assert response.status_code == 503
    assert response.body == b'{"detail":{"code":"governed_output_unavailable"}}'
    assert b"private-name" not in response.body


def test_pipeline_list_rejects_duplicate_producer_pipeline_ids_before_batch(monkeypatch):
    _, _, boundary = _setup_pair(monkeypatch)
    core = boundary._paired_core()
    calls = 0

    class BatchResolver:
        async def evidence_many(self, _arguments_seq, _scope):
            nonlocal calls
            calls += 1
            return ()

    monkeypatch.setattr(core[4], "JacobsPipelineStatusResolver", BatchResolver)
    monkeypatch.setattr(boundary, "_paired_core", lambda: core)
    response = asyncio.run(boundary.govern_pipeline_list({
        "pipelines": [
            {"pipeline_id": "pipeline-7", "name": "private-name", "status": "running"},
            {"pipeline_id": "pipeline-7", "name": "private-name", "status": "running"},
        ], "has_more": False}, _user()))

    assert response.status_code == 503
    assert calls == 0
    assert b"private-name" not in response.body


def test_pipeline_list_transport_failure_records_unknown_without_ack(monkeypatch):
    models, store, boundary = _setup_pair(monkeypatch)
    canonical = _pipeline(models)

    async def pipeline_status_snapshots(pipeline_ids):
        assert pipeline_ids == (canonical.pipeline_id,)
        return {canonical.pipeline_id: _status_snapshot(store, canonical)}

    monkeypatch.setattr(store, "pipeline_status_snapshots", pipeline_status_snapshots)
    response = asyncio.run(boundary._govern_pipeline_list({
        "pipelines": [_active_row("pipeline-7", "Pipeline seven", "running")],
        "has_more": False}, _user()))

    sends = 0

    async def failed_send(_message):
        nonlocal sends
        sends += 1
        if sends == 2:
            raise OSError("simulated transport failure")

    with pytest.raises(OSError):
        asyncio.run(response({}, None, failed_send))
    assert response.lifecycle_state.value == "TRANSPORT_OUTCOME_UNKNOWN"

    retried = []
    with pytest.raises(boundary.GovernedPipelineListUnavailable):
        asyncio.run(response({}, None, retried.append))
    assert retried == []
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
