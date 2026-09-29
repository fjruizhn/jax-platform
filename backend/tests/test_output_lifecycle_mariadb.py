"""MariaDB tests for F2-D transactional preparation, attempts and transitions."""
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import uuid

import pytest

from db.connection import get_pool
from db.transaccion import transaccion
from webchat_f2d import repository as outbox_module
from webchat_f2d import repository

pytestmark = pytest.mark.usefixtures("client")


def _call(client, async_fn, *args, **kwargs):
    async def invoke():
        return await async_fn(*args, **kwargs)
    return client.portal.call(invoke)


def _unit(request_id=None, response_id=None, text="durable safe output", idempotency_key=None, scope=None):
    from policy.governance.governed_renderer import GovernedRenderer, RenderContext, WebChatGovernanceAdapter
    from policy.governance.output_lifecycle import mint_governed_transport_unit
    from policy.governance.response import GovernanceReceipt, ResponseScope

    request_id = request_id or (scope.request_id if scope else str(uuid.uuid4()))
    response_id = response_id or str(uuid.uuid4())
    scope = scope or ResponseScope(
        environment="test", tenant_id="1", project_id=None, subject_id="7",
        actor_id="user:7", audience="user:7", component_id="web-chat",
        request_id=request_id, trace_id=str(uuid.uuid4()),
    )
    receipt = GovernanceReceipt("test-policy", "test-vocabulary", "test-registry", "test-validator", "test-renderer")
    envelope = WebChatGovernanceAdapter(scope, receipt).seal_non_governed_candidate(
        response_id=response_id, candidate_text=text,
    )
    context = RenderContext(None, now=lambda: datetime.now(timezone.utc))
    rendered = GovernedRenderer().render_text(envelope, context)
    idem = idempotency_key or hashlib.sha256(
        f"{scope.scope_digest}:{request_id}:{response_id}:web-chat-http-json".encode()
    ).hexdigest()
    unit = mint_governed_transport_unit(
        envelope, rendered, context, transport_kind="web-chat-http-json",
        idempotency_key=idem,
    )
    body = json.dumps({
        "facet": "jekyll", "response": rendered.text,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "contract_degraded": False, "aviso": None,
        "response_id": rendered.response_id,
        "envelope_digest": rendered.envelope_digest,
        "source_envelope_digest": rendered.source_envelope_digest,
        "contract_state": rendered.contract_state.value, "governed_plain": True,
    }, ensure_ascii=False, separators=(",", ":")).encode()
    return scope, unit, body


async def _row(outbox_id):
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT state, committed_at, acknowledged_at, response_payload, attempt_sequence, previous_attempt_id "
                "FROM governed_output_outbox WHERE outbox_id=%s", (outbox_id,),
            )
            return await cur.fetchone()


async def _count_attempts(tenant_id, request_id):
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT COUNT(*) FROM governed_output_outbox WHERE tenant_id=%s AND request_id=%s",
                (tenant_id, request_id),
            )
            return (await cur.fetchone())[0]


async def _delete_request(tenant_id, request_id):
    async with transaccion() as cur:
        await cur.execute(
            "SELECT outbox_id FROM governed_output_outbox WHERE tenant_id=%s AND request_id=%s",
            (tenant_id, request_id),
        )
        rows = await cur.fetchall()
        for (outbox_id,) in rows:
            await cur.execute("DELETE FROM governed_output_lifecycle_events WHERE outbox_id=%s", (outbox_id,))
            await cur.execute("DELETE FROM governed_output_outbox WHERE outbox_id=%s", (outbox_id,))


