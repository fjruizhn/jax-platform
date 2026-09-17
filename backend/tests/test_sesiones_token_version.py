"""Sesiones que se cortan de verdad (2026-09-12, administración de usuarios, etapa 2).

Antes: get_current_user solo decodificaba el JWT (rol del token) y /refresh
reemitía el access con el rol del refresh token, sin consultar la base.
Desactivar, borrar o bajar de rol NO cortaba la sesión: el usuario seguía
entrando hasta 7 días, y un superadmin degradado seguía siéndolo (spec §1,
hallazgo 1).

Ahora: access y refresh llevan `tv`; cada request lee status, role y
token_version por clave primaria; el rol sale de la base. Un token de antes
del despliegue (sin `tv`) vale como tv=0.
"""
import logging
import time

from jose import jwt

from auth.jwt import ALGORITHM, SECRET, create_access_token, create_refresh_token, decode_token


# ------------------------------------------------------------- puros

def test_access_token_lleva_la_version():
    assert decode_token(create_access_token("5", "1", "operator"))["tv"] == 0
    assert decode_token(create_access_token("5", "1", "operator", 3))["tv"] == 3


def test_refresh_token_lleva_la_version():
    payload = decode_token(create_refresh_token("5", "1", "operator", 7, vida_segundos=3600))
    assert (payload["tv"], payload["type"]) == (7, "refresh")


# ---------------------------------------------------------- esquema

async def _columna():
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT DATA_TYPE, IS_NULLABLE, COLUMN_DEFAULT FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'jax_users' AND COLUMN_NAME = 'token_version'")
            return [tuple(f) for f in await cur.fetchall()]


def test_columna_token_version(client):
    assert client.portal.call(_columna) == [("int", "NO", "0")]


# ---------------------------------------------------------- middleware

from tests.identidades import auth, sql, token_para  # noqa: E402


def _me(client, token):
    return client.get("/api/auth/me", headers=auth(token))


def _usuarios_admin(client, token):
    return client.get("/api/admin/users", headers=auth(token))


def test_usuario_inexistente_no_entra(client):
    ((libre,),) = client.portal.call(sql, "SELECT COALESCE(MAX(user_id), 0) + 1000 FROM jax_users", (), True)
    r = _me(client, token_para(libre))
    assert (r.status_code, r.json()["detail"]) == (401, "sesion_invalida")


def test_user_id_no_numerico_no_entra(client):
    # Antes pasaba: el rol "superadmin" salía del token sin mirar la base.
    r = _usuarios_admin(client, create_access_token("no-es-un-id", "1", "superadmin"))
    assert r.status_code == 401


def test_usuario_desactivado_pierde_el_acceso_en_el_request_siguiente(client, usuarios):
    user_id, _ = usuarios()
    token = token_para(user_id)
    assert _me(client, token).status_code == 200
    client.portal.call(sql, "UPDATE jax_users SET status = 'inactive' WHERE user_id = %s", (user_id,))
    assert _me(client, token).status_code == 401


def test_version_distinta_no_entra(client, usuarios):
    user_id, _ = usuarios(token_version=3)
    assert _me(client, token_para(user_id, tv=2)).status_code == 401
    assert _me(client, token_para(user_id, tv=3)).status_code == 200


def test_token_de_antes_del_despliegue_sin_tv_vale_como_cero(client, usuarios):
    # CONTROL: ya pasa hoy. Protege la regla de spec §3.2 (nadie queda afuera
    # al desplegar) de un refactor que exija `tv`.
    user_id, _ = usuarios()
    viejo = jwt.encode({"user_id": str(user_id), "tenant_id": "1", "role": "operator",
                        "exp": int(time.time()) + 600, "type": "access"}, SECRET, algorithm=ALGORITHM)
    assert _me(client, viejo).status_code == 200


def test_el_rol_sale_de_la_base_no_del_token(client, usuarios):
    user_id, _ = usuarios(role="operator")
    token = token_para(user_id, role="superadmin")
    assert _usuarios_admin(client, token).status_code == 403
    assert _me(client, token).json()["role"] == "operator"


def test_superadmin_degradado_pierde_admin_al_instante(client, usuarios):
    user_id, _ = usuarios(role="superadmin")
    token = token_para(user_id, role="superadmin")
    assert _usuarios_admin(client, token).status_code == 200
    client.portal.call(sql, "UPDATE jax_users SET role = 'operator' WHERE user_id = %s", (user_id,))
    assert _usuarios_admin(client, token).status_code == 403


