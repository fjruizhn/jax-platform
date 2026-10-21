"""Office-only account classes never enter a classic handler or stream."""
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