def test_exact_preparation_survives_repository_reinstantiation_and_is_idempotent(client):
    scope, unit, payload = _unit()
    repo1 = repository.OutputOutboxRepository()
    args = dict(tenant_id=1, project_id=None, subject_id="7", request_id=unit.request_id)
    auth1 = _call(client, repo1.prepare, unit, payload, **args)
    auth2 = _call(client, repository.OutputOutboxRepository().prepare, unit, payload, **args)
    assert auth1.outbox_id == auth2.outbox_id
    assert auth1.attempt_id == auth2.attempt_id
    state, committed_at, acknowledged_at, stored, sequence, parent = client.portal.call(_row, auth1.outbox_id)
    assert state == "OUTPUT_PREPARED"
    assert committed_at is None and acknowledged_at is None
    assert bytes(stored) == payload
    assert (sequence, parent) == (1, None)
    assert client.portal.call(_count_attempts, 1, unit.request_id) == 1
    snapshot = _call(client, repository.OutputOutboxRepository().recovery_snapshot)
    assert snapshot["OUTPUT_PREPARED"] >= 1
    assert snapshot["OUTPUT_COMMITTED_TO_TRANSPORT"] == 0
    client.portal.call(_delete_request, 1, unit.request_id)


def test_same_idempotency_identity_with_changed_output_fails_closed(client):
    request_id = str(uuid.uuid4())
    _, unit_a, payload_a = _unit(request_id=request_id, text="safe A")
    _, unit_b, payload_b = _unit(request_id=request_id, text="safe B", idempotency_key=unit_a.idempotency_key)
    repo = repository.OutputOutboxRepository()
    auth = _call(client, repo.prepare, unit_a, payload_a, tenant_id=1, project_id=None,
                              subject_id="7", request_id=request_id)
    with pytest.raises(repository.OutputIdentityConflict):
        _call(client, repo.prepare, unit_b, payload_b, tenant_id=1, project_id=None,
                           subject_id="7", request_id=request_id)
    client.portal.call(_delete_request, 1, request_id)


def test_scope_and_wire_mutation_are_rejected_before_transport(client):
    request_id = str(uuid.uuid4())
    _, unit, payload = _unit(request_id=request_id)
    repo = repository.OutputOutboxRepository()
    with pytest.raises(repository.OutputLifecycleUnavailable):
        _call(client, repo.prepare, unit, payload, tenant_id=2, project_id=None,
                           subject_id="7", request_id=request_id)
    with pytest.raises(repository.OutputLifecycleUnavailable):
        _call(client, repo.prepare, unit, payload + b" ", tenant_id=1, project_id=None,
                           subject_id="7", request_id=request_id)
    assert client.portal.call(_count_attempts, 1, request_id) == 0


def test_prepare_and_audit_event_rollback_atomically(client, monkeypatch):
    request_id = str(uuid.uuid4())
    _, unit, payload = _unit(request_id=request_id)
    repo = repository.OutputOutboxRepository()

    async def fail_event(*_args, **_kwargs):
        raise RuntimeError("simulated audit insert failure")

    monkeypatch.setattr(repo, "_event", fail_event)
    with pytest.raises(RuntimeError):
        _call(client, repo.prepare, unit, payload, tenant_id=1, project_id=None,
                           subject_id="7", request_id=request_id)
    assert client.portal.call(_count_attempts, 1, request_id) == 0


def test_concurrent_prepare_converges_and_concurrent_commit_is_single_winner(client):
    request_id = str(uuid.uuid4())
    _, unit, payload = _unit(request_id=request_id)
    repo1, repo2 = repository.OutputOutboxRepository(), repository.OutputOutboxRepository()
    args = dict(tenant_id=1, project_id=None, subject_id="7", request_id=request_id)

    async def prepare_both():
        return await asyncio.gather(repo1.prepare(unit, payload, **args), repo2.prepare(unit, payload, **args))

    auth1, auth2 = client.portal.call(prepare_both)
    assert auth1.outbox_id == auth2.outbox_id

    from api.governed_chat import _lifecycle_core
    core = _lifecycle_core()
    target = core.OutputLifecycleState.TRANSPORT_COMMITTING

    async def commit_both():
        return await asyncio.gather(
            repo1.transition(auth1, target), repo2.transition(auth2, target),
            return_exceptions=True,
        )

    results = client.portal.call(commit_both)
    assert sum(not isinstance(value, Exception) for value in results) == 1
    assert sum(isinstance(value, Exception) for value in results) == 1
    state, *_ = client.portal.call(_row, auth1.outbox_id)
    assert state == "TRANSPORT_COMMITTING"
    client.portal.call(_delete_request, 1, request_id)


