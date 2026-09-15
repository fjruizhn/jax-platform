"""Contraseñas (2026-09-12, administración de usuarios, etapa 4, spec §3.4).

Una sola regla en todo el sistema: mínimo 8 caracteres, máximo 72 bytes
(bcrypt 5 LANZA con más, antes era un 500). Mi cuenta exige la actual y
cierra las otras sesiones; el reset por admin es un enlace por correo que
falla a la vista si no hay SMTP; completar un reset corta las sesiones
viejas.
"""
import uuid

from auth.password_rules import problema_de_password
from tests.identidades import _hash, auth, sql, token_para

CLAVE = "clave-vieja-123"
NUEVA = "clave-nueva-456"


def _admin():
    return auth(token_para(1, role="superadmin"))


# ---------------------------------------------------------------- puros

def test_menos_de_8_caracteres_es_corta():
    assert problema_de_password("1234567") == "corta"
    assert problema_de_password("12345678") is None


def test_mas_de_72_bytes_es_larga_aunque_tenga_menos_caracteres():
    assert problema_de_password("ñ" * 37) == "larga"   # 37 caracteres, 74 bytes
    assert problema_de_password("ñ" * 36) is None      # 72 bytes: el máximo


def test_cuenta_caracteres_como_python_no_unidades_utf16():
    assert problema_de_password("😀" * 7) == "corta"    # 7 caracteres (28 bytes)


# ------------------------------------------------------------------ alta

def test_el_alta_aplica_la_regla_y_no_da_500(client):
    email = f"test-alta-regla-{uuid.uuid4().hex[:10]}@example.invalid"
    for password, codigo in (("corta", "password_corta"), ("x" * 73, "password_larga")):
        r = client.post("/api/admin/users", json={"email": email, "role": "viewer", "password": password},
                        headers=_admin())
        assert (r.status_code, r.json()["detail"]) == (400, codigo)
    ((cuantos,),) = client.portal.call(sql, "SELECT COUNT(*) FROM jax_users WHERE email = %s", (email,), True)
    assert cuantos == 0


# ------------------------------------------------------------- Mi cuenta

import pytest  # noqa: E402

import api.auth as auth_mod  # noqa: E402
import api.admin.users as users_mod  # noqa: E402
from auth import rate_limit  # noqa: E402
from auth.jwt import decode_token  # noqa: E402
from auth.rate_limit import SlidingWindowLimiter  # noqa: E402
from jax_engine.websocket_hub import ws_hub  # noqa: E402


async def _version(user_id):
    ((tv,),) = await sql("SELECT token_version FROM jax_users WHERE user_id = %s", (user_id,), True)
    return tv


async def _acciones(target):
    filas = await sql("SELECT action FROM user_admin_audit WHERE target_user_id = %s ORDER BY id", (target,), True)
    return [f[0] for f in filas]


def _cambiar(client, token, actual, nueva):
    try:
        return client.post("/api/auth/me/password", json={"current_password": actual, "new_password": nueva},
                           headers=auth(token))
    finally:
        client.cookies.clear()


def _login(client, email, password):
    try:
        return client.post("/api/auth/login", json={"email": email, "password": password})
    finally:
        client.cookies.clear()


@pytest.fixture
def cortes(monkeypatch):
    """Ruling U9: registra cada corte (WS y SSE) con la token_version que ve
    OTRA conexión en ese momento -- si el corte corriera dentro de la
    transacción, todavía vería la versión vieja."""
    registro = []

    async def ws(user_id, code=4001):
        registro.append(("ws", user_id, await _version(int(user_id))))
        return 0

    async def sse(user_id):
        registro.append(("sse", user_id, await _version(int(user_id))))
        return 0

    monkeypatch.setattr(ws_hub, "close_user", ws)
    monkeypatch.setattr(users_mod, "close_user_streams", sse)
    return registro


def test_mi_cuenta_exige_la_contrasena_actual(client, usuarios, cortes):
    u, _ = usuarios(password=CLAVE)
    r = _cambiar(client, token_para(u), "equivocada", NUEVA)
    assert (r.status_code, r.json()["detail"]) == (400, "password_actual_incorrecta")
    assert client.portal.call(_version, u) == 0
    assert cortes == [], "un rechazo no corta conexiones"


def test_mi_cuenta_aplica_la_regla(client, usuarios, cortes):
    u, _ = usuarios(password=CLAVE)
    assert _cambiar(client, token_para(u), CLAVE, "corta").json()["detail"] == "password_corta"
    assert _cambiar(client, token_para(u), CLAVE, "x" * 73).json()["detail"] == "password_larga"
    assert client.portal.call(_version, u) == 0
    assert cortes == []


def test_mi_cuenta_cambia_cierra_las_otras_sesiones_y_da_tokens_nuevos(client, usuarios, cortes):
    u, email = usuarios(password=CLAVE)
    viejo = token_para(u)
    r = client.post("/api/auth/me/password", json={"current_password": CLAVE, "new_password": NUEVA},
                    headers=auth(viejo))
    cookie = r.cookies.get("refresh_token")
    client.cookies.clear()
    assert r.status_code == 200, r.text
    nuevo = r.json()["access_token"]
    assert decode_token(nuevo)["tv"] == 1 and decode_token(cookie)["tv"] == 1
    assert client.get("/api/auth/me", headers=auth(viejo)).status_code == 401, "las otras sesiones se cierran"
    assert client.get("/api/auth/me", headers=auth(nuevo)).status_code == 200, "la actual sigue con tokens nuevos"
    assert _login(client, email, CLAVE).status_code == 401
    assert _login(client, email, NUEVA).status_code == 200
    assert client.portal.call(_acciones, u) == ["password_changed_self"]