def test_refresh_token_no_sirve_como_access(client, usuarios):
    # CONTROL: ya pasa hoy (el middleware viejo miraba `type`).
    user_id, _ = usuarios()
    assert _me(client, token_para(user_id, tipo="refresh")).status_code == 401


def test_la_consulta_del_middleware_va_por_primary(client):
    from auth.middleware import SQL_ESTADO_DE_SESION

    async def explicar():
        from db.connection import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("EXPLAIN " + SQL_ESTADO_DE_SESION, (1,))
                columnas = [d[0] for d in cur.description]
                return [dict(zip(columnas, fila)) for fila in await cur.fetchall()]

    (plan,) = client.portal.call(explicar)
    assert (plan["type"], plan["key"]) == ("const", "PRIMARY"), plan


# Hallazgo de code review (M-3, revisión final): /me hacía DOS consultas por
# PK -- verificar_sesion (status/role/token_version) y después un SELECT
# email aparte en el propio handler. Con el email ya en la fila que lee el
# middleware, /me no necesita su propio SELECT.
def test_me_hace_una_sola_consulta_a_jax_users(client, usuarios):
    import aiomysql

    user_id, email = usuarios()

    class _Contador:
        def __init__(self):
            self.selects = 0
            self._original = aiomysql.cursors.Cursor.execute

        def __enter__(self):
            original = self._original
            contador = self

            async def wrapped(cursor_self, query, args=None):
                normalizada = " ".join(query.split())
                if normalizada.strip().upper().startswith("SELECT") and "FROM jax_users" in normalizada:
                    contador.selects += 1
                return await original(cursor_self, query, args)

            aiomysql.cursors.Cursor.execute = wrapped
            return self

        def __exit__(self, *exc):
            aiomysql.cursors.Cursor.execute = self._original

    with _Contador() as contador:
        r = _me(client, token_para(user_id))

    assert r.status_code == 200, r.text
    assert r.json()["email"] == email
    assert contador.selects == 1, (
        f"esperaba una sola SELECT contra jax_users en /me, hubo {contador.selects}"
    )


# --------------------------------------------------------------- refresh

def _refresh(client, token):
    try:
        # Cookie explícita: el jar del `client` de sesión puede traer la de
        # otro test (login); http.cookiejar no pisa un Cookie ya puesto.
        return client.post("/api/auth/refresh", headers={"Cookie": f"refresh_token={token}"})
    finally:
        client.cookies.clear()


def test_refresh_con_version_vieja_no_emite(client, usuarios):
    user_id, _ = usuarios(token_version=1)
    r = _refresh(client, token_para(user_id, tv=0, tipo="refresh"))
    assert (r.status_code, r.json()["detail"]) == (401, "sesion_invalida")


def test_refresh_de_usuario_desactivado_no_emite(client, usuarios):
    user_id, _ = usuarios(status="inactive")
    assert _refresh(client, token_para(user_id, tipo="refresh")).status_code == 401


def test_refresh_emite_con_el_rol_y_la_version_actuales(client, usuarios):
    user_id, _ = usuarios(role="superadmin", token_version=2)
    r = _refresh(client, token_para(user_id, role="operator", tv=2, tipo="refresh"))
    assert r.status_code == 200, r.text
    nuevo = decode_token(r.json()["access_token"])
    assert (nuevo["user_id"], nuevo["role"], nuevo["tv"], nuevo["type"]) == (str(user_id), "superadmin", 2, "access")


def test_access_token_no_sirve_como_refresh(client, usuarios):
    # CONTROL: ya pasa hoy.
    user_id, _ = usuarios()
    assert _refresh(client, token_para(user_id)).status_code == 401


# ----------------------------------------------------------------- login

def test_login_emite_la_version_actual(client, usuarios):
    user_id, email = usuarios(password="clave-de-prueba-9", token_version=4)
    r = client.post("/api/auth/login", json={"email": email, "password": "clave-de-prueba-9"})
    client.cookies.clear()
    assert r.status_code == 200, r.text
    # Task 3b (sesión única, 2026-09-15): el login sube la versión y emite la
    # que él mismo escribió (4 -> 5), no la que leyó antes del bcrypt.
    assert decode_token(r.json()["access_token"])["tv"] == 5


