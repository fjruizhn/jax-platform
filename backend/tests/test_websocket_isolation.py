"""WebSocket per-user isolation tests (Bug #1).

Project rule (CLAUDE.md #3): "WS canal por usuario — nunca por tenant".

These tests drive real WebSocket connections through Starlette's TestClient.
The `client` fixture (conftest.py) enters TestClient as a context manager, so
every websocket session it opens shares ONE anyio blocking portal / event loop.
That lets us publish an event ON that loop with `client.portal.call(...)`, which
blocks until `EventBus.publish` has fully awaited every subscriber callback — so
by the time it returns, any message destined for a socket is already buffered in
that session's receive stream. That makes the "nothing was delivered" assertion
deterministic (inspect the buffer) instead of racy (sleep-and-hope).
"""
import asyncio

import main as main_module
from auth.jwt import create_access_token
from jax_engine.events import event_bus
from jax_engine.schemas import JAXEvent
from jax_engine.websocket_hub import ws_hub

TENANT = "1"


class _FakeWebSocket:
    """Stand-in for a starlette WebSocket, for exercising main.py's
    connect/disconnect orchestration without a real transport.

    ws_hub.connect() skips calling .accept() when application_state is
    already "CONNECTED"; send_json just needs to be awaitable.
    """

    def __init__(self):
        self.application_state = type("_State", (), {"name": "CONNECTED"})()
        self.sent = []

    async def send_json(self, data):
        self.sent.append(data)


def _handshake(sock, user_id):
    """Complete the WS auth handshake; leaves the receive buffer drained."""
    token = create_access_token(user_id, TENANT, "operator")
    sock.send_json({"type": "auth", "token": token})
    assert sock.receive_json() == {"type": "auth_ok"}


def _publish(client, event):
    """Run EventBus.publish on the app's event loop and block until complete."""
    client.portal.call(event_bus.publish, event)


def _buffered(sock) -> int:
    """How many messages are already waiting in this socket's receive stream.

    Valid to read only right after a synchronous _publish(): publish awaits all
    sends before returning, so the buffer is settled (no in-flight async work).
    """
    return sock._send_rx.statistics().current_buffer_used


def test_event_reaches_only_addressed_user(client):
    """An event addressed to user A must not leak to user B in the same tenant."""
    with client.websocket_connect("/ws/test-user-a") as ws_a, \
         client.websocket_connect("/ws/test-user-b") as ws_b:
        _handshake(ws_a, "test-user-a")
        _handshake(ws_b, "test-user-b")

        event = JAXEvent(
            event_type="facet_status_changed",
            tenant_id=TENANT,
            user_id="test-user-a",
            payload={"facet": "jax_local", "status": "thinking"},
        )
        _publish(client, event)

        got = ws_a.receive_json()
        assert got["event_id"] == event.event_id
        assert got["user_id"] == "test-user-a"

        # B is in the same tenant but is NOT the addressee -> must get nothing.
        assert _buffered(ws_b) == 0


def test_multi_tab_second_connection_survives_first_close(client):
    """Two tabs for the same user: closing one must not silence the other."""
    ws1 = client.websocket_connect("/ws/test-user-a")
    sock1 = ws1.__enter__()
    _handshake(sock1, "test-user-a")

    ws2 = client.websocket_connect("/ws/test-user-a")
    sock2 = ws2.__enter__()
    _handshake(sock2, "test-user-a")

    # Close the first tab. The second tab's socket stays open.
    ws1.__exit__(None, None, None)

    try:
        event = JAXEvent(
            event_type="facet_status_changed",
            tenant_id=TENANT,
            user_id="test-user-a",
            payload={"facet": "jax_local", "status": "idle"},
        )
        _publish(client, event)

        # Check the buffer before receiving: on the buggy code nothing is
        # delivered to sock2, so a blind receive_json() would hang forever.
        # Exactly 1 (not >=1): guards against a regression to a per-connection
        # EventBus callback structure, which would deliver this event twice.
        assert _buffered(sock2) == 1, (
            f"expected exactly 1 event delivered to surviving tab, got {_buffered(sock2)}"
        )
        got = sock2.receive_json()
        assert got["event_id"] == event.event_id
    finally:
        ws2.__exit__(None, None, None)


async def test_reconnect_race_does_not_lose_subscription(monkeypatch):
    """Regression: a tab reconnecting while a sibling tab is mid-teardown
    must not lose its event subscription (final-review finding on the WS
    isolation fix).

    main.py's WS endpoint drives two sequences through separate locks
    (ws_hub's and event_bus's), so a disconnecting tab A and a reconnecting
    tab B for the SAME user can interleave:
      1. Tab A's teardown calls ws_hub.disconnect(connA).
      2. Tab A's teardown calls ws_hub.has_connections(user) -> False.
      3. Tab B reconnects: ws_hub.connect + event_bus.subscribe.
      4. Tab A's teardown resumes: event_bus.unsubscribe(user).

    Step 4 wipes out the subscription step 3 just installed. Tab B's socket
    stays registered in ws_hub (looks alive, still gets heartbeats which
    bypass the bus) but silently stops receiving facet/chat/command/pipeline
    events published via the bus.

    Drives main.py's actual connect/disconnect coroutines directly (fake
    sockets, no real transport) instead of two overlapping WS connections,
    so the interleaving is forced deterministically via an asyncio.Event
    gate rather than relying on timing.
    """
    user_id = "race-user"
    tenant_id = TENANT

    pause = asyncio.Event()
    paused_signal = asyncio.Event()
    original_has_connections = ws_hub.has_connections

    async def gated_has_connections(uid):
        # Pauses tab A's teardown right after step 2 resolves (to False,
        # matching the real race -- computed before tab B ever connects),
        # so the test can deterministically drive step 3 before letting
        # step 4 run.
        result = await original_has_connections(uid)
        if uid == user_id:
            paused_signal.set()
            await pause.wait()
        return result

    monkeypatch.setattr(ws_hub, "has_connections", gated_has_connections)

    wsA = _FakeWebSocket()
    connA = await main_module._ws_connect_and_subscribe(user_id, tenant_id, "operator", wsA)

    task_a = asyncio.create_task(
        main_module._ws_disconnect_and_maybe_unsubscribe(user_id, connA)
    )
    await asyncio.wait_for(paused_signal.wait(), timeout=5)

    wsB = _FakeWebSocket()
    task_b = asyncio.create_task(
        main_module._ws_connect_and_subscribe(user_id, tenant_id, "operator", wsB)
    )
    # Let the loop run task_b as far as it currently can (fully, if nothing
    # serializes it against task_a; blocked on a shared lock otherwise).
    for _ in range(5):
        await asyncio.sleep(0)

    pause.set()

    connB = await asyncio.wait_for(task_b, timeout=5)
    await asyncio.wait_for(task_a, timeout=5)

    event = JAXEvent(
        event_type="facet_status_changed",
        tenant_id=tenant_id,
        user_id=user_id,
        payload={"facet": "jax_local", "status": "idle"},
    )
    await event_bus.publish(event)

    assert wsB.sent, (
        "reconnecting tab lost its event subscription to a concurrent "
        "teardown of the tab it replaced"
    )
    assert wsB.sent[-1]["event_id"] == event.event_id

    await main_module._ws_disconnect_and_maybe_unsubscribe(user_id, connB)