def test_mi_cuenta_corta_ws_y_sse_despues_del_commit_incluida_la_pestana_propia(client, usuarios, cortes):
    """U9 / spec §3.2: se cortan TODAS las conexiones del usuario (también la
    de la pestaña que cambió, que reconecta con el token nuevo) y el corte ve
    ya la versión confirmada."""
    u, _ = usuarios(password=CLAVE)
    assert _cambiar(client, token_para(u), CLAVE, NUEVA).status_code == 200
    assert cortes == [("ws", str(u), 1), ("sse", str(u), 1)]


def test_mi_cuenta_si_el_corte_falla_el_cambio_confirmado_responde_igual(client, usuarios, monkeypatch):
    async def revienta(user_id, code=4001):
        raise RuntimeError("hub caído")

    monkeypatch.setattr(ws_hub, "close_user", revienta)
    monkeypatch.setattr(users_mod, "close_user_streams", revienta)
    u, _ = usuarios(password=CLAVE)
    r = _cambiar(client, token_para(u), CLAVE, NUEVA)
    assert r.status_code == 200 and decode_token(r.json()["access_token"])["tv"] == 1
    assert client.portal.call(_version, u) == 1


def test_mi_cuenta_si_la_contrasena_cambio_mientras_se_verificaba_no_pisa(client, usuarios, cortes, monkeypatch):
    """La contraseña verificada tiene que ser la vigente al escribir: si otro
    cambio (otra pestaña, un reset) la reemplazó entre la verificación y la
    transacción, este no la pisa con la contraseña vieja como prueba."""
    u, _ = usuarios(password=CLAVE)
    verificar_real = auth_mod.verify_password

    async def verifica_y_otro_cambia(plain, hashed):
        ok = await verificar_real(plain, hashed)
        await sql("UPDATE jax_users SET password_hash = %s WHERE user_id = %s", (_hash("otra-clave-789"), u))
        return ok

    monkeypatch.setattr(auth_mod, "verify_password", verifica_y_otro_cambia)
    r = _cambiar(client, token_para(u), CLAVE, NUEVA)
    assert (r.status_code, r.json()["detail"]) == (400, "password_actual_incorrecta")
    assert client.portal.call(_version, u) == 0
    assert client.portal.call(_acciones, u) == [] and cortes == []


async def _hash_de(user_id):
    ((h,),) = await sql("SELECT password_hash FROM jax_users WHERE user_id = %s", (user_id,), True)
    return h


def _cambio_con_algo_concurrente(client, usuarios, monkeypatch, sentencia):
    """Mientras se verifica la actual (la ventana de bcrypt), otra conexión
    ejecuta `sentencia` sobre el usuario. Devuelve (user_id, hash_antes, respuesta, cookie)."""
    u, _ = usuarios(password=CLAVE)
    antes = client.portal.call(_hash_de, u)
    verificar_real = auth_mod.verify_password

    async def verifica_y_otro_cambia(plain, hashed):
        ok = await verificar_real(plain, hashed)
        await sql(sentencia, (u,))
        return ok

    monkeypatch.setattr(auth_mod, "verify_password", verifica_y_otro_cambia)
    try:
        r = client.post("/api/auth/me/password", json={"current_password": CLAVE, "new_password": NUEVA},
                        headers=auth(token_para(u)))
        cookie = r.cookies.get("refresh_token")
    finally:
        client.cookies.clear()
    return u, antes, r, cookie


def _no_hizo_nada(client, u, antes, r, cookie, cortes):
    assert (r.status_code, r.json()["detail"]) == (401, "sesion_invalida")
    assert "access_token" not in r.json() and cookie is None, "sin tokens"
    assert client.portal.call(_hash_de, u) == antes, "el hash no cambia"
    assert client.portal.call(_acciones, u) == [] and cortes == []


def test_mi_cuenta_no_deshace_una_revocacion_concurrente(client, usuarios, cortes, monkeypatch):
    u, antes, r, cookie = _cambio_con_algo_concurrente(
        client, usuarios, monkeypatch, "UPDATE jax_users SET token_version = token_version + 1 WHERE user_id = %s")
    _no_hizo_nada(client, u, antes, r, cookie, cortes)
    assert client.portal.call(_version, u) == 1, "la revocación queda como la dejó el admin"


def test_mi_cuenta_no_cambia_la_contrasena_de_una_cuenta_desactivada_en_el_medio(client, usuarios, cortes,
                                                                                 monkeypatch):
    u, antes, r, cookie = _cambio_con_algo_concurrente(
        client, usuarios, monkeypatch, "UPDATE jax_users SET status = 'inactive' WHERE user_id = %s")
    _no_hizo_nada(client, u, antes, r, cookie, cortes)
    assert client.portal.call(_version, u) == 0


def test_mi_cuenta_tiene_el_limite_del_login(client, usuarios, monkeypatch):
    monkeypatch.setattr(rate_limit, "LOGIN_IP_LIMITER", SlidingWindowLimiter(2, 60, 1000))
    monkeypatch.setattr(rate_limit, "TRUSTED_PROXIES", frozenset())
    u, _ = usuarios(password=CLAVE)
    for _ in range(2):
        assert _cambiar(client, token_para(u), "equivocada", NUEVA).status_code == 400
    r = _cambiar(client, token_para(u), "equivocada", NUEVA)
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1
