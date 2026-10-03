"""Narrow F2-E composition seam for the existing LAS MANOS change event.

The event boolean is checked against the atomic ENGINE_STATUS snapshot. It is
never itself the health authority; a layout-owned map keeps the JSON schema.
"""
from __future__ import annotations

import hashlib

from auth.models import AuthUser
from jax_engine.state import engine_state

from .adapter import PlatformGovernedExternalOutputAdapter
from .channels import PlatformDeliveryKind, channel_binding
from .composition import compose_runtime_output
from .core import load_structured_core


class GovernedHealthEventUnavailable(RuntimeError):
    """The change event cannot safely represent a current health observation."""


def _idempotency(event_id: str, delivery: PlatformDeliveryKind) -> str:
    return hashlib.sha256(("f2e-health-event:" + delivery.value + ":" + event_id).encode("utf-8")).hexdigest()


async def prepare_health_change_event(*, event: object, user: AuthUser, delivery: PlatformDeliveryKind):
    """Prepare exact governed bytes; the caller still owns F2-D transport."""
    if not isinstance(user, AuthUser) or not isinstance(delivery, PlatformDeliveryKind):
        raise GovernedHealthEventUnavailable("health event requires authenticated server delivery")
    event_type = getattr(event, "event_type", None)
    event_id = getattr(event, "event_id", None)
    payload = getattr(event, "payload", None)
    if event_type != "las_manos_health_changed" or not isinstance(event_id, str):
        raise GovernedHealthEventUnavailable("not a LAS MANOS health change event")
    if not isinstance(payload, dict) or type(payload.get("alive")) is not bool:
        raise GovernedHealthEventUnavailable("health event payload is not the registered shape")
    snapshot = engine_state.engine_health_status_snapshot("las_manos")
    if snapshot is None:
        raise GovernedHealthEventUnavailable("no completed Platform health probe exists")
    status, _observed_at = snapshot
    expected_alive = status == "alive"
    if status not in {"alive", "down"} or payload["alive"] is not expected_alive:
        raise GovernedHealthEventUnavailable("health event no longer matches atomic current observation")
    if not hasattr(event, "model_dump"):
        raise GovernedHealthEventUnavailable("event lacks server schema serialization")
    core = load_structured_core()
    runtime = core.runtime_composition
    composed = await compose_runtime_output(
        user=user,
        tool_data=event.model_dump(),
        requests=(runtime.RuntimeClaimRequest(
            "ENGINE_STATUS", {"name": "las_manos", "status": status},
            "/payload/alive", presentation_map_id="engine-status-bool-v1",
        ),),
    )
    binding = channel_binding(delivery)
    channel = getattr(core.external_output.ExternalOutputChannelId, binding.core_channel_name)
    structured = core.structured_output
    layout = structured.StructuredLayout(
        layout_id="platform-health-change-" + delivery.value.lower() + ".v1",
        layout_version="1", channel_id=channel.value, root_block_index=0,
        slots=composed.slots,
        origins=(structured.OriginBinding("", structured.OutputOrigin.SYSTEM, "platform:health-probe"),),
        presentation_maps={"engine-status-bool-v1": {"alive": True, "down": False}},
        fallback={"event_type": "governed_output_unavailable", "payload": {}},
    )
    registry = structured.StructuredLayoutRegistry({layout.layout_id: layout})
    submission = core.external_output.GovernedExternalOutputSubmission(
        composed.envelope, composed.envelope.response_scope, structured.OutputOrigin.SYSTEM,
        channel, output_reference="event:" + event_id,
    )
    return PlatformGovernedExternalOutputAdapter(core).prepare(
        submission, layout=layout, context=composed.context, layout_registry=registry,
        delivery=delivery, idempotency_key=_idempotency(event_id, delivery),
        event_type="jax-event" if delivery is PlatformDeliveryKind.SSE_JSON else None,
    )
