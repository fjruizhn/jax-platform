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
from auth.jwt import create_access_token
from jax_engine.events import event_bus
from jax_engine.schemas import JAXEvent

TENANT = "1"


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
        assert _buffered(sock2) >= 1, "surviving tab received no event after sibling closed"
        got = sock2.receive_json()
        assert got["event_id"] == event.event_id
    finally:
        ws2.__exit__(None, None, None)
