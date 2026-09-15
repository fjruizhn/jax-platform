"""Sesión única (2026-09-15, Task 3b, Ruling F2 -- requisito de Fernando:
"una persona no debería poder tener 2 sesiones abiertas... al salir de una
sesión debería morirse la misma, y si quiere volver a accesar, la nueva sesión
debería matar la vieja").

- Sólo el login EXITOSO sube token_version, en una transacción que incrementa
  y relee bajo el bloqueo de la fila (dos logins simultáneos: gana el último y
  su token es el único válido). Después del commit corta WS/SSE.
- El logout identifica la sesión por la cookie de refresh, sube la versión y
  corta. Sin sesión viva que matar (sin cookie, basura, versión vieja,
  inactivo): sólo borra la cookie, 200, sin escribir.

El camino caliente (verificar_sesion: una SELECT por PK) no cambia; lo fijan
test_sesiones_token_version.py::test_la_consulta_del_middleware_va_por_primary
y ::test_me_hace_una_sola_consulta_a_jax_users.
"""
import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta

import pytest
from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import Response

import api.auth as auth_mod
import auth.conexiones as conexiones_mod
from auth.jwt import decode_token
from auth.middleware import verificar_sesion
from auth.models import LoginRequest
from jax_engine.websocket_hub import ws_hub
from tests.identidades import auth, sql, token_para
from tiempo import utc_ahora

CLAVE = "clave-sesion-unica-1"


async def _version(user_id):
    ((v,),) = await sql("SELECT token_version FROM jax_users WHERE user_id = %s", (user_id,), True)
    return int(v)


def _login(client, email, password=CLAVE):
    """Un "dispositivo": devuelve (respuesta, cookie de refresh) y deja el jar limpio."""
    try:
        r = client.post("/api/auth/login", json={"email": email, "password": password})
        return r, r.cookies.get("refresh_token")
    finally:
        client.cookies.clear()


def _con_cookie(client, url, cookie):
    try:
        # Cookie explícita (patrón de test_sesiones_token_version.py::_refresh).
        headers = {"Cookie": f"refresh_token={cookie}"} if cookie is not None else {}
        return client.post(url, headers=headers)
    finally:
        client.cookies.clear()


@pytest.fixture
def cortes(monkeypatch):
    """Registra cada corte (WS y SSE) con la token_version que ve OTRA conexión
    en ese momento: si el corte corriera antes del commit, vería la vieja."""
    registro = []

    async def ws(user_id, code=4001):
        registro.append(("ws", int(user_id), await _version(int(user_id))))
        return 0

    async def sse(user_id):
        registro.append(("sse", int(user_id), await _version(int(user_id))))
        return 0

    monkeypatch.setattr(ws_hub, "close_user", ws)
    monkeypatch.setattr(conexiones_mod, "close_user_streams", sse)
    return registro


def _borra_la_cookie(r):
    sc = r.headers.get("set-cookie", "")
    # Starlette.delete_cookie emite refresh_token=""; Max-Age=0.
    return "refresh_token=" in sc and "Max-Age=0" in sc


# ------------------------------------------------------------------ login

def test_el_segundo_login_mata_la_primera_sesion(client, usuarios, cortes):
    u, email = usuarios(password=CLAVE, token_version=3)
    r1, c1 = _login(client, email)
    assert r1.status_code == 200, r1.text
    assert client.portal.call(_version, u) == 4, "un login exitoso sube la versión exactamente 1"
    a1 = r1.json()["access_token"]
    assert decode_token(a1)["tv"] == 4 and decode_token(c1)["tv"] == 4

    r2, c2 = _login(client, email)
    assert r2.status_code == 200, r2.text
    assert client.portal.call(_version, u) == 5
    a2 = r2.json()["access_token"]

    viejo = client.get("/api/auth/me", headers=auth(a1))
    assert (viejo.status_code, viejo.json()["detail"]) == (401, "sesion_invalida")
    assert _con_cookie(client, "/api/auth/refresh", c1).status_code == 401
    assert client.get("/api/auth/me", headers=auth(a2)).status_code == 200
    assert _con_cookie(client, "/api/auth/refresh", c2).status_code == 200
    # Corte post-commit por cada login exitoso, con la versión ya confirmada.
    assert cortes == [("ws", u, 4), ("sse", u, 4), ("ws", u, 5), ("sse", u, 5)]


