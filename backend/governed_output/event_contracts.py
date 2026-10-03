"""Closed server-owned contracts for Platform dynamic event frames.

This registry is declarative: it never derives a claim from an event DTO.  The
route adapter must obtain every listed runtime claim through F2-B before it
can render the matching layout.
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum


class EventContractId(str, Enum):
    FACET_STATUS = "platform-event-facet-status.v1"
    PIPELINE_STATE = "platform-event-pipeline-state.v1"
    HEALTH_CHANGE = "platform-event-health-change.v1"
    SYSTEM_EVENT = "platform-event-system.v1"


@dataclass(frozen=True)
class EventContract:
    event_types: frozenset[str]
    contract_id: EventContractId
    # Tuple values are predicate and fixed JSON pointer; argument identities
    # are bound only by RuntimeSlotContract in server composition.
    runtime_slots: tuple[tuple[str, str], ...]
    # Pointer-pattern origins are passed verbatim to the versioned F2-C layout.
    origins: tuple[tuple[str, str, str], ...]


EVENT_CONTRACTS: tuple[EventContract, ...] = (
    EventContract(
        frozenset({"facet_status_changed"}), EventContractId.FACET_STATUS,
        (("FACET_RUNTIME_STATUS", "/payload/status"),),
        (("", "SYSTEM", "platform:facet-state"), ("/payload/message", "USER", "event:operator-input")),
    ),
    EventContract(
        frozenset({"pipeline_step_changed", "pipeline_continued"}), EventContractId.PIPELINE_STATE,
        (("PIPELINE_STATUS", "/payload/status"),),
        (("", "SYSTEM", "platform:pipeline-projection"), ("/payload/steps/*/output", "TOOL", "pipeline:step-output")),
    ),
    EventContract(
        frozenset({"las_manos_health_changed"}), EventContractId.HEALTH_CHANGE,
        (("ENGINE_STATUS", "/payload/alive"),),
        (("", "SYSTEM", "platform:health-probe"),),
    ),
    EventContract(
        frozenset({"human_gate_requested", "kill_switch_activated", "kill_switch_released", "facet_response_completed"}),
        EventContractId.SYSTEM_EVENT, (), (("", "SYSTEM", "platform:event"),),
    ),
)


def event_contract(event_type: str) -> EventContract:
    if not isinstance(event_type, str):
        raise TypeError("event type must be server schema value")
    matches = [contract for contract in EVENT_CONTRACTS if event_type in contract.event_types]
    if len(matches) != 1:
        raise ValueError("event type has no closed governed contract")
    return matches[0]
