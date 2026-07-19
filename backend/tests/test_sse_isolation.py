"""SSE /api/events lifecycle isolation tests.

Mirrors test_websocket_isolation.py's approach: drive the endpoint's actual
connect/disconnect helper coroutines directly (no real transport needed for
SSE — it's just an asyncio.Queue-backed callback), and force interleaving
deterministically with an asyncio.Event gate rather than relying on timing.

Two distinct bugs are covered here:

1. api/events.py's unsubscribe on disconnect was unconditional — it never
   checked whether another connection (SSE or WS) for the same user was
   still alive, so a second connection's subscription was always wiped out
   by the first one's teardown, deterministically (no interleaving needed).
2. Even with a "some connection still alive?" check in place, ws_hub and the
   SSE connection counter each guard their own state with their own lock, so
   a WS tab's disconnect+maybe-unsubscribe sequence can interleave with an
   SSE connection's connect+subscribe sequence for the same user: the SSE
   subscribe can land in the gap between the WS teardown's "any connections
   left?" check and its unsubscribe call, and then get wiped out by that
   unsubscribe. This is the cross-channel version of the WS-to-WS race
   closed in test_websocket_isolation.py::test_reconnect_race_does_not_lose_subscription.
"""
import asyncio

import main as main_module
from api.events import _sse_connect_and_subscribe, _sse_disconnect_and_maybe_unsubscribe
from jax_engine.events import event_bus
from jax_engine.schemas import JAXEvent
from jax_engine.websocket_hub import ws_hub

TENANT = "1"


class _FakeWebSocket:
    """Stand-in for a starlette WebSocket, matching test_websocket_isolation.py."""

    def __init__(self):
        self.application_state = type("_State", (), {"name": "CONNECTED"})()
        self.sent = []

    async def send_json(self, data):
        self.sent.append(data)


async def test_second_sse_connection_survives_first_disconnect():
    """Two SSE connections for the same user: closing the first must not
    silence the second (bug #1 above — was unconditional, deterministic)."""
    user_id = "sse-user-a"

    queue1: asyncio.Queue = asyncio.Queue()
    await _sse_connect_and_subscribe(user_id, TENANT, queue1.put)

    queue2: asyncio.Queue = asyncio.Queue()
    await _sse_connect_and_subscribe(user_id, TENANT, queue2.put)

    # First connection closes; the second is still open.
    await _sse_disconnect_and_maybe_unsubscribe(user_id)

    event = JAXEvent(
        event_type="facet_status_changed",
        tenant_id=TENANT,
        user_id=user_id,
        payload={"facet": "jax_local", "status": "idle"},
    )
    await event_bus.publish(event)

    assert not queue2.empty(), (
        "surviving SSE connection lost its event subscription when a "
        "sibling connection for the same user disconnected"
    )
    got = await queue2.get()
    assert got.event_id == event.event_id

    await _sse_disconnect_and_maybe_unsubscribe(user_id)


async def test_cross_channel_race_ws_disconnect_does_not_lose_sse_subscription(monkeypatch):
    """Regression: a WS tab disconnecting must not race an SSE connection
    starting up for the SAME user and wipe out the SSE subscription.

    Sequence forced deterministically:
      1. WS tab connects (event_bus subscribed to the WS callback).
      2. WS tab's teardown calls ws_hub.disconnect, then ws_hub.has_connections
         -> False (no other WS tabs), and pauses there.
      3. An SSE connection for the same user connects: subscribes its own
         callback, replacing the WS callback in event_bus's single slot.
      4. WS teardown resumes. Without the shared lock/counter, it would
         call event_bus.unsubscribe(user_id) unconditionally, wiping out the
         SSE subscription installed in step 3.
    """
    user_id = "cross-channel-user"

    pause = asyncio.Event()
    paused_signal = asyncio.Event()
    original_has_connections = ws_hub.has_connections

    async def gated_has_connections(uid):
        result = await original_has_connections(uid)
        if uid == user_id:
            paused_signal.set()
            await pause.wait()
        return result

    monkeypatch.setattr(ws_hub, "has_connections", gated_has_connections)

    wsA = _FakeWebSocket()
    connA = await main_module._ws_connect_and_subscribe(user_id, TENANT, "operator", wsA)

    task_ws_disconnect = asyncio.create_task(
        main_module._ws_disconnect_and_maybe_unsubscribe(user_id, connA)
    )
    await asyncio.wait_for(paused_signal.wait(), timeout=5)

    # task_ws_disconnect is paused while HOLDING lifecycle_lock, so the SSE
    # connect must run as its own task (it will block acquiring the lock)
    # rather than be awaited inline here — otherwise this coroutine itself
    # would deadlock before ever reaching pause.set() below.
    sse_queue: asyncio.Queue = asyncio.Queue()
    task_sse_connect = asyncio.create_task(
        _sse_connect_and_subscribe(user_id, TENANT, sse_queue.put)
    )
    for _ in range(5):
        await asyncio.sleep(0)

    pause.set()
    await asyncio.wait_for(task_sse_connect, timeout=5)
    await asyncio.wait_for(task_ws_disconnect, timeout=5)

    event = JAXEvent(
        event_type="facet_status_changed",
        tenant_id=TENANT,
        user_id=user_id,
        payload={"facet": "jax_local", "status": "idle"},
    )
    await event_bus.publish(event)

    assert not sse_queue.empty(), (
        "SSE connection lost its event subscription to a concurrent WS "
        "teardown for the same user"
    )
    got = await sse_queue.get()
    assert got.event_id == event.event_id

    await _sse_disconnect_and_maybe_unsubscribe(user_id)
