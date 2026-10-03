from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from governed_output.channels import PlatformDeliveryKind, channel_binding
from governed_output.core import StructuredCoreUnavailable, load_structured_core
from governed_output.composition import output_scope
from auth.models import AuthUser
from jax_engine.schemas import JAXEvent
from jax_engine.state import JAXEngineState


def test_platform_delivery_bindings_are_closed_and_use_paired_channel_names():
    assert channel_binding(PlatformDeliveryKind.HTTP_JSON).core_channel_name == "PLATFORM_HTTP_JSON_V1"
    assert channel_binding(PlatformDeliveryKind.SSE_JSON).outcome == "SSE_EVENT"
    assert channel_binding(PlatformDeliveryKind.WEBSOCKET_JSON).outcome == "WEBSOCKET_FRAME"
    with pytest.raises(TypeError):
        channel_binding("PLATFORM_HTTP_JSON_V1")


def test_exact_structured_core_loads_from_configured_paired_checkout(monkeypatch):
    root = Path(__file__).resolve().parents[4] / "f2e-general-jax"
    if not root.is_dir():
        pytest.skip("paired F2-E JAX worktree is not available")
    monkeypatch.setenv("JAX_REPO_PATH", str(root))
    # The loader must use the already imported process graph or fail closed;
    # tests must not replace `sys.modules` mid-process and leave F2-B classes
    # with split identities.
    core = load_structured_core()
    assert core.structured_output.STRUCTURED_RENDERER_API_VERSION == "f2-c.structured-renderer.2"
    assert core.lifecycle.STRUCTURED_OUTPUT_LIFECYCLE_API_VERSION == "f2-d.structured.1"
    assert core.external_output.EXTERNAL_OUTPUT_CHANNEL_REGISTRY_VERSION == "f2-e.channels.1"


def test_structured_core_rejects_missing_or_unpaired_checkout(monkeypatch, tmp_path):
    monkeypatch.setenv("JAX_REPO_PATH", str(tmp_path))
    with pytest.raises(StructuredCoreUnavailable):
        load_structured_core()


def test_output_scope_uses_authenticated_principal_not_a_client_channel(monkeypatch):
    root = Path(__file__).resolve().parents[4] / "f2e-general-jax"
    if not root.is_dir():
        pytest.skip("paired F2-E JAX worktree is not available")
    monkeypatch.setenv("JAX_REPO_PATH", str(root))
    user = AuthUser(user_id="u-1", tenant_id="t-1", role="viewer")
    scope = output_scope(user=user, request_id="request-1", trace_id="trace-1")
    assert scope.tenant_id == "t-1"
    assert scope.subject_id == "u-1"
    assert scope.audience == "user:u-1"
    assert scope.component_id == "platform-external-output"


def test_health_change_uses_atomic_snapshot_and_exact_sse_wire(monkeypatch):
    root = Path(__file__).resolve().parents[4] / "f2e-general-jax"
    if not root.is_dir():
        pytest.skip("paired F2-E JAX worktree is not available")
    monkeypatch.setenv("JAX_REPO_PATH", str(root))
    from governed_output import health_event
    from governed_output.channels import PlatformDeliveryKind
    from governed_output.composition import runtime_output_composer
    from jax_engine import status_resolution

    state = JAXEngineState()
    from datetime import datetime, timezone
    state._commit_las_manos_health_observation(True, datetime.now(timezone.utc))
    monkeypatch.setattr(health_event, "engine_state", state)
    monkeypatch.setattr(status_resolution, "engine_state", state)
    runtime_output_composer.cache_clear()
    event = JAXEvent(event_type="las_manos_health_changed", tenant_id="1", user_id="u-1",
                     payload={"alive": True})
    prepared = asyncio.run(health_event.prepare_health_change_event(
        event=event, user=AuthUser(user_id="u-1", tenant_id="1", role="viewer"),
        delivery=PlatformDeliveryKind.SSE_JSON,
    ))
    assert b'"alive":true' in prepared.canonical_bytes
    assert prepared.transport_unit.wire_bytes.endswith(b"\n\n")
    assert b"data:" in prepared.transport_unit.wire_bytes


