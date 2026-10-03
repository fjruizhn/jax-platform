"""Platform entry point for the paired F2-E structured output boundary.

Route composition supplies a sealed F2-A submission only after it has resolved
any F2-B slots from its designated server-owned source.  This module neither
turns a DTO into a claim nor chooses an authority source.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .channels import PlatformDeliveryKind, channel_binding
from .core import StructuredCore, StructuredCoreUnavailable, load_structured_core


@dataclass(frozen=True)
class PreparedStructuredOutput:
    """Opaque F2-D unit plus its canonical bytes, never a mutable DTO."""

    transport_unit: object
    canonical_bytes: bytes
    value: object


class PlatformGovernedExternalOutputAdapter:
    """Server-owned adapter from a pre-sealed F2-A graph to exact wire bytes."""

    def __init__(self, core: StructuredCore | None = None):
        self._core = core or load_structured_core()

    def prepare(
        self, submission: object, *, layout: object, context: object,
        layout_registry: object, delivery: PlatformDeliveryKind,
        idempotency_key: str, http_status: int | None = None,
        frame_type: str | None = None, event_type: str | None = None,
        now: datetime | None = None,
    ) -> PreparedStructuredOutput:
        """Render and mint the exact F2-D unit for a closed delivery mapping.

        ``submission`` must be JAX's server-minted
        ``GovernedExternalOutputSubmission``.  A route cannot provide a raw
        candidate, a textual rendering, or a channel string here.
        """
        if not isinstance(delivery, PlatformDeliveryKind):
            raise TypeError("structured output delivery must be server-owned")
        binding = channel_binding(delivery)
        external = self._core.external_output
        channel_enum = getattr(external.ExternalOutputChannelId, binding.core_channel_name, None)
        if channel_enum is None:
            raise StructuredCoreUnavailable("paired JAX lacks Platform channel identity")
        if not isinstance(submission, external.GovernedExternalOutputSubmission):
            raise TypeError("structured output needs a server-minted external submission")
        if submission.channel_id is not channel_enum:
            raise ValueError("submission channel does not match Platform delivery")
        if not isinstance(idempotency_key, str) or not idempotency_key:
            raise ValueError("structured output idempotency key is required")
        if delivery is PlatformDeliveryKind.HTTP_JSON:
            if http_status is None:
                http_status = 200
            if frame_type is not None or event_type is not None:
                raise ValueError("HTTP structured output cannot carry frame/event metadata")
            wire_encoding = "http-json"
        elif delivery is PlatformDeliveryKind.SSE_JSON:
            if http_status is not None or frame_type is not None:
                raise ValueError("SSE structured output accepts only an event type")
            event_type = event_type or "jax-event"
            wire_encoding = "sse-data"
        else:
            if http_status is not None or event_type is not None:
                raise ValueError("WebSocket structured output accepts only a frame type")
            # Browser clients receive a text frame whose contents are the
            # exact canonical UTF-8 JSON bytes. Event identity remains inside
            # that governed JSON, never in a caller-selected frame type.
            frame_type = frame_type or "text"
            if frame_type != "text":
                raise ValueError("WebSocket structured output requires text frame encoding")
            wire_encoding = "websocket-text"
        metadata = self._core.lifecycle.StructuredTransportMetadata(
            channel_id=channel_enum.value,
            media_type=binding.media_type,
            outcome=binding.outcome,
            http_status=http_status,
            frame_type=frame_type,
            event_type=event_type,
            wire_encoding=wire_encoding,
        )
        channel = self._core.external_output.CHANNELS[channel_enum]
        composed = external.GovernedExternalOutputAdapter(context, layout_registry, channel)
        unit = composed.prepare_structured(
            submission, layout=layout, metadata=metadata,
            idempotency_key=idempotency_key, now=now,
        )
        return PreparedStructuredOutput(
            transport_unit=unit,
            canonical_bytes=unit.rendered.canonical_bytes,
            value=unit.rendered.value,
        )
