"""Office-only account classes never enter a classic handler or stream."""
import pytest

from auth.models import AuthUser
from tests.identidades import auth, sql, token_para


def _set_class(client, user_id: int, account_class: str):
    client.portal.call(sql, "UPDATE jax_users SET account_class=%s WHERE user_id=%s", (account_class, user_id))


def test_restricted_class_is_rejected_before_classic_admin_handler(client, usuarios, monkeypatch):
    from api.admin import users as users_mod

    target, _ = usuarios()
    actor, _ = usuarios(role="superadmin")
    _set_class(client, actor, "OFFICE_INTERNAL")
    reached = []

    async def forbidden(*args, **kwargs):
        reached.append(True)
        raise AssertionError("classic handler was reached")

    monkeypatch.setattr(users_mod, "_bloquear_tenant", forbidden)
    response = client.post(f"/api/admin/users/{target}/unlock", headers=auth(token_para(actor, role="superadmin")))
    assert (response.status_code, response.json()) == (403, {"detail": "office_surface_required"})
    assert reached == []


def test_restricted_pending_password_upload_stops_before_limiter_body_kill_or_quota(client, usuarios, monkeypatch):
    """The real app stack keeps CORS outermost and Office before upload work."""
    from adjuntos import limite_de_subidas
    from api import upload
    from main import app

    actor, _ = usuarios()
    client.portal.call(sql, "UPDATE jax_users SET account_class='OFFICE_INTERNAL', must_change_password=1 WHERE user_id=%s", (actor,))
    reached = []
    original_limiter = limite_de_subidas.LimiteDeSubidas.__call__

    async def limiter_spy(self, scope, receive, send):
        reached.append("limiter")
        return await original_limiter(self, scope, receive, send)

    async def forbidden_kill(*args, **kwargs):
        reached.append("kill")
        raise AssertionError("upload handler ran")

    monkeypatch.setattr(limite_de_subidas.LimiteDeSubidas, "__call__", limiter_spy)
    monkeypatch.setattr(upload, "exigir_mesa_libre", forbidden_kill)
    response = client.post("/api/chat/upload", headers=auth(token_para(actor)),
                           files={"file": ("blocked.txt", b"this body must remain unread", "text/plain")})
    assert (response.status_code, response.json()) == (403, {"detail": "office_surface_required"})
    assert reached == []
    # The installed order is a regression guard for aliases and future routes:
    # CORS outer -> Office barrier -> upload limiter -> JSON/body/handler.
    names = [entry.cls.__name__ for entry in app.user_middleware]
    assert names.index("CORSMiddleware") < names.index("OfficeSurfaceBarrierMiddleware") < names.index("LimiteDeSubidas")


def test_restricted_class_keeps_exact_auth_self_service_allowlist(client, usuarios):
    actor, _ = usuarios()
    _set_class(client, actor, "EXTERNAL_CLIENT")
    token = token_para(actor)
    allowed = client.get("/api/auth/me", headers=auth(token))
    assert allowed.status_code == 200
    assert allowed.json()["account_class"] == "EXTERNAL_CLIENT"
    # A look-alike path is not an allowlist entry and cannot become one by a
    # future router alias or encoded prefix.
    denied = client.get("/api/auth/me/", headers=auth(token))
    assert (denied.status_code, denied.json()) == (403, {"detail": "office_surface_required"})
    encoded = client.get("/api/%61dmin/users", headers=auth(token))
    assert (encoded.status_code, encoded.json()) == (403, {"detail": "office_surface_required"})


def test_restricted_class_websocket_closes_before_auth_ok(client, usuarios):
    from starlette.websockets import WebSocketDisconnect

    actor, _ = usuarios()
    _set_class(client, actor, "OFFICE_INTERNAL")
    with client.websocket_connect(f"/ws/{actor}") as ws:
        ws.send_json({"type": "auth", "token": token_para(actor)})
        try:
            ws.receive_json()
        except WebSocketDisconnect as exc:
            assert exc.code == 4001
        else:
            raise AssertionError("restricted account received auth_ok")


def test_ws_class_change_during_registration_emits_no_auth_ok(client, usuarios, monkeypatch):
    import main

    actor, _ = usuarios()
    real_register = main._ws_connect_and_subscribe

    async def change_class_then_register(user_id, *args):
        await sql("UPDATE jax_users SET account_class='OFFICE_INTERNAL' WHERE user_id=%s", (int(user_id),))
        return await real_register(user_id, *args)

    monkeypatch.setattr(main, "_ws_connect_and_subscribe", change_class_then_register)
    with client.websocket_connect(f"/ws/{actor}") as ws:
        ws.send_json({"type": "auth", "token": token_para(actor)})
        message = ws.receive()
        assert (message["type"], message.get("code")) == ("websocket.close", 4001)


