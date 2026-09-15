"""Invariantes y guardas de administración de usuarios (2026-09-12, etapa 3).

Antes: update_user no tenía guardas (se podía degradar o desactivar al último
superadmin, o a uno mismo) y la única protección era `user_id == 1` literal
en delete_user (spec §1, hallazgo 2). Ahora: siempre al menos un superadmin
activo (409), nadie actúa sobre sí mismo (403), y el literal desaparece.

user_id=1 aparece SOLO como actor. Para "el último superadmin" se reemplaza
otros_superadmins_activos: en jax_memory_test user 1 siempre es un
superadmin activo, así que el caso real no se puede armar sin tocarlo. El
conteo en sí se prueba aparte, contra la base.

Step 4b (enmienda M-1 de la etapa 2, Ruling U5): verificar_sesion corta el WS
y el SSE solo al conectar. Cuando cambian rol o estado (o se borra la fila),
las conexiones YA abiertas del usuario se cierran después del commit: el WS con
4001 (ws_hub.close_user) y el stream SSE terminando su generador
(api.events.close_user_streams).
"""
import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from api import events as events_mod
from api.admin import users as users_mod
from jax_engine.lifecycle import sse_connections
from jax_engine.websocket_hub import WebSocketHub, ws_hub
from tests.identidades import auth, sql, token_para
from tiempo import utc_ahora


def _admin():
    return auth(token_para(1, role="superadmin"))


def _put(client, target, cabeceras=None, **cuerpo):
    return client.put(f"/api/admin/users/{target}", json=cuerpo, headers=cabeceras or _admin())


async def _fila(user_id):
    filas = await sql("SELECT role, status, token_version FROM jax_users WHERE user_id = %s", (user_id,), True)
    return tuple(filas[0]) if filas else None


async def _auditoria(target):
    filas = await sql("SELECT actor_user_id, action, detail, ip FROM user_admin_audit "
                      "WHERE target_user_id = %s ORDER BY id", (target,), True)
    return [tuple(f) for f in filas]


async def _ninguno(cur, excluido):
    return 0


# ---------------------------------------------------------------- puros

def test_nadie_actua_sobre_si_mismo():
    with pytest.raises(HTTPException) as exc:
        users_mod.guarda_auto_accion(7, 7)
    assert (exc.value.status_code, exc.value.detail) == (403, "auto_accion_prohibida")
    users_mod.guarda_auto_accion(7, 8)


def test_que_cambios_le_quitan_un_superadmin_activo_al_sistema():
    p = users_mod.pierde_superadmin_activo
    assert p("superadmin", "active", "operator", "active")
    assert p("superadmin", "active", "superadmin", "inactive")
    assert p("superadmin", "active", "superadmin", "deleted")
    assert not p("superadmin", "active", "superadmin", "active")
    assert not p("superadmin", "inactive", "operator", "inactive")
    assert not p("operator", "active", "superadmin", "active")


# ------------------------------------------------------------------- PUT

def test_un_superadmin_no_puede_degradarse_a_si_mismo(client, usuarios):
    s, _ = usuarios(role="superadmin")
    r = _put(client, s, auth(token_para(s, role="superadmin")), role="operator")
    assert (r.status_code, r.json()["detail"]) == (403, "auto_accion_prohibida")
    assert client.portal.call(_fila, s) == ("superadmin", "active", 0)


def test_nadie_puede_desactivarse_a_si_mismo(client, usuarios):
    s, _ = usuarios(role="superadmin")
    r = _put(client, s, auth(token_para(s, role="superadmin")), status="inactive")
    assert (r.status_code, r.json()["detail"]) == (403, "auto_accion_prohibida")
    assert client.portal.call(_fila, s) == ("superadmin", "active", 0)


def test_no_se_degrada_al_ultimo_superadmin_activo(client, usuarios, monkeypatch):
    s, _ = usuarios(role="superadmin")
    monkeypatch.setattr(users_mod, "otros_superadmins_activos", _ninguno)
    r = _put(client, s, role="operator")
    assert (r.status_code, r.json()["detail"]) == (409, "ultimo_superadmin")
    r = _put(client, s, status="inactive")
    assert (r.status_code, r.json()["detail"]) == (409, "ultimo_superadmin")
    assert client.portal.call(_fila, s) == ("superadmin", "active", 0)
    assert client.portal.call(_auditoria, s) == [], "la transacción revierte: ni cambio ni registro"


