"""Native JAX event producer for the closed F2-E transport boundary.

The event bus only carries the server schema.  This module is the sole place
that turns that schema into TOOL_DATA and then asks the paired core to render
an immutable F2-D unit.  Transport code receives that unit's bytes only.
"""
from __future__ import annotations

import hashlib
import logging
import asyncio
from collections.abc import Awaitable, Callable

from auth.models import AuthUser
from jax_engine.schemas import JAXEvent
from jax_engine.state import engine_state
from starlette.responses import Response

from .adapter import PlatformGovernedExternalOutputAdapter, PreparedStructuredOutput
from .channels import PlatformDeliveryKind, channel_binding
from .composition import compose_runtime_output
from .core import load_structured_core
from .event_contracts import EventContract, event_contract
from .transport import StructuredOutputOutboxRepository, commit_structured_wire

logger = logging.getLogger(__name__)


class GovernedEventUnavailable(RuntimeError):
    """A native event cannot be represented by its registered contract."""


def unavailable_wire(delivery: PlatformDeliveryKind) -> bytes:
    """Static server-owned failure frame; it contains no event/model state."""
    if delivery is PlatformDeliveryKind.SSE_JSON:
        return b"event: jax-event\ndata: {\"event_type\":\"governed_output_unavailable\",\"payload\":{}}\n\n"
    if delivery is PlatformDeliveryKind.WEBSOCKET_JSON:
        return b'{"event_type":"governed_output_unavailable","payload":{}}'
    raise GovernedEventUnavailable("native events are stream-only deliveries")


def _idempotency(event_id: str, delivery: PlatformDeliveryKind) -> str:
    return hashlib.sha256(("f2e-native-event:" + delivery.value + ":" + event_id).encode("utf-8")).hexdigest()


def _runtime_requests(event: JAXEvent, contract: EventContract, runtime) -> tuple[object, ...]:
    payload = event.payload
    requests: list[object] = []
    for predicate, pointer in contract.runtime_slots:
        if predicate == "ENGINE_STATUS":
            snapshot = engine_state.engine_health_status_snapshot("las_manos")
            if snapshot is None or snapshot[0] not in {"alive", "down"}:
                raise GovernedEventUnavailable("registered engine observation is unavailable")
            if payload.get("alive") is not (snapshot[0] == "alive"):
                raise GovernedEventUnavailable("health event is not its current atomic observation")
            arguments = {"name": "las_manos", "status": snapshot[0]}
            map_id = "engine-status-bool-v1"
        elif predicate == "FACET_RUNTIME_STATUS":
            name = payload.get("facet")
            if not isinstance(name, str) or not name:
                raise GovernedEventUnavailable("facet event lacks registered facet identity")
            snapshot = engine_state.facet_runtime_status_snapshot(name)
            if snapshot is None:
                raise GovernedEventUnavailable("facet observation is unavailable")
            arguments = {"name": name, "status": snapshot[0]}
            map_id = None
        elif predicate == "PIPELINE_STATUS":
            pipeline_id, status = payload.get("pipeline_id"), payload.get("status")
            if not isinstance(pipeline_id, str) or not pipeline_id or not isinstance(status, str) or not status:
                raise GovernedEventUnavailable("pipeline event lacks registered pipeline claim arguments")
            arguments = {"pipeline_id": pipeline_id, "status": status}
            map_id = None
        else:  # Every registered event contract is explicit; future predicates fail closed.
            raise GovernedEventUnavailable("event contract uses unsupported runtime predicate")
        requests.append(runtime.RuntimeClaimRequest(predicate, arguments, pointer, presentation_map_id=map_id))
    return tuple(requests)