def test_sse_delivery_rechecks_class_and_emits_no_event_after_change(client, usuarios):
    import asyncio
    from api import events as events_mod
    from auth.jwt import decode_token
    from auth.middleware import verificar_sesion
    from jax_engine.events import event_bus
    from jax_engine.schemas import JAXEvent

    actor, _ = usuarios()

    async def scenario():
        user = await verificar_sesion(decode_token(token_para(actor)), "access")
        response = await events_mod.sse_events(user)
        generator = response.body_iterator
        try:
            await sql("UPDATE jax_users SET account_class='EXTERNAL_CLIENT' WHERE user_id=%s", (actor,))
            await event_bus.publish(JAXEvent(event_type="heartbeat", tenant_id=user.tenant_id, user_id=user.user_id, payload={"ping": True}))
            try:
                await asyncio.wait_for(generator.__anext__(), timeout=1)
            except StopAsyncIteration:
                return "closed"
            raise AssertionError("SSE emitted after class changed")
        finally:
            await generator.aclose()
    assert client.portal.call(scenario) == "closed"


def test_sse_queued_event_is_rechecked_before_generator_yield(client, usuarios):
    """A class mutation after enqueue but before consumption leaks no SSE data."""
    import asyncio
    from api import events as events_mod
    from auth.jwt import decode_token
    from auth.middleware import verificar_sesion
    from jax_engine.events import event_bus
    from jax_engine.schemas import JAXEvent

    actor, _ = usuarios()

    async def scenario():
        user = await verificar_sesion(decode_token(token_para(actor)), "access")
        response = await events_mod.sse_events(user)
        generator = response.body_iterator
        try:
            # Callback sees an allowed INTERNAL account and queues the event.
            await event_bus.publish(JAXEvent(event_type="heartbeat", tenant_id=user.tenant_id,
                                              user_id=user.user_id, payload={"queued": True}))
            await sql("UPDATE jax_users SET account_class='OFFICE_INTERNAL' WHERE user_id=%s", (actor,))
            try:
                await asyncio.wait_for(generator.__anext__(), timeout=1)
            except StopAsyncIteration:
                return "closed"
            raise AssertionError("SSE yielded an event queued before the class transition")
        finally:
            await generator.aclose()
    assert client.portal.call(scenario) == "closed"


@pytest.mark.parametrize("infrastructure_failure", [False, True])
def test_sse_sibling_streams_close_together_after_callback_reverification_failure(client, usuarios, monkeypatch,
                                                                                   infrastructure_failure):
    """One EventBus callback must close both SSE siblings for the same user."""
    import asyncio
    from api import events as events_mod
    from auth.jwt import decode_token
    from auth.middleware import verificar_sesion
    from jax_engine.events import event_bus
    from jax_engine.schemas import JAXEvent
    from jax_engine.lifecycle import sse_connections

    actor, _ = usuarios()

    async def scenario():
        user = await verificar_sesion(decode_token(token_para(actor)), "access")
        first = await events_mod.sse_events(user)
        second = await events_mod.sse_events(user)
        first_generator, second_generator = first.body_iterator, second.body_iterator
        try:
            if infrastructure_failure:
                async def unavailable(_user):
                    raise RuntimeError("database unavailable")
                monkeypatch.setattr(events_mod, "reverificar_sesion", unavailable)
            else:
                await sql("UPDATE jax_users SET account_class='OFFICE_INTERNAL' WHERE user_id=%s", (actor,))
            await event_bus.publish(JAXEvent(event_type="heartbeat", tenant_id=user.tenant_id,
                                              user_id=user.user_id, payload={"must_not": "escape"}))
            with pytest.raises(StopAsyncIteration):
                await asyncio.wait_for(first_generator.__anext__(), timeout=1)
            with pytest.raises(StopAsyncIteration):
                await asyncio.wait_for(second_generator.__anext__(), timeout=1)
            assert not sse_connections.has_connections(user.user_id)
            assert user.user_id not in events_mod._streams
            assert user.user_id not in event_bus._subscribers.get(user.tenant_id, {})
        finally:
            await first_generator.aclose()
            await second_generator.aclose()
    client.portal.call(scenario)


@pytest.mark.parametrize("infrastructure_failure", [False, True])
def test_second_sse_post_registration_failure_closes_prior_sse_and_ws(client, usuarios, monkeypatch,
                                                                         infrastructure_failure):
    """The second registration replaces EventBus's callback but cannot strand siblings."""
    import asyncio
    import main
    from api import events as events_mod
    from auth.jwt import decode_token
    from auth.middleware import verificar_sesion
    from jax_engine.events import event_bus
    from jax_engine.lifecycle import sse_connections

    class Socket:
        def __init__(self): self.closed = []
        async def close(self, code): self.closed.append(code)
        async def send_json(self, value): raise AssertionError("no event may be sent")

    actor, _ = usuarios()

    async def scenario():
        user = await verificar_sesion(decode_token(token_para(actor)), "access")
        socket = Socket()
        connection_id = await main._ws_connect_and_subscribe(user.user_id, user.tenant_id, user.role, socket, user)
        first = await events_mod.sse_events(user)
        first_generator = first.body_iterator
        original_reverify = events_mod.reverificar_sesion
        try:
            if infrastructure_failure:
                async def fail_during_second_registration(_user):
                    raise RuntimeError("database unavailable")
                monkeypatch.setattr(events_mod, "reverificar_sesion", fail_during_second_registration)
            else:
                async def flip_during_second_registration(current_user):
                    await sql("UPDATE jax_users SET account_class='OFFICE_INTERNAL' WHERE user_id=%s", (actor,))
                    return await original_reverify(current_user)
                monkeypatch.setattr(events_mod, "reverificar_sesion", flip_during_second_registration)
            second = await events_mod.sse_events(user)
            second_generator = second.body_iterator
            try:
                assert socket.closed == [4001]
                with pytest.raises(StopAsyncIteration):
                    await asyncio.wait_for(first_generator.__anext__(), timeout=1)
                with pytest.raises(StopAsyncIteration):
                    await asyncio.wait_for(second_generator.__anext__(), timeout=1)
                assert not sse_connections.has_connections(user.user_id)
            finally:
                await second_generator.aclose()
        finally:
            await first_generator.aclose()
            await main._ws_disconnect_and_maybe_unsubscribe(user.user_id, connection_id)
        assert user.user_id not in events_mod._streams
        assert user.user_id not in event_bus._subscribers.get(user.tenant_id, {})
    client.portal.call(scenario)