def test_degradar_con_otro_superadmin_cambia_sube_la_version_y_audita(client, usuarios, monkeypatch):
    from auth import rate_limit
    monkeypatch.setattr(rate_limit, "TRUSTED_PROXIES", frozenset({"testclient"}))
    s, _ = usuarios(role="superadmin")
    r = _put(client, s, {**_admin(), "X-Real-IP": "203.0.113.9"}, role="operator")
    assert r.status_code == 200, r.text
    assert client.portal.call(_fila, s) == ("operator", "active", 1)
    ((actor, accion, detalle, ip),) = client.portal.call(_auditoria, s)
    assert (actor, accion, json.loads(detalle), ip) == (1, "update_role", {"from": "superadmin", "to": "operator"}, "203.0.113.9")


def test_cambio_de_estado_sube_la_version_y_audita(client, usuarios):
    o, _ = usuarios()
    assert _put(client, o, status="inactive").status_code == 200
    assert client.portal.call(_fila, o) == ("operator", "inactive", 1)
    assert [a[1] for a in client.portal.call(_auditoria, o)] == ["update_status"]


def test_sin_cambios_no_sube_la_version_ni_audita(client, usuarios):
    o, _ = usuarios()
    assert _put(client, o, role="operator", status="active").status_code == 200
    assert client.portal.call(_fila, o) == ("operator", "active", 0)
    assert client.portal.call(_auditoria, o) == []


def test_valores_invalidos_y_campos_desconocidos(client, usuarios):
    o, _ = usuarios()
    assert _put(client, o, role="dios").json()["detail"] == "rol_invalido"
    assert _put(client, o, status="deleted").json()["detail"] == "estado_invalido"
    # La contraseña ya no se cambia por acá (Mi cuenta / enlace, etapa 4).
    assert _put(client, o, password="una-clave-cualquiera").status_code == 422
    assert _put(client, 10**9, role="viewer").json()["detail"] == "usuario_no_encontrado"
    assert client.portal.call(_fila, o) == ("operator", "active", 0)


def test_cuenta_los_otros_superadmins_activos(client, usuarios):
    a, _ = usuarios(role="superadmin")

    async def contar(excluido):
        from db.connection import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                return await users_mod.otros_superadmins_activos(cur, excluido)

    base = client.portal.call(contar, a)
    usuarios(role="superadmin", status="inactive")
    usuarios(role="operator")
    assert client.portal.call(contar, a) == base
    usuarios(role="superadmin")
    assert client.portal.call(contar, a) == base + 1
    ((independiente,),) = client.portal.call(
        sql, "SELECT COUNT(*) FROM jax_users WHERE role = 'superadmin' AND status = 'active' AND user_id <> %s",
        (a,), True)
    assert client.portal.call(contar, a) == independiente


# ---------------------------------------------------------------- DELETE

def test_delete_usa_las_guardas_y_no_el_literal_user_id_1(client, usuarios, monkeypatch):
    s, _ = usuarios(role="superadmin")
    r = client.delete(f"/api/admin/users/{s}", headers=auth(token_para(s, role="superadmin")))
    assert (r.status_code, r.json()["detail"]) == (403, "auto_accion_prohibida")
    monkeypatch.setattr(users_mod, "otros_superadmins_activos", _ninguno)
    r = client.delete(f"/api/admin/users/{s}", headers=_admin())
    assert (r.status_code, r.json()["detail"]) == (409, "ultimo_superadmin")
    assert client.portal.call(_fila, s) is not None


# ------------------------------------------- Step 4b: el hub cierra el WS

class _SocketQueCierra:
    """Stand-in de WebSocket que registra el cierre (y si el lock del hub
    estaba tomado en ese momento)."""

    def __init__(self, hub, falla=False):
        self.application_state = type("_State", (), {"name": "CONNECTED"})()
        self._hub, self._falla = hub, falla
        self.cerrado_con = None
        self.lock_tomado_al_cerrar = None

    async def close(self, code=1000):
        self.lock_tomado_al_cerrar = self._hub._lock.locked()
        if self._falla:
            raise RuntimeError("el socket ya estaba cerrado")
        self.cerrado_con = code