async def prepare_native_event(*, event: JAXEvent, user: AuthUser,
                               delivery: PlatformDeliveryKind) -> PreparedStructuredOutput:
    """Compose one server-originated event into its exact F2-D bytes."""
    if not isinstance(event, JAXEvent) or not isinstance(user, AuthUser):
        raise GovernedEventUnavailable("native events require JAXEvent and authenticated principal")
    if event.user_id != str(user.user_id) or event.tenant_id != str(user.tenant_id):
        raise GovernedEventUnavailable("event recipient differs from authenticated stream scope")
    if delivery not in {PlatformDeliveryKind.SSE_JSON, PlatformDeliveryKind.WEBSOCKET_JSON}:
        raise GovernedEventUnavailable("native events require a registered stream delivery")
    try:
        contract = event_contract(event.event_type)
    except ValueError as exc:
        raise GovernedEventUnavailable("event type has no governed contract") from exc
    core = load_structured_core()
    runtime = core.runtime_composition
    requests = _runtime_requests(event, contract, runtime)
    # JAXEvent is typed server data. The route never receives a candidate or
    # origin map from clients; number registration is an immutable server map.
    composed = await compose_runtime_output(
        user=user, tool_data=event.model_dump(mode="json"), requests=requests,
        number_binding_contract_id="platform-native-event.v1",
    )
    binding = channel_binding(delivery)
    channel = getattr(core.external_output.ExternalOutputChannelId, binding.core_channel_name)
    structured = core.structured_output
    layout_id = "platform-native-event-" + contract.contract_id.value + "-" + delivery.value.lower() + ".v1"
    origins = tuple(
        structured.OriginBinding(pointer, structured.OutputOrigin(origin), source)
        for pointer, origin, source in contract.origins
    )
    layout = structured.StructuredLayout(
        layout_id=layout_id, layout_version="1", channel_id=channel.value,
        root_block_index=0, slots=composed.slots, origins=origins,
        presentation_maps={"engine-status-bool-v1": {"alive": True, "down": False}},
        fallback={"event_type": "governed_output_unavailable", "payload": {}},
        number_bindings=composed.number_bindings,
    )
    registry = structured.StructuredLayoutRegistry({layout.layout_id: layout})
    submission = core.external_output.GovernedExternalOutputSubmission(
        composed.envelope, composed.envelope.response_scope, structured.OutputOrigin.SYSTEM,
        channel, output_reference="event:" + event.event_id,
    )
    return PlatformGovernedExternalOutputAdapter(core).prepare(
        submission, layout=layout, context=composed.context, layout_registry=registry,
        delivery=delivery, idempotency_key=_idempotency(event.event_id, delivery),
        event_type="jax-event" if delivery is PlatformDeliveryKind.SSE_JSON else None,
    )


async def deliver_native_event(*, event: JAXEvent, user: AuthUser,
                               delivery: PlatformDeliveryKind,
                               send_wire: Callable[[bytes], Awaitable[None]],
                               repository: StructuredOutputOutboxRepository | None = None) -> bool:
    """Persist/commit one immutable unit, or send only the static fallback.

    A successful return means the terminal transport send completed and F2-D
    recorded OUTPUT_COMMITTED_TO_TRANSPORT. A false return is deliberately not
    a delivery acknowledgement for the fallback frame.
    """
    try:
        prepared = await prepare_native_event(event=event, user=user, delivery=delivery)
        repo = repository or StructuredOutputOutboxRepository()
        authorization = await repo.prepare(prepared.transport_unit)
    except Exception as exc:
        # Governance, DB, or pre-send failure must never fall through to the
        # original JAXEvent JSON. The static frame intentionally has no event
        # fields, tool output, model text, or current-state proposition.
        logger.warning("governed native event unavailable (%s)", type(exc).__name__)
        await send_wire(unavailable_wire(delivery))
        return False
    try:
        await commit_structured_wire(authorization=authorization, repository=repo, send_wire=send_wire)
        return True
    except Exception:
        # Once F2-D reached its commit boundary a failed send is explicitly
        # UNKNOWN, not permission to attempt another frame.  The repository
        # already records that state; callers may close the broken transport.
        return False


class GovernedSSEEventsResponse(Response):
    """ASGI SSE response that commits only after each body send returns."""

    media_type = "text/event-stream"

    def __init__(self, *, user: AuthUser, next_event: Callable[[], Awaitable[JAXEvent | None]],
                 on_close: Callable[[], Awaitable[None]]):
        super().__init__(content=b"", status_code=200, media_type=self.media_type,
                         headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
        self._user = user
        self._next_event = next_event
        self._on_close = on_close

    async def __call__(self, scope, receive, send):
        await send({"type": "http.response.start", "status": self.status_code,
                    "headers": self.raw_headers})

        async def disconnected() -> None:
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return

        disconnect_task = asyncio.create_task(disconnected())
        try:
            while True:
                event_task = asyncio.create_task(self._next_event())
                done, _pending = await asyncio.wait(
                    {event_task, disconnect_task}, return_when=asyncio.FIRST_COMPLETED,
                )
                if disconnect_task in done:
                    event_task.cancel()
                    try:
                        await event_task
                    except asyncio.CancelledError:
                        pass
                    break
                event = event_task.result()
                if event is None:
                    break

                async def _send_wire(payload: bytes) -> None:
                    await send({"type": "http.response.body", "body": payload, "more_body": True})

                await deliver_native_event(event=event, user=self._user,
                                           delivery=PlatformDeliveryKind.SSE_JSON,
                                           send_wire=_send_wire)
        finally:
            if not disconnect_task.done():
                disconnect_task.cancel()
                try:
                    await disconnect_task
                except asyncio.CancelledError:
                    pass
            try:
                await send({"type": "http.response.body", "body": b"", "more_body": False})
            finally:
                await self._on_close()
