"""_poll_las_manos must broadcast las_manos_health_changed to every connected
user, not just the hardcoded tenant/user "1".

Before this fix, a single health-check tick that flips las_manos_alive
published exactly one JAXEvent addressed to tenant_id="1"/user_id="1" --
literal placeholders, not real connected users. Any other user only found
out LAS MANOS' status on their next loadState() refresh, never in real
time (EventBus routes strictly by user_id, see jax_engine/events.py).
"""
from jax_engine.events import event_bus
from jax_engine.schemas import JAXEvent
from jax_engine.state import JAXEngineState

USER_A = "user-a"
TENANT_A = "tenant-a"
USER_B = "user-b"
TENANT_B = "tenant-b"


class _FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code


class _FakeClient:
    def __init__(self, status_code):
        self._status_code = status_code

    async def get(self, url, *args, **kwargs):
        return _FakeResponse(self._status_code)


def _make_state_with_two_users() -> JAXEngineState:
    state = JAXEngineState()
    state.register_user(USER_A, TENANT_A, "operator")
    state.register_user(USER_B, TENANT_B, "operator")
    return state


async def test_health_change_reaches_all_connected_users():
    state = _make_state_with_two_users()

    captured: list[JAXEvent] = []

    async def capture(event: JAXEvent):
        captured.append(event)

    await event_bus.subscribe(TENANT_A, USER_A, capture)
    await event_bus.subscribe(TENANT_B, USER_B, capture)
    try:
        client = _FakeClient(200)  # alive flips False -> True
        await state._check_las_manos_health(client)
    finally:
        await event_bus.unsubscribe(USER_A)
        await event_bus.unsubscribe(USER_B)

    recipients = {(e.tenant_id, e.user_id) for e in captured}
    assert recipients == {(TENANT_A, USER_A), (TENANT_B, USER_B)}, (
        f"expected both connected users to receive their own "
        f"las_manos_health_changed event, got {recipients}"
    )
    assert all(e.event_type == "las_manos_health_changed" for e in captured)


async def test_health_change_with_zero_connected_users_does_not_raise():
    state = JAXEngineState()
    client = _FakeClient(200)

    await state._check_las_manos_health(client)

    assert state._state.las_manos_alive is True