async def test_close_user_cierra_todas_las_conexiones_del_usuario_con_4001_y_no_las_de_otro():
    hub = WebSocketHub()
    a1, a2, b = _SocketQueCierra(hub), _SocketQueCierra(hub), _SocketQueCierra(hub)
    await hub.connect("u-a", a1)
    await hub.connect("u-a", a2)
    await hub.connect("u-b", b)
    assert await hub.close_user("u-a") == 2
    assert (a1.cerrado_con, a2.cerrado_con, b.cerrado_con) == (4001, 4001, None)
    # El hub NO borra las entradas: eso lo hace el `finally` del endpoint
    # (_ws_disconnect_and_maybe_unsubscribe, bajo lifecycle_lock), que además
    # decide unregister_user/unsubscribe.
    assert await hub.has_connections("u-a")


async def test_close_user_cierra_fuera_del_lock_y_un_fallo_no_frena_a_las_demas():
    hub = WebSocketHub()
    rota, sana = _SocketQueCierra(hub, falla=True), _SocketQueCierra(hub)
    await hub.connect("u-a", rota)
    await hub.connect("u-a", sana)
    assert await hub.close_user("u-a") == 1
    assert sana.cerrado_con == 4001
    assert (rota.lock_tomado_al_cerrar, sana.lock_tomado_al_cerrar) == (False, False)


async def test_close_user_de_un_usuario_sin_conexiones_no_hace_nada():
    assert await WebSocketHub().close_user("nadie") == 0


# ---------------------------------------- Step 4b: se corta el stream SSE

class _Usuario:
    def __init__(self, user_id):
        self.user_id, self.tenant_id = user_id, "1"


async def test_close_user_streams_termina_el_stream_sse_del_usuario_y_no_el_de_otro(monkeypatch):
    # Sin base: la re-verificación posterior al registro (m1) se da por buena;
    # lo que se prueba acá es close_user_streams.
    async def _vigente(user):
        return user

    monkeypatch.setattr(events_mod, "reverificar_sesion", _vigente)
    resp_a = await events_mod.sse_events(_Usuario("sse-corte-a"))
    resp_b = await events_mod.sse_events(_Usuario("sse-corte-b"))
    gen_a, gen_b = resp_a.body_iterator, resp_b.body_iterator
    pendiente_b = asyncio.ensure_future(gen_b.__anext__())
    try:
        assert await events_mod.close_user_streams("sse-corte-a") == 1
        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(gen_a.__anext__(), timeout=2)
        # El finally del generador bajó el contador (y soltó la suscripción).
        assert not sse_connections.has_connections("sse-corte-a")
        await asyncio.sleep(0)
        assert not pendiente_b.done(), "el stream de otro usuario sigue abierto"
        assert sse_connections.has_connections("sse-corte-b")
    finally:
        pendiente_b.cancel()
        await asyncio.gather(pendiente_b, return_exceptions=True)
        await gen_b.aclose()
    assert not sse_connections.has_connections("sse-corte-b")
    assert await events_mod.close_user_streams("sse-corte-a") == 0


# ------------------------- Step 4b: los endpoints cortan DESPUÉS del commit

@pytest.fixture
def cortes(monkeypatch):
    """Registra cada corte con la fila que ve OTRA conexión en ese momento:
    si el corte corriera dentro de la transacción, esa fila todavía tendría
    los valores viejos."""
    registro = []

    async def ws(user_id, code=4001):
        registro.append(("ws", user_id, await _fila(int(user_id))))
        return 0

    async def sse(user_id):
        registro.append(("sse", user_id, await _fila(int(user_id))))
        return 0

    monkeypatch.setattr(ws_hub, "close_user", ws)
    monkeypatch.setattr(users_mod, "close_user_streams", sse)
    return registro


def test_put_que_cambia_rol_corta_ws_y_sse_despues_del_commit(client, usuarios, cortes):
    o, _ = usuarios()
    assert _put(client, o, role="viewer").status_code == 200
    assert cortes == [("ws", str(o), ("viewer", "active", 1)), ("sse", str(o), ("viewer", "active", 1))]


def test_put_rechazado_o_sin_cambios_no_corta_nada(client, usuarios, cortes, monkeypatch):
    o, _ = usuarios()
    s, _ = usuarios(role="superadmin")
    assert _put(client, o, role="operator").status_code == 200            # sin cambios
    assert _put(client, 10**9, role="viewer").status_code == 404
    monkeypatch.setattr(users_mod, "otros_superadmins_activos", _ninguno)
    assert _put(client, s, role="operator").status_code == 409
    assert cortes == []


