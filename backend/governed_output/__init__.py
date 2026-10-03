"""Server-owned F2-E external structured-output boundary."""

from .channels import PlatformDeliveryKind, channel_binding
from .core import StructuredCoreUnavailable, load_structured_core
from .adapter import PlatformGovernedExternalOutputAdapter, PreparedStructuredOutput
from .composition import compose_runtime_output, output_scope
from .health_event import GovernedHealthEventUnavailable, prepare_health_change_event
from .event_transport import GovernedEventUnavailable, prepare_native_event, unavailable_wire
from .transport import StructuredOutputOutboxRepository, StructuredTransportUnavailable, commit_structured_wire

__all__ = (
    "PlatformDeliveryKind",
    "PlatformGovernedExternalOutputAdapter",
    "PreparedStructuredOutput",
    "compose_runtime_output",
    "output_scope",
    "GovernedHealthEventUnavailable",
    "prepare_health_change_event",
    "GovernedEventUnavailable",
    "prepare_native_event",
    "unavailable_wire",
    "StructuredOutputOutboxRepository",
    "StructuredTransportUnavailable",
    "commit_structured_wire",
    "StructuredCoreUnavailable",
    "channel_binding",
    "load_structured_core",
)
