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

import pytest
from fastapi import HTTPException

from api import events as events_mod
from api.admin import users as users_mod
from jax_engine.lifecycle import sse_connections
from jax_engine.websocket_hub import WebSocketHub, ws_hub
from tests.identidades import auth, sql, token_para


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


async def test_close_user_streams_termina_el_stream_sse_del_usuario_y_no_el_de_otro():
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