def test_delete_corta_despues_del_commit_y_no_si_la_guarda_responde(client, usuarios, cortes, monkeypatch):
    o, _ = usuarios()
    assert client.delete(f"/api/admin/users/{o}", headers=_admin()).status_code == 200
    assert cortes == [("ws", str(o), None), ("sse", str(o), None)], "la fila ya no existe al cortar"
    cortes.clear()
    assert client.delete(f"/api/admin/users/{10**9}", headers=_admin()).status_code == 404
    s, _ = usuarios(role="superadmin")
    monkeypatch.setattr(users_mod, "otros_superadmins_activos", _ninguno)
    assert client.delete(f"/api/admin/users/{s}", headers=_admin()).status_code == 409
    assert cortes == []


def test_una_pestana_ws_abierta_se_cierra_con_4001_al_desactivar_al_usuario(client, usuarios):
    """De punta a punta, con el hub real: la pestaña ya autenticada recibe el
    cierre 4001 en cuanto el admin desactiva a su dueño."""
    o, _ = usuarios()
    with client.websocket_connect(f"/ws/{o}") as sock:
        sock.send_json({"type": "auth", "token": token_para(o)})
        assert sock.receive_json() == {"type": "auth_ok"}
        assert _put(client, o, status="inactive").status_code == 200
        # Puede haber eventos ya encolados para la pestaña; lo que importa es
        # que el siguiente frame de control sea el cierre 4001.
        mensaje = sock.receive()
        while mensaje["type"] == "websocket.send":
            mensaje = sock.receive()
        assert (mensaje["type"], mensaje.get("code")) == ("websocket.close", 4001)


# ------------- m1 (revisión final): el corte cae entre verificar y registrar
#
# Antes: WS y SSE verificaban la sesión y DESPUÉS se registraban. Si el commit
# del admin (sube token_version) y su corte caían en ese hueco, el corte no
# encontraba la conexión y ésta quedaba registrada con una sesión ya inválida.
# Ahora se re-verifica después de registrar. Los tests fuerzan el hueco: el
# paso de registro sube la versión y corre el corte antes de registrar.

async def _subir_version(user_id):
    await sql("UPDATE jax_users SET token_version = token_version + 1 WHERE user_id = %s", (int(user_id),))


def test_ws_registrado_despues_del_corte_se_cierra_con_4001(client, usuarios, monkeypatch):
    import main

    o, _ = usuarios()
    registrar_real = main._ws_connect_and_subscribe

    async def corte_en_el_hueco(user_id, *args):
        await _subir_version(user_id)
        assert await ws_hub.close_user(str(user_id)) == 0, "el corte todavía no encuentra la conexión"
        return await registrar_real(user_id, *args)

    monkeypatch.setattr(main, "_ws_connect_and_subscribe", corte_en_el_hueco)
    with client.websocket_connect(f"/ws/{o}") as sock:
        sock.send_json({"type": "auth", "token": token_para(o)})
        assert sock.receive_json() == {"type": "auth_ok"}
        sock.send_json({"type": "ping"})
        mensaje = sock.receive()
        while mensaje["type"] == "websocket.send" and '"pong"' not in mensaje.get("text", ""):
            mensaje = sock.receive()
        assert (mensaje["type"], mensaje.get("code")) == ("websocket.close", 4001), (
            "la conexión sobrevivió al corte: respondió el ping")


def test_sse_registrado_despues_del_corte_termina_el_stream(client, usuarios, monkeypatch):
    from auth.jwt import decode_token
    from auth.middleware import verificar_sesion

    o, _ = usuarios()
    registrar_real = events_mod._sse_connect_and_subscribe

    async def corte_en_el_hueco(user_id, *args):
        await _subir_version(user_id)
        assert await events_mod.close_user_streams(str(user_id)) == 0, "el corte todavía no encuentra el stream"
        return await registrar_real(user_id, *args)

    monkeypatch.setattr(events_mod, "_sse_connect_and_subscribe", corte_en_el_hueco)

    async def abrir_y_leer():
        user = await verificar_sesion(decode_token(token_para(o)), "access")
        gen = (await events_mod.sse_events(user)).body_iterator
        siguiente = asyncio.ensure_future(gen.__anext__())
        try:
            listos, _ = await asyncio.wait({siguiente}, timeout=2)
            if not listos:
                return "abierto"
            try:
                siguiente.result()
            except StopAsyncIteration:
                return "terminado"
            return "evento"
        finally:
            siguiente.cancel()
            await asyncio.gather(siguiente, return_exceptions=True)
            await gen.aclose()

    assert client.portal.call(abrir_y_leer) == "terminado"
    assert not sse_connections.has_connections(str(o))