def test_established_ws_heartbeat_class_change_closes_all_ws_and_sse(client, usuarios, monkeypatch):
    import asyncio
    import main
    from api import events as events_mod
    from auth.jwt import decode_token
    from auth.middleware import verificar_sesion

    class Socket:
        def __init__(self):
            self.closed = []
            self.sent = []
        async def close(self, code): self.closed.append(code)
        async def send_json(self, value): self.sent.append(value)

    actor, _ = usuarios()
    monkeypatch.setattr(main, "_HEARTBEAT_INTERVAL_SECONDS", 0)

    async def scenario():
        session = await verificar_sesion(decode_token(token_para(actor)), "access")
        first, second = Socket(), Socket()
        first_id = await main._ws_connect_and_subscribe(session.user_id, session.tenant_id, session.role, first, session)
        second_id = await main._ws_connect_and_subscribe(session.user_id, session.tenant_id, session.role, second, session)
        response = await events_mod.sse_events(session)
        generator = response.body_iterator
        try:
            await sql("UPDATE jax_users SET account_class='OFFICE_INTERNAL' WHERE user_id=%s", (actor,))
            await main._heartbeat(session.user_id, session.tenant_id, session)
            assert first.closed == [4001] and second.closed == [4001]
            with pytest.raises(StopAsyncIteration):
                await asyncio.wait_for(generator.__anext__(), timeout=1)
        finally:
            await generator.aclose()
            await main._ws_disconnect_and_maybe_unsubscribe(session.user_id, first_id)
            await main._ws_disconnect_and_maybe_unsubscribe(session.user_id, second_id)
    import pytest
    client.portal.call(scenario)


def test_session_lookup_keeps_primary_key_plan_after_account_class_added(client):
    from auth.middleware import SQL_ESTADO_DE_SESION

    async def explain():
        from db.connection import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("EXPLAIN " + SQL_ESTADO_DE_SESION, (1,))
                columns = [column[0] for column in cur.description]
                return [dict(zip(columns, row)) for row in await cur.fetchall()]
    (plan,) = client.portal.call(explain)
    assert (plan["type"], plan["key"], plan["Extra"] or "") == ("const", "PRIMARY", ""), plan


def test_unknown_database_class_fails_closed_in_session_verification(client, usuarios, monkeypatch):
    import auth.middleware as middleware
    from fastapi import HTTPException

    actor, _ = usuarios()

    class Cursor:
        async def execute(self, *args):
            return None
        async def fetchone(self):
            return ("active", "operator", 0, "a@example.invalid", False, 1, "UNKNOWN")
    class Connection:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return None
        def cursor(self):
            class Context:
                async def __aenter__(self): return Cursor()
                async def __aexit__(self, *args): return None
            return Context()
    class Pool:
        def acquire(self): return Connection()
    async def pool(): return Pool()
    monkeypatch.setattr(middleware, "get_pool", pool)

    async def scenario():
        try:
            await middleware.verificar_sesion({"type": "access", "user_id": str(actor), "tv": 0, "tenant_id": "1"}, "access")
        except HTTPException as exc:
            return exc.status_code, exc.detail
        raise AssertionError("unknown class was accepted")
    assert client.portal.call(scenario) == (401, "sesion_invalida")


@pytest.mark.parametrize("user", [
    AuthUser(user_id="1", tenant_id="1", role="operator"),
    type("UnknownClassUser", (), {"user_id": "2", "tenant_id": "1", "account_class": "UNKNOWN"})(),
])
def test_direct_stream_without_canonical_class_never_subscribes(client, monkeypatch, user):
    from fastapi import HTTPException
    from api import events as events_mod

    subscribed = []

    async def forbid(*args, **kwargs):
        subscribed.append(True)

    monkeypatch.setattr(events_mod.event_bus, "subscribe", forbid)

    async def scenario():
        with pytest.raises(HTTPException, match="office_surface_required"):
            await events_mod.sse_events(user)
    client.portal.call(scenario)
    assert subscribed == []