def test_un_login_fallido_no_sube_la_version_ni_corta(client, usuarios, cortes):
    # Sin la contraseña no se puede echar al dueño: contraseña mala, bloqueo e
    # inactivo no escriben token_version ni cortan nada.
    u, email = usuarios(password=CLAVE)
    viva = auth(token_para(u))
    r, _ = _login(client, email, "equivocada")
    assert r.status_code == 401
    assert client.portal.call(_version, u) == 0
    assert client.get("/api/auth/me", headers=viva).status_code == 200, "la sesión real sigue viva"

    b, email_b = usuarios(password=CLAVE)
    client.portal.call(sql, "UPDATE jax_users SET locked_until = %s WHERE user_id = %s",
                       (utc_ahora() + timedelta(minutes=10), b))
    r, _ = _login(client, email_b)
    assert r.status_code == 423
    assert client.portal.call(_version, b) == 0
    assert client.get("/api/auth/me", headers=auth(token_para(b))).status_code == 200

    i, email_i = usuarios(password=CLAVE, status="inactive")
    r, _ = _login(client, email_i)
    assert (r.status_code, r.json()["detail"]) == (403, "Usuario inactivo")
    assert client.portal.call(_version, i) == 0
    assert cortes == [], "un login fallido no corta conexiones"


def test_si_el_usuario_deja_de_estar_activo_durante_el_bcrypt_no_hay_tokens(client, usuarios, cortes, monkeypatch):
    """El UPDATE del login exitoso exige status='active': si en la ventana del
    bcrypt un admin lo desactivó, 0 filas -> la misma respuesta que un inactivo,
    sin tokens, sin subir la versión y sin cortar."""
    u, email = usuarios(password=CLAVE)
    real = auth_mod.verify_password

    async def y_en_el_medio_lo_desactivan(plain, hashed):
        ok = await real(plain, hashed)
        await sql("UPDATE jax_users SET status = 'inactive' WHERE user_id = %s", (u,))
        return ok

    monkeypatch.setattr(auth_mod, "verify_password", y_en_el_medio_lo_desactivan)
    r, cookie = _login(client, email)
    assert (r.status_code, r.json()["detail"]) == (403, "Usuario inactivo")
    assert cookie is None
    assert client.portal.call(_version, u) == 0
    assert cortes == []


def test_dos_logins_simultaneos_dejan_una_sola_sesion_valida(client, usuarios, monkeypatch):
    """Dos transacciones reales. La barrera deja pasar a los dos bcrypt antes
    de que cualquiera escriba: los dos UPDATE compiten por la fila. Con
    "old + 1" leído sin bloqueo, los dos emitirían la misma versión vieja y no
    quedaría ninguna sesión viva; sin incremento, quedarían las dos."""
    u, email = usuarios(password=CLAVE, token_version=7)
    real = auth_mod.verify_password
    llegadas, todas = [], asyncio.Event()

    async def con_barrera(plain, hashed):
        ok = await real(plain, hashed)
        llegadas.append(1)
        if len(llegadas) == 2:
            todas.set()
        await asyncio.wait_for(todas.wait(), timeout=5)
        return ok

    monkeypatch.setattr(auth_mod, "verify_password", con_barrera)

    def pedido(puerto):
        return Request({"type": "http", "method": "POST", "path": "/api/auth/login", "headers": [],
                        "client": ("testclient", puerto)})

    async def ambos():
        return await asyncio.gather(
            auth_mod.login(LoginRequest(email=email, password=CLAVE), pedido(1), Response()),
            auth_mod.login(LoginRequest(email=email, password=CLAVE), pedido(2), Response()),
            return_exceptions=True)

    resultados = client.portal.call(ambos)
    fallas = [r for r in resultados if isinstance(r, BaseException)]
    assert fallas == [], f"sin 500 ni deadlock: {fallas!r}"
    assert client.portal.call(_version, u) == 9, "cada login exitoso suma 1: 7 + 2"
    versiones = sorted(decode_token(r.access_token)["tv"] for r in resultados)
    assert versiones == [8, 9], "cada uno emitió la versión que él mismo escribió"

    async def vale(token):
        try:
            await verificar_sesion(decode_token(token), "access")
            return True
        except HTTPException:
            return False

    validas = [r for r in resultados if client.portal.call(vale, r.access_token)]
    assert len(validas) == 1 and decode_token(validas[0].access_token)["tv"] == 9