# ------------------------- fix ronda 1: concurrencia y corte tolerante

def test_degradacion_mutua_concurrente_no_da_deadlock_uno_gana_y_el_otro_409(client, usuarios, monkeypatch):
    """A degrada a B y B degrada a A, a la vez, en dos transacciones reales.
    Antes: cada una bloqueaba su destino y después pedía el del otro (el
    FOR UPDATE del conteo) -> InnoDB 1213 -> 500. Ahora los bloqueos se toman
    en orden fijo: uno gana y el otro ve el resultado y responde 409.

    Mundo de dos superadmins: user 1 (y los de otros tests) también son
    superadmins activos en jax_memory_test y no se tocan, así que el conteo
    se restringe a la pareja — pero SIEMPRE después de ejecutar la consulta
    real, con sus bloqueos reales. La barrera espera a que las dos lleguen al
    punto de decisión (o 1,5 s, si el orden fijo deja a una esperando antes)."""
    from starlette.requests import Request

    from auth.models import AuthUser

    a, _ = usuarios(role="superadmin")
    b, _ = usuarios(role="superadmin")
    conteo_real = users_mod.otros_superadmins_activos

    async def en_un_mundo_de_dos(cur, excluido):
        await conteo_real(cur, excluido)
        await cur.execute(
            "SELECT user_id FROM jax_users WHERE role = 'superadmin' AND status = 'active' "
            "AND user_id IN (%s, %s) AND user_id <> %s FOR UPDATE", (a, b, excluido))
        return len(await cur.fetchall())

    llegadas, todas = [], asyncio.Event()
    invariante_real = users_mod.exigir_invariante

    async def con_barrera(*args):
        llegadas.append(1)
        if len(llegadas) == 2:
            todas.set()
        try:
            await asyncio.wait_for(todas.wait(), timeout=1.5)
        except asyncio.TimeoutError:  # fail-soft: con el orden fijo la otra transacción espera ANTES de este punto; la barrera no puede exigir que llegue
            pass
        return await invariante_real(*args)

    monkeypatch.setattr(users_mod, "otros_superadmins_activos", en_un_mundo_de_dos)
    monkeypatch.setattr(users_mod, "exigir_invariante", con_barrera)

    def pedido():
        return Request({"type": "http", "method": "PUT", "path": "/", "headers": [], "client": ("testclient", 1)})

    async def ambos():
        return await asyncio.gather(
            users_mod.update_user(b, users_mod.UpdateUserRequest(role="operator"), pedido(),
                                  AuthUser(user_id=str(a), tenant_id="1", role="superadmin")),
            users_mod.update_user(a, users_mod.UpdateUserRequest(role="operator"), pedido(),
                                  AuthUser(user_id=str(b), tenant_id="1", role="superadmin")),
            return_exceptions=True)

    resultados = client.portal.call(ambos)
    inesperados = [r for r in resultados if isinstance(r, Exception) and not isinstance(r, HTTPException)]
    assert inesperados == [], f"nada de 500 (deadlock): {inesperados!r}"
    ok = [r for r in resultados if r == {"ok": True}]
    rechazos = [(r.status_code, r.detail) for r in resultados if isinstance(r, HTTPException)]
    assert (len(ok), rechazos) == (1, [(409, "ultimo_superadmin")]), resultados
    roles = sorted(client.portal.call(_fila, u)[0] for u in (a, b))
    assert roles == ["operator", "superadmin"], "queda exactamente un superadmin de la pareja"


def test_si_cortar_conexiones_falla_el_cambio_confirmado_responde_igual(client, usuarios, monkeypatch):
    async def revienta(user_id, code=4001):
        raise RuntimeError("hub caído")

    monkeypatch.setattr(ws_hub, "close_user", revienta)
    monkeypatch.setattr(users_mod, "close_user_streams", revienta)
    o, _ = usuarios()
    assert _put(client, o, status="inactive").status_code == 200
    assert client.portal.call(_fila, o) == ("operator", "inactive", 1)
    p, _ = usuarios()
    assert client.delete(f"/api/admin/users/{p}", headers=_admin()).status_code == 200
    assert client.portal.call(_fila, p) is None