# ------------------------------------------------------------- WebSocket

from starlette.websockets import WebSocketDisconnect  # noqa: E402


def _ws_auth(client, user_id, token):
    with client.websocket_connect(f"/ws/{user_id}") as ws:
        ws.send_json({"type": "auth", "token": token})
        try:
            return ws.receive_json()
        except WebSocketDisconnect as exc:
            return exc.code


def test_ws_de_usuario_desactivado_se_cierra_con_4001(client, usuarios):
    user_id, _ = usuarios(status="inactive")
    assert _ws_auth(client, user_id, token_para(user_id)) == 4001


def test_ws_con_version_vieja_se_cierra_con_4001(client, usuarios):
    user_id, _ = usuarios(token_version=1)
    assert _ws_auth(client, user_id, token_para(user_id, tv=0)) == 4001


def test_ws_de_usuario_real_activo_autentica(client, usuarios):
    # CONTROL: ya pasa hoy.
    user_id, _ = usuarios()
    assert _ws_auth(client, user_id, token_para(user_id)) == {"type": "auth_ok"}


# --------------------------------------------------- fallo 4001 sin log (round 1)
#
# Hallazgo de code review: `verificar_sesion` (un round-trip real a la base)
# corre dentro del mismo `try` cuyo único manejador era `except Exception:
# close(4001)` sin loguear nada. Una sesión inválida (HTTPException 401) y un
# fallo de infraestructura (pool agotado, MariaDB caída) terminaban IGUAL en
# 4001, indistinguibles y sin rastro en logs.


def test_ws_con_fallo_de_infraestructura_se_cierra_con_4001_y_lo_loguea(client, usuarios, monkeypatch, caplog):
    import main

    async def _pool_caido(*_a, **_k):
        raise RuntimeError("pool caído")

    user_id, _ = usuarios()
    monkeypatch.setattr(main, "verificar_sesion", _pool_caido)
    with caplog.at_level(logging.ERROR):
        resultado = _ws_auth(client, user_id, token_para(user_id))
    assert resultado == 4001
    assert any(r.levelno >= logging.ERROR for r in caplog.records), (
        "un fallo real (pool caído) debe quedar en el log, no desaparecer en el 4001"
    )


def test_ws_de_sesion_invalida_no_deja_log_de_error(client, usuarios, caplog):
    # Caso esperado (usuario desactivado): 4001 sin ensuciar el log de errores.
    user_id, _ = usuarios(status="inactive")
    with caplog.at_level(logging.ERROR):
        resultado = _ws_auth(client, user_id, token_para(user_id))
    assert resultado == 4001
    assert not any(r.levelno >= logging.ERROR for r in caplog.records)


# --------------------------------------- M-2: TimeoutError fuera del receive()
#
# Hallazgo de code review (M-2, revisión final): el único `except
# asyncio.TimeoutError` del handshake envolvía TODO el bloque -- el
# `wait_for(receive_json(), timeout=5)` Y `verificar_sesion` (un round-trip a
# la base). Hoy `pool.acquire()` no tiene timeout propio, así que en la
# práctica sólo el receive() dispara ese TimeoutError. Pero si algún día
# `verificar_sesion` (o lo que sea que corra ahí) lanza un TimeoutError por
# otra razón (pool agotado con timeout, por ejemplo), quedaría indistinguible
# del timeout del receive -- 4001 en silencio, sin loguear el fallo real.


def test_ws_timeout_fuera_del_receive_se_loguea(client, usuarios, monkeypatch, caplog):
    import asyncio as asyncio_mod

    import main

    async def _timeout_en_verificar_sesion(*_a, **_k):
        raise asyncio_mod.TimeoutError()

    user_id, _ = usuarios()
    monkeypatch.setattr(main, "verificar_sesion", _timeout_en_verificar_sesion)
    with caplog.at_level(logging.ERROR):
        resultado = _ws_auth(client, user_id, token_para(user_id))
    assert resultado == 4001
    assert any(r.levelno >= logging.ERROR for r in caplog.records), (
        "un TimeoutError que NO es el del receive() del mensaje de auth "
        "(p.ej. verificar_sesion/pool.acquire) debe quedar en el log, no "
        "desaparecer en el 4001 silencioso reservado para el timeout de auth"
    )
