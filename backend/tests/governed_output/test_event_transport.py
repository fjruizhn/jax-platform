"""Real native-event producer coverage for the Platform F2-E boundary."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

from auth.models import AuthUser
from jax_engine.schemas import JAXEvent
from jax_engine.state import JAXEngineState


def test_native_health_event_prepares_exact_sse_unit_from_authenticated_scope(monkeypatch):
    """Removing the native producer must not permit raw JAXEvent SSE output."""
    root = Path(__file__).resolve().parents[4] / "f2e-general-jax"
    if not root.is_dir():
        return
    monkeypatch.setenv("JAX_REPO_PATH", str(root))
    from governed_output import event_transport
    from governed_output.composition import runtime_output_composer
    from jax_engine import status_resolution

    state = JAXEngineState()
    state._commit_las_manos_health_observation(True, datetime.now(timezone.utc))
    monkeypatch.setattr(event_transport, "engine_state", state)
    monkeypatch.setattr(status_resolution, "engine_state", state)
    runtime_output_composer.cache_clear()

    event = JAXEvent(event_type="las_manos_health_changed", tenant_id="1", user_id="u-1",
                     payload={"alive": True})
    prepared = asyncio.run(event_transport.prepare_native_event(
        event=event, user=AuthUser(user_id="u-1", tenant_id="1", role="viewer"),
        delivery=event_transport.PlatformDeliveryKind.SSE_JSON,
    ))

    assert prepared.transport_unit.wire_bytes.startswith(b"event:jax-event\ndata:")
    assert prepared.transport_unit.wire_bytes.endswith(b"\n\n")
    assert b'"event_type":"las_manos_health_changed"' in prepared.transport_unit.wire_bytes
    assert b'"user_id":"u-1"' in prepared.transport_unit.wire_bytes


def test_native_event_delivery_sends_only_prepared_bytes_after_commit_intent(monkeypatch):
    """Replacing the F2-D unit with event.model_dump must make this fail."""
    root = Path(__file__).resolve().parents[4] / "f2e-general-jax"
    if not root.is_dir():
        return
    monkeypatch.setenv("JAX_REPO_PATH", str(root))
    from governed_output import event_transport
    from governed_output.composition import runtime_output_composer
    from governed_output.transport import StructuredOutputOutboxRepository, StructuredTransportAuthorization
    from jax_engine import status_resolution

    state = JAXEngineState()
    state._commit_las_manos_health_observation(True, datetime.now(timezone.utc))
    monkeypatch.setattr(event_transport, "engine_state", state)
    monkeypatch.setattr(status_resolution, "engine_state", state)
    runtime_output_composer.cache_clear()
    event = JAXEvent(event_type="las_manos_health_changed", tenant_id="1", user_id="u-1",
                     payload={"alive": True})

    class RecordingRepository(StructuredOutputOutboxRepository):
        def __init__(self):
            self.transitions = []

        async def prepare(self, unit):
            scope = unit.envelope.response_scope
            return StructuredTransportAuthorization("outbox", 1, scope.scope_digest, scope.request_id,
                unit.durable_projection()["response_id"], scope.subject_id, unit.idempotency_key,
                unit.wire_bytes, unit.wire_payload_digest, unit)

        async def transition(self, authorization, target, *, failure_class=None):
            self.transitions.append((target.value, failure_class))

    sent: list[bytes] = []

    async def send_wire(payload: bytes):
        sent.append(payload)

    repository = RecordingRepository()
    committed = asyncio.run(event_transport.deliver_native_event(
        event=event, user=AuthUser(user_id="u-1", tenant_id="1", role="viewer"),
        delivery=event_transport.PlatformDeliveryKind.SSE_JSON,
        send_wire=send_wire, repository=repository,
    ))
    assert committed is True
    assert len(sent) == 1 and sent[0].startswith(b"event:jax-event\ndata:")
    assert b'"event_type":"las_manos_health_changed"' in sent[0]
    assert repository.transitions == [
        ("TRANSPORT_COMMITTING", None),
        ("OUTPUT_COMMITTED_TO_TRANSPORT", None),
    ]


def test_unregistered_native_event_never_falls_back_to_raw_event_json():
    """Adding a raw broadcast fallback would expose heartbeat payloads here."""
    from governed_output import event_transport

    event = JAXEvent(event_type="heartbeat", tenant_id="1", user_id="u-1", payload={"ping": True})
    sent: list[bytes] = []

    async def send_wire(payload: bytes):
        sent.append(payload)

    delivered = asyncio.run(event_transport.deliver_native_event(
        event=event, user=AuthUser(user_id="u-1", tenant_id="1", role="viewer"),
        delivery=event_transport.PlatformDeliveryKind.WEBSOCKET_JSON, send_wire=send_wire,
    ))
    assert delivered is False
    assert sent == [b'{"event_type":"governed_output_unavailable","payload":{}}']
    assert event.event_id.encode() not in sent[0]
    assert b"ping" not in sent[0]
