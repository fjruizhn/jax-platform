"""Platform mappings onto the JAX-owned closed F2-E channel registry.

The identities themselves live in the paired JAX core.  This module may map a
known Platform delivery mechanism to one of those identities, but cannot add
one or accept a caller-selected name.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class PlatformDeliveryKind(str, Enum):
    HTTP_JSON = "HTTP_JSON"
    SSE_JSON = "SSE_JSON"
    WEBSOCKET_JSON = "WEBSOCKET_JSON"


@dataclass(frozen=True)
class PlatformChannelBinding:
    """A delivery mechanism whose channel enum is obtained from paired JAX."""

    delivery: PlatformDeliveryKind
    core_channel_name: str
    media_type: str
    outcome: str


_BINDINGS = {
    PlatformDeliveryKind.HTTP_JSON: PlatformChannelBinding(
        PlatformDeliveryKind.HTTP_JSON, "PLATFORM_HTTP_JSON_V1", "application/json", "HTTP_RESPONSE"),
    PlatformDeliveryKind.SSE_JSON: PlatformChannelBinding(
        PlatformDeliveryKind.SSE_JSON, "PLATFORM_SSE_JSON_V1", "text/event-stream", "SSE_EVENT"),
    PlatformDeliveryKind.WEBSOCKET_JSON: PlatformChannelBinding(
        PlatformDeliveryKind.WEBSOCKET_JSON, "PLATFORM_WEBSOCKET_JSON_V1", "application/json", "WEBSOCKET_FRAME"),
}


def channel_binding(delivery: PlatformDeliveryKind) -> PlatformChannelBinding:
    if not isinstance(delivery, PlatformDeliveryKind):
        raise TypeError("platform delivery kind must be server-owned")
    return _BINDINGS[delivery]