def test_changed_output_retry_has_new_attempt_and_preserves_first_record(client):
    request_id = str(uuid.uuid4())
    scope, unit1, payload1 = _unit(request_id=request_id, text="first safe output")
    repo = repository.OutputOutboxRepository()
    auth1 = _call(client, repo.prepare, unit1, payload1, tenant_id=1, project_id=None,
                              subject_id="7", request_id=request_id)
    from api.governed_chat import _lifecycle_core
    core = _lifecycle_core()
    _call(client, repo.transition, auth1, core.OutputLifecycleState.FAILED_BEFORE_COMMIT,
                       failure_class="TEST_PRECOMMIT_FAILURE")
    _, unit2, payload2 = _unit(request_id=request_id, text="replacement safe output", scope=scope)
    auth2 = _call(client, repo.prepare, unit2, payload2, tenant_id=1, project_id=None,
                              subject_id="7", request_id=request_id,
                              previous_attempt_id=auth1.attempt_id)
    assert auth2.attempt_id != auth1.attempt_id
    assert client.portal.call(_row, auth1.outbox_id)[0] == "FAILED_BEFORE_COMMIT"
    state2, _, _, stored2, seq2, parent2 = client.portal.call(_row, auth2.outbox_id)
    assert state2 == "OUTPUT_PREPARED"
    assert bytes(stored2) == payload2
    assert seq2 == 2 and parent2 == auth1.attempt_id
    client.portal.call(_delete_request, 1, request_id)


def test_outbox_schema_is_versioned_and_rollback_is_explicit():
    assert outbox_module.OUTBOX_RECORD_SCHEMA_VERSION == "f2-d.outbox.record.1"
    from db.output_lifecycle_migration import DOWN_SQL, UP_SQL
    assert len(UP_SQL) == 3 and len(DOWN_SQL) == 2
    assert DOWN_SQL[0].endswith("governed_output_lifecycle_events")
    assert DOWN_SQL[1].endswith("governed_output_outbox")


def test_mariadb_migration_created_innodb_outbox_and_scoped_constraints(client):
    async def inspect_schema():
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT ENGINE FROM information_schema.TABLES "
                    "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='governed_output_outbox'"
                )
                outbox_row = await cur.fetchone()
                await cur.execute(
                    "SELECT ENGINE FROM information_schema.TABLES "
                    "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='governed_output_lifecycle_events'"
                )
                event_row = await cur.fetchone()
                await cur.execute(
                    "SELECT INDEX_NAME, COLUMN_NAME, NON_UNIQUE "
                    "FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE() "
                    "AND TABLE_NAME='governed_output_outbox'"
                )
                outbox_indexes = await cur.fetchall()
                await cur.execute(
                    "SELECT INDEX_NAME, COLUMN_NAME, NON_UNIQUE "
                    "FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE() "
                    "AND TABLE_NAME='governed_output_lifecycle_events'"
                )
                event_indexes = await cur.fetchall()
                return outbox_row, event_row, outbox_indexes, event_indexes

    outbox_row, event_row, outbox_indexes, event_indexes = client.portal.call(inspect_schema)
    assert outbox_row == ("InnoDB",)
    assert event_row == ("InnoDB",)
    unique = {(index, column) for index, column, non_unique in outbox_indexes if non_unique == 0}
    assert ("uq_go_output_idempotency", "tenant_id") in unique
    assert ("uq_go_output_attempt", "scope_digest") in unique
    assert ("uq_go_output_attempt_sequence", "attempt_sequence") in unique
    event_unique = {(index, column) for index, column, non_unique in event_indexes
                    if non_unique == 0}
    assert ("uq_go_output_event_seq", "outbox_id") in event_unique
    assert ("uq_go_output_event_seq", "sequence_no") in event_unique