def test_structured_wire_commit_uses_exact_prepared_sse_bytes(monkeypatch):
    root = Path(__file__).resolve().parents[4] / "f2e-general-jax"
    if not root.is_dir():
        pytest.skip("paired F2-E JAX worktree is not available")
    monkeypatch.setenv("JAX_REPO_PATH", str(root))
    from governed_output import health_event
    from governed_output.channels import PlatformDeliveryKind
    from governed_output.composition import runtime_output_composer
    from governed_output.transport import (
        StructuredOutputOutboxRepository,
        StructuredTransportAuthorization,
        commit_structured_wire,
    )
    from jax_engine import status_resolution
    from datetime import datetime, timezone

    state = JAXEngineState()
    state._commit_las_manos_health_observation(True, datetime.now(timezone.utc))
    monkeypatch.setattr(health_event, "engine_state", state)
    monkeypatch.setattr(status_resolution, "engine_state", state)
    runtime_output_composer.cache_clear()
    event = JAXEvent(event_type="las_manos_health_changed", tenant_id="1", user_id="u-1",
                     payload={"alive": True})
    prepared = asyncio.run(health_event.prepare_health_change_event(
        event=event, user=AuthUser(user_id="u-1", tenant_id="1", role="viewer"),
        delivery=PlatformDeliveryKind.SSE_JSON,
    ))

    class RecordingRepository(StructuredOutputOutboxRepository):
        def __init__(self):
            self.transitions = []

        async def transition(self, authorization, target, *, failure_class=None):
            self.transitions.append((target.value, failure_class))

    unit = prepared.transport_unit
    scope = unit.envelope.response_scope
    authorization = StructuredTransportAuthorization(
        "test-outbox", int(scope.tenant_id), scope.scope_digest, scope.request_id,
        unit.durable_projection()["response_id"], scope.subject_id, unit.idempotency_key,
        unit.wire_bytes, unit.wire_payload_digest, unit,
    )
    sent: list[bytes] = []

    async def send_wire(payload: bytes):
        sent.append(payload)

    repository = RecordingRepository()
    asyncio.run(commit_structured_wire(
        authorization=authorization, repository=repository, send_wire=send_wire,
    ))
    assert sent == [unit.wire_bytes]
    assert repository.transitions == [
        ("TRANSPORT_COMMITTING", None),
        ("OUTPUT_COMMITTED_TO_TRANSPORT", None),
    ]


def test_structured_wire_send_failure_records_unknown_not_delivery(monkeypatch):
    root = Path(__file__).resolve().parents[4] / "f2e-general-jax"
    if not root.is_dir():
        pytest.skip("paired F2-E JAX worktree is not available")
    monkeypatch.setenv("JAX_REPO_PATH", str(root))
    from governed_output import health_event
    from governed_output.channels import PlatformDeliveryKind
    from governed_output.composition import runtime_output_composer
    from governed_output.transport import (
        StructuredOutputOutboxRepository,
        StructuredTransportAuthorization,
        commit_structured_wire,
    )
    from jax_engine import status_resolution
    from datetime import datetime, timezone

    state = JAXEngineState()
    state._commit_las_manos_health_observation(True, datetime.now(timezone.utc))
    monkeypatch.setattr(health_event, "engine_state", state)
    monkeypatch.setattr(status_resolution, "engine_state", state)
    runtime_output_composer.cache_clear()
    event = JAXEvent(event_type="las_manos_health_changed", tenant_id="1", user_id="u-1",
                     payload={"alive": True})
    prepared = asyncio.run(health_event.prepare_health_change_event(
        event=event, user=AuthUser(user_id="u-1", tenant_id="1", role="viewer"),
        delivery=PlatformDeliveryKind.WEBSOCKET_JSON,
    ))

    class RecordingRepository(StructuredOutputOutboxRepository):
        def __init__(self):
            self.transitions = []

        async def transition(self, authorization, target, *, failure_class=None):
            self.transitions.append((target.value, failure_class))

    unit = prepared.transport_unit
    scope = unit.envelope.response_scope
    authorization = StructuredTransportAuthorization(
        "test-outbox", 1, scope.scope_digest, scope.request_id,
        unit.durable_projection()["response_id"], scope.subject_id, unit.idempotency_key,
        unit.wire_bytes, unit.wire_payload_digest, unit,
    )

    async def failing_send(_payload: bytes):
        raise OSError("simulated socket loss")

    repository = RecordingRepository()
    with pytest.raises(OSError, match="simulated socket loss"):
        asyncio.run(commit_structured_wire(
            authorization=authorization, repository=repository, send_wire=failing_send,
        ))
    assert repository.transitions == [
        ("TRANSPORT_COMMITTING", None),
        ("TRANSPORT_OUTCOME_UNKNOWN", "STRUCTURED_TRANSPORT_OUTCOME_UNKNOWN"),
    ]
    assert all(state != "DELIVERY_ACKNOWLEDGED" for state, _ in repository.transitions)

def test_event_contract_registry_is_closed_and_status_slots_are_explicit():
    from governed_output.event_contracts import event_contract
    assert event_contract("facet_status_changed").runtime_slots == (("FACET_RUNTIME_STATUS", "/payload/status"),)
    assert event_contract("pipeline_step_changed").runtime_slots == (("PIPELINE_STATUS", "/payload/status"),)
    with pytest.raises(ValueError):
        event_contract("caller_invented_channel")