async def test_transaccion_rechaza_un_nivel_de_aislamiento_fuera_de_la_lista():
    """El nivel va interpolado en `SET TRANSACTION`: lista cerrada, y falla
    antes de pedir una conexión."""
    from db.transaccion import transaccion
    with pytest.raises(ValueError):
        async with transaccion("SERIALIZABLE; DROP TABLE jax_users"):
            pass


# ---------------------------------------------- acciones auditadas e historial

import uuid  # noqa: E402

from tests.identidades import borrar_usuario  # noqa: E402


def test_cerrar_sesiones_corta_los_tokens_y_audita(client, usuarios):
    o, _ = usuarios()
    viejo = token_para(o)
    assert client.get("/api/auth/me", headers=auth(viejo)).status_code == 200
    assert client.post(f"/api/admin/users/{o}/revoke-sessions", headers=_admin()).status_code == 200
    assert client.get("/api/auth/me", headers=auth(viejo)).status_code == 401
    assert client.portal.call(_fila, o)[2] == 1
    assert [a[1] for a in client.portal.call(_auditoria, o)] == ["sessions_revoked"]
    r = client.post("/api/admin/users/1000000000/revoke-sessions", headers=_admin())
    assert (r.status_code, r.json()["detail"]) == (404, "usuario_no_encontrado")


def test_cerrar_sesiones_corta_ws_y_sse_despues_del_commit(client, usuarios, cortes):
    """Controller: además de subir token_version, las conexiones YA abiertas
    se cierran tras el commit (mismo corte que PUT/DELETE). Un 404 no corta."""
    o, _ = usuarios()
    assert client.post(f"/api/admin/users/{o}/revoke-sessions", headers=_admin()).status_code == 200
    assert cortes == [("ws", str(o), ("operator", "active", 1)), ("sse", str(o), ("operator", "active", 1))]
    cortes.clear()
    assert client.post(f"/api/admin/users/{10**9}/revoke-sessions", headers=_admin()).status_code == 404
    assert cortes == []


def test_desbloquear_audita(client, usuarios):
    o, _ = usuarios()
    client.portal.call(sql, "UPDATE jax_users SET failed_attempts = 5, "
                            "locked_until = UTC_TIMESTAMP() + INTERVAL 10 MINUTE WHERE user_id = %s", (o,))
    assert client.post(f"/api/admin/users/{o}/unlock", headers=_admin()).status_code == 200
    ((intentos, bloqueo),) = client.portal.call(sql, "SELECT failed_attempts, locked_until FROM jax_users "
                                                     "WHERE user_id = %s", (o,), True)
    assert (intentos, bloqueo) == (0, None)
    assert [a[1] for a in client.portal.call(_auditoria, o)] == ["unlock"]


def test_alta_audita(client):
    email = f"test-alta-{uuid.uuid4().hex[:10]}@example.invalid"
    r = client.post("/api/admin/users", json={"email": email, "role": "viewer", "password": "clave-larga-1"},
                    headers=_admin())
    assert r.status_code == 200, r.text
    nuevo = r.json()["user_id"]
    try:
        ((actor, accion, detalle, _ip),) = client.portal.call(_auditoria, nuevo)
        assert (actor, accion, json.loads(detalle)) == (1, "create", {"email": email, "role": "viewer"})
    finally:
        client.portal.call(borrar_usuario, nuevo)


def test_historial_devuelve_las_ultimas_50_mas_nuevas_primero(client, usuarios):
    o, _ = usuarios()
    valores = ", ".join(["(1, %s, 'unlock', %s, NOW(6) - INTERVAL %s SECOND)"] * 55)
    args = tuple(x for i in range(55) for x in (o, json.dumps({"n": i}), 55 - i))
    client.portal.call(sql, "INSERT INTO user_admin_audit (actor_user_id, target_user_id, action, detail, ts) "
                            f"VALUES {valores}", args)
    r = client.get(f"/api/admin/users/{o}/audit", headers=_admin())
    assert r.status_code == 200, r.text
    entradas = r.json()["entries"]
    assert len(entradas) == 50
    assert (entradas[0]["detail"], entradas[-1]["detail"]) == ({"n": 54}, {"n": 5})
    ((email_1,),) = client.portal.call(sql, "SELECT email FROM jax_users WHERE user_id = 1", (), True)
    assert entradas[0]["actor_email"] == email_1 and entradas[0]["action"] == "unlock"