# ----------------------------------------------------------------- logout

def test_logout_mata_la_sesion_en_el_servidor(client, usuarios, cortes):
    u, email = usuarios(password=CLAVE)
    r, cookie = _login(client, email)
    access = r.json()["access_token"]
    cortes.clear()
    r = _con_cookie(client, "/api/auth/logout", cookie)
    assert (r.status_code, r.json()) == (200, {"ok": True})
    assert _borra_la_cookie(r), r.headers.get("set-cookie")
    assert client.portal.call(_version, u) == 2
    assert cortes == [("ws", u, 2), ("sse", u, 2)], "un corte, después del commit"
    viejo = client.get("/api/auth/me", headers=auth(access))
    assert (viejo.status_code, viejo.json()["detail"]) == (401, "sesion_invalida")
    assert _con_cookie(client, "/api/auth/refresh", cookie).status_code == 401


@pytest.mark.parametrize("caso", ["sin_cookie", "basura", "version_vieja", "inactivo", "access_en_vez_de_refresh"])
def test_logout_sin_sesion_viva_solo_borra_la_cookie(client, usuarios, cortes, caso):
    u, _ = usuarios(token_version=1, status="inactive" if caso == "inactivo" else "active")
    cookie = {
        "sin_cookie": None,
        "basura": "esto-no-es-un-jwt",
        "version_vieja": token_para(u, tipo="refresh", tv=0),
        "inactivo": token_para(u, tipo="refresh", tv=1),
        "access_en_vez_de_refresh": token_para(u, tv=1),
    }[caso]
    r = _con_cookie(client, "/api/auth/logout", cookie)
    assert (r.status_code, r.json()) == (200, {"ok": True})
    assert _borra_la_cookie(r), r.headers.get("set-cookie")
    assert client.portal.call(_version, u) == 1, "sin sesión viva no se escribe nada"
    assert cortes == []


def test_logout_con_la_marca_de_cambio_obligatorio_funciona(client, usuarios, cortes):
    u, _ = usuarios()
    client.portal.call(sql, "UPDATE jax_users SET must_change_password = TRUE WHERE user_id = %s", (u,))
    r = _con_cookie(client, "/api/auth/logout", token_para(u, tipo="refresh"))
    assert (r.status_code, r.json()) == (200, {"ok": True})
    assert client.portal.call(_version, u) == 1
    assert cortes == [("ws", u, 1), ("sse", u, 1)]


def test_si_la_escritura_del_logout_falla_no_se_informa_exito(client, usuarios, cortes, monkeypatch):
    """Sólo HTTPException (token o sesión rechazados) es "no hay sesión que
    matar". Un error de la base en el UPDATE no se traga."""
    u, _ = usuarios()

    @asynccontextmanager
    async def rota(aislamiento=None):
        raise RuntimeError("base caída")
        yield  # pragma: no cover

    monkeypatch.setattr(auth_mod, "transaccion", rota)
    with pytest.raises(RuntimeError, match="base caída"):
        _con_cookie(client, "/api/auth/logout", token_para(u, tipo="refresh"))
    assert client.portal.call(_version, u) == 0
    assert cortes == []


def test_si_la_lectura_de_la_sesion_falla_el_logout_no_la_da_por_muerta(client, usuarios, cortes, monkeypatch):
    """El except del logout es EXACTAMENTE HTTPException: un error de la base
    al verificar la sesión no se confunde con "no hay sesión viva" (si se
    tragara, respondería 200 con la sesión todavía viva en el servidor)."""
    u, _ = usuarios()

    async def base_caida(*args, **kwargs):
        raise RuntimeError("base caída al leer la sesión")

    monkeypatch.setattr(auth_mod, "verificar_sesion", base_caida)
    with pytest.raises(RuntimeError, match="al leer la sesión"):
        _con_cookie(client, "/api/auth/logout", token_para(u, tipo="refresh"))
    assert client.portal.call(_version, u) == 0
    assert cortes == []
