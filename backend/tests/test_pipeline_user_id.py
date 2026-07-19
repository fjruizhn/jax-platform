"""Pipeline poller must publish events tagged with the pipeline's real owning
user_id, not the hardcoded "1" placeholder.

api/pipelines.py's create_pipeline already threads the real user_id into the
initial PipelineState (user.user_id, at creation time). But
_parse_jacobs_pipeline rebuilds PipelineState from Jacobs API responses on
every poll tick and, before this fix, never carried that user_id forward the
same way it already preserves existing.tenant_id/existing.created_at -- so by
the time _poll_pipelines ran, the real owning user_id was gone and the
poller substituted the literal string "1" when publishing events. Any user
whose id isn't literally "1" never received their own pipeline's
status-change or human-gate events over their WS/SSE connection (EventBus
routes strictly by user_id, see jax_engine/events.py).
"""
import asyncio

from jax_engine.events import event_bus
from jax_engine.schemas import JAXEvent, PipelineState
from jax_engine.state import JAXEngineState

REAL_USER_ID = "real-owning-user-42"
TENANT_ID = "tenant-7"


class _FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, response):
        self._response = response

    async def get(self, url, *args, **kwargs):
        return self._response


def _make_state_with_pipeline(pid: str, status: str = "running") -> JAXEngineState:
    state = JAXEngineState()
    state._state.active_pipelines[pid] = PipelineState(
        pipeline_id=pid,
        tenant_id=TENANT_ID,
        user_id=REAL_USER_ID,
        name="Test Pipeline",
        status=status,
    )
    return state


def test_parse_jacobs_pipeline_preserves_real_user_id():
    """_parse_jacobs_pipeline must carry existing.user_id forward, the same
    way it already preserves existing.tenant_id and existing.created_at."""
    state = _make_state_with_pipeline("pid-1")
    existing = state._state.active_pipelines["pid-1"]

    updated = state._parse_jacobs_pipeline(
        "pid-1",
        existing,
        {"pipeline": {"status": "running"}, "steps": []},
    )

    assert updated.user_id == REAL_USER_ID, (
        f"expected pipeline's real owning user_id to survive a poll-tick "
        f"rebuild, got {updated.user_id!r}"
    )


async def test_poll_one_pipeline_publishes_events_with_real_user_id():
    """A poll tick that detects a status change must publish both the
    pipeline_step_changed event (via upsert_pipeline) and, when the status
    transitions into waiting_gate, the human_gate_requested event, addressed
    to the pipeline's real owning user_id -- not the hardcoded "1"."""
    pid = "pid-2"
    state = _make_state_with_pipeline(pid, status="running")
    pipeline = state._state.active_pipelines[pid]

    captured: list[JAXEvent] = []

    async def capture(event: JAXEvent):
        captured.append(event)

    await event_bus.subscribe(TENANT_ID, REAL_USER_ID, capture)
    try:
        client = _FakeClient(_FakeResponse(200, {
            "pipeline": {"status": "interrupted"},  # maps to waiting_gate
            "steps": [],
        }))
        await state._poll_one_pipeline(client, pid, pipeline)
    finally:
        await event_bus.unsubscribe(REAL_USER_ID)

    event_types = {e.event_type for e in captured}
    assert "pipeline_step_changed" in event_types, (
        f"expected a pipeline_step_changed event, got types {event_types}"
    )
    assert "human_gate_requested" in event_types, (
        f"expected a human_gate_requested event on the waiting_gate "
        f"transition, got types {event_types}"
    )
    for event in captured:
        assert event.user_id == REAL_USER_ID, (
            f"{event.event_type} event was addressed to user_id "
            f"{event.user_id!r} instead of the pipeline's real owning "
            f"user {REAL_USER_ID!r} -- that user's WS/SSE connection would "
            f"never receive it (EventBus routes strictly by user_id)"
        )