def test_historial_solo_superadmin(client, usuarios):
    o, _ = usuarios()
    assert client.get(f"/api/admin/users/{o}/audit", headers=auth(token_para(o))).status_code == 403


def test_alta_con_email_duplicado_concurrente_responde_409_sin_auditoria(client, monkeypatch):
    """Carrera entre el SELECT COUNT y el INSERT: otra conexión da de alta el
    mismo email justo después del chequeo. El INSERT perdedor choca con el
    UNIQUE de email (1062): tiene que ser el mismo 409 que el chequeo, no un
    500, y la transacción revierte sin registro de auditoría."""
    from contextlib import asynccontextmanager

    email = f"test-carrera-{uuid.uuid4().hex[:10]}@example.invalid"
    transaccion_real = users_mod.transaccion

    class _CursorQueDejaColarse:
        def __init__(self, cur):
            self._cur = cur

        def __getattr__(self, nombre):
            return getattr(self._cur, nombre)

        async def execute(self, consulta, args=()):
            resultado = await self._cur.execute(consulta, args)
            if consulta.startswith("SELECT COUNT(*) FROM jax_users WHERE email"):
                await sql("INSERT INTO jax_users (tenant_id, email, password_hash, role, status) "
                          "VALUES (1, %s, 'x', 'viewer', 'active')", (email,))
            return resultado

    @asynccontextmanager
    async def con_carrera(*args, **kw):
        async with transaccion_real(*args, **kw) as cur:
            yield _CursorQueDejaColarse(cur)

    monkeypatch.setattr(users_mod, "transaccion", con_carrera)
    try:
        r = client.post("/api/admin/users", json={"email": email, "role": "viewer", "password": "clave-larga-1"},
                        headers=_admin())
        assert (r.status_code, r.json().get("detail")) == (409, "Email ya existe"), r.text
        ((auditados,),) = client.portal.call(
            sql, "SELECT COUNT(*) FROM user_admin_audit WHERE action = 'create' AND detail LIKE %s",
            (f"%{email}%",), True)
        assert auditados == 0
    finally:
        for (intruso,) in client.portal.call(sql, "SELECT user_id FROM jax_users WHERE email = %s", (email,), True):
            client.portal.call(borrar_usuario, intruso)


# ------------------------------------------- fechas con zona (Task 4, ronda 1)
# La sesión de MariaDB de la app corre en SYSTEM = CST (UTC-6; medido el
# 2026-09-15 con @@session.time_zone y UTC_TIMESTAMP() vs NOW()). Antes estas
# fechas salían con isoformat() sin zona -- y además en hora CST --, así que
# `new Date()` en el navegador las leía como hora local.

def _utc_cerca(valor, esperado, margen=120):
    dt = datetime.fromisoformat(valor)
    assert dt.utcoffset() is not None and dt.utcoffset().total_seconds() == 0, f"sin zona UTC explícita: {valor!r}"
    assert abs((dt - esperado).total_seconds()) < margen, (
        f"{valor!r} no es ~{esperado.isoformat()} (¿hora CST de la sesión presentada como UTC?)")


def test_ultimo_acceso_alta_y_bloqueo_salen_en_utc_con_zona(client, usuarios):
    o, _ = usuarios()
    ahora = datetime.now(timezone.utc)
    # Como auth.login: last_login con NOW() (TIMESTAMP), locked_until con utc_ahora().
    client.portal.call(sql, "UPDATE jax_users SET last_login = NOW(), locked_until = %s WHERE user_id = %s",
                       (utc_ahora() + timedelta(minutes=10), o))
    r = client.get("/api/admin/users", headers=_admin())
    assert r.status_code == 200, r.text
    fila = next(u for u in r.json()["users"] if u["user_id"] == o)
    _utc_cerca(fila["last_login"], ahora)
    _utc_cerca(fila["created_at"], ahora)
    _utc_cerca(fila["locked_until"], ahora + timedelta(minutes=10))
    assert fila["is_locked"] is True


def test_historial_ts_sale_en_utc_con_zona(client, usuarios):
    o, _ = usuarios()
    ahora = datetime.now(timezone.utc)
    assert client.post(f"/api/admin/users/{o}/revoke-sessions", headers=_admin()).status_code == 200
    (entrada,) = client.get(f"/api/admin/users/{o}/audit", headers=_admin()).json()["entries"]
    _utc_cerca(entrada["ts"], ahora)
