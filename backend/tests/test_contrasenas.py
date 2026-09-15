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
from auth import conexiones as conexiones_mod  # noqa: E402
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
    monkeypatch.setattr(conexiones_mod, "close_user_streams", sse)
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
    monkeypatch.setattr(conexiones_mod, "close_user_streams", revienta)
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


# ------------------------------------------------ enlace de recuperación

import asyncio  # noqa: E402
import smtplib  # noqa: E402
from datetime import timedelta  # noqa: E402

import smtp_config  # noqa: E402
import user_audit  # noqa: E402
from tiempo import utc_ahora  # noqa: E402


@pytest.fixture
def smtp_configurado(monkeypatch):
    async def cargar():
        return smtp_config.SmtpSettings(host="mail.example.test", port=587, encryption="tls", user="u",
                                        password="p", from_name="Axioma", from_email="no-reply@example.test")

    monkeypatch.setattr(smtp_config, "cargar_settings", cargar)


def _enlace(client, user_id, cabeceras=None):
    return client.post(f"/api/admin/users/{user_id}/reset-link", headers=cabeceras or _admin())


async def _tokens(user_id):
    return [f[0] for f in await sql("SELECT token FROM password_reset_tokens WHERE user_id = %s AND used = FALSE",
                                    (user_id,), True)]


def test_enlace_sin_smtp_configurado_es_503_y_no_crea_token(client, usuarios, monkeypatch):
    async def sin_configurar():
        raise smtp_config.SmtpNoConfigurado("nunca configurado")

    monkeypatch.setattr(smtp_config, "cargar_settings", sin_configurar)
    u, _ = usuarios()
    r = _enlace(client, u)
    assert (r.status_code, r.json()["detail"]) == (503, "smtp_no_configurado")
    assert client.portal.call(_tokens, u) == []


def test_enlace_crea_token_manda_al_correo_guardado_y_audita(client, usuarios, smtp_configurado, monkeypatch):
    from api import auth as auth_mod
    enviados = []
    monkeypatch.setattr(auth_mod, "_send_reset_email", lambda s, to, link: enviados.append((s.host, to, link)))
    u, email = usuarios()
    r = _enlace(client, u)
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "to": email}
    (token,) = client.portal.call(_tokens, u)
    ((host, destino, link),) = enviados
    assert (host, destino) == ("mail.example.test", email) and token in link
    assert client.portal.call(_acciones, u) == ["reset_link_sent"]


def test_enlace_con_smtp_que_falla_es_502_con_la_respuesta_y_borra_el_token(client, usuarios, smtp_configurado,
                                                                             monkeypatch):
    from api import auth as auth_mod

    def falla(s, to, link):
        raise smtplib.SMTPServerDisconnected("Connection unexpectedly closed")

    monkeypatch.setattr(auth_mod, "_send_reset_email", falla)
    u, _ = usuarios()
    r = _enlace(client, u)
    assert r.status_code == 502
    assert r.json()["detail"]["code"] == "smtp_envio_fallido"
    assert "unexpectedly closed" in r.json()["detail"]["server"]
    assert client.portal.call(_acciones, u) == [], "no se audita un envío que no salió"
    # U17: un enlace no entregado no puede seguir siendo válido.
    assert client.portal.call(_tokens, u) == []


def test_enlace_con_password_smtp_no_ascii_es_502_y_borra_el_token(client, usuarios, smtp_configurado, monkeypatch):
    """Fix ronda 1 (2026-09-15): mismo caso que /smtp/test (etapa 1,
    api/admin/smtp.py) -- smtplib codifica el AUTH en ascii, una contraseña
    guardada no ASCII revienta con UnicodeEncodeError, no con OSError."""
    from api import auth as auth_mod

    def falla(s, to, link):
        raise UnicodeEncodeError("ascii", "contraseña-ñ", 0, 1, "ordinal not in range(128)")

    monkeypatch.setattr(auth_mod, "_send_reset_email", falla)
    u, _ = usuarios()
    r = _enlace(client, u)
    assert r.status_code == 502
    assert r.json()["detail"] == {"code": "smtp_password_no_ascii", "server": ""}
    assert client.portal.call(_tokens, u) == []
    assert client.portal.call(_acciones, u) == [], "no se audita un envío que no salió"


def test_enlace_con_config_corrupta_es_503_y_borra_el_token(client, usuarios, smtp_configurado, monkeypatch):
    """Fix ronda 1 (2026-09-15): construir_mensaje rechaza encabezados con
    caracteres de control (misma red que /smtp/test) -- ValueError, DESPUÉS
    de UnicodeEncodeError en el orden de los except (es su superclase)."""
    from api import auth as auth_mod

    def falla(s, to, link):
        raise ValueError("encabezado con caracter de control")

    monkeypatch.setattr(auth_mod, "_send_reset_email", falla)
    u, _ = usuarios()
    r = _enlace(client, u)
    assert (r.status_code, r.json()["detail"]) == (503, "smtp_config_corrupta")
    assert client.portal.call(_tokens, u) == []
    assert client.portal.call(_acciones, u) == [], "no se audita un envío que no salió"


def test_enlace_borra_por_token_exacto_no_pisa_un_forgot_password_concurrente(client, usuarios, smtp_configurado,
                                                                               monkeypatch):
    """Fix ronda 1 / U17 (2026-09-15): la limpieza borra por TOKEN exacto, no
    por user_id -- un forgot-password concurrente del mismo usuario, creado
    DESPUÉS del nuestro y ANTES de que el envío admita el fallo, sobrevive."""
    from api import auth as auth_mod
    u, _ = usuarios()

    def falla_y_llega_otro_token(s, to, link):
        client.portal.call(sql, "INSERT INTO password_reset_tokens (user_id, token, expires_at, ip_address) "
                                "VALUES (%s, %s, %s, 'test')",
                           (u, str(uuid.uuid4()), utc_ahora() + timedelta(hours=1)))
        raise smtplib.SMTPServerDisconnected("Connection unexpectedly closed")

    monkeypatch.setattr(auth_mod, "_send_reset_email", falla_y_llega_otro_token)
    r = _enlace(client, u)
    assert r.status_code == 502
    assert len(client.portal.call(_tokens, u)) == 1, "el token concurrente sobrevive; solo se borra el nuestro"


def test_enlace_con_envio_cancelado_borra_el_token_y_propaga(client, usuarios, smtp_configurado, monkeypatch):
    """Fix ronda 2, hallazgo 3(a) (2026-09-15): asyncio.CancelledError es
    BaseException, no Exception -- ningún `except` de send_reset_link lo
    atrapa (ni el de smtp_password_no_ascii, ni config_corrupta, ni
    smtp_envio_fallido). Antes de este fix, una cancelación se saltaba
    también la limpieza y dejaba el token vivo. El `finally` con la bandera
    `enviado` corre igual y lo borra; la cancelación sigue propagándose (no
    se convierte en una respuesta HTTP -- Starlette la deja pasar tal cual).
    Lo que llega hasta acá es `concurrent.futures.CancelledError`, no
    `asyncio.CancelledError`: levantar CancelledError DENTRO de una tarea es
    la señal de asyncio para cancelar ESA tarea (medido), así que el portal
    (que corre el request como una tarea y espera el resultado con un
    `concurrent.futures.Future`) ve la tarea cancelada, no la excepción
    original -- pero el efecto que importa acá (nada atrapa la cancelación
    antes del `finally`, y el `finally` limpia igual) es el mismo."""
    import concurrent.futures
    from api import auth as auth_mod

    def cancelado(s, to, link):
        raise asyncio.CancelledError("cliente se fue a mitad del envío")

    monkeypatch.setattr(auth_mod, "_send_reset_email", cancelado)
    u, _ = usuarios()
    with pytest.raises(concurrent.futures.CancelledError):
        _enlace(client, u)
    assert client.portal.call(_tokens, u) == [], "el token no puede quedar vivo aunque el envío se cancele"
    assert client.portal.call(_acciones, u) == [], "no se audita un envío que no salió"


def test_enlace_con_fallo_de_auditoria_tras_enviar_responde_200_igual(client, usuarios, smtp_configurado,
                                                                       monkeypatch):
    """Ruling U22, fix ronda 2 (2026-09-15): el correo YA SALIÓ cuando la
    auditoría se intenta -- si esa escritura falla, la respuesta sigue siendo
    200 (fail-soft; el error no puede deshacer un correo ya entregado). El
    token queda consumido igual (no hay limpieza: `enviado` es True) y nada
    más cambia."""
    from api import auth as auth_mod

    enviados = []
    monkeypatch.setattr(auth_mod, "_send_reset_email", lambda s, to, link: enviados.append((to, link)))

    async def falla(*a, **kw):
        raise RuntimeError("DB caída justo acá")

    monkeypatch.setattr(user_audit, "registrar", falla)
    u, email = usuarios()
    r = _enlace(client, u)
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "to": email}
    assert len(enviados) == 1, "el correo salió a pesar de que la auditoría falló DESPUÉS"
    assert client.portal.call(_acciones, u) == [], "la auditoría falló: no hay fila"
    (token,) = client.portal.call(_tokens, u)
    assert token in enviados[0][1], "el token del correo enviado sigue siendo el vigente (no se borró ni se creó otro)"


def test_reset_bloquea_el_usuario_antes_que_el_token(client, usuarios, monkeypatch):
    """Ruling U21 (fix ronda 2, 2026-09-15): esto prueba el ORDEN de bloqueo
    real -- no solo el resultado -- grabando el SQL que /reset-password
    ejecuta dentro de su transacción. Tiene que aparecer un
    `SELECT ... FROM jax_users ... FOR UPDATE` ANTES que el
    `UPDATE password_reset_tokens ... used = TRUE` que reclama el token. Sin
    el `FOR UPDATE` del usuario (o con el bloqueo movido después del reclamo)
    este test falla: verificado a mano quitando cada uno y restaurando
    después (ver task-3-report.md)."""
    from contextlib import asynccontextmanager

    u, _ = usuarios(password=CLAVE)
    token = str(uuid.uuid4())
    client.portal.call(sql, "INSERT INTO password_reset_tokens (user_id, token, expires_at, ip_address) "
                            "VALUES (%s, %s, %s, 'test')", (u, token, utc_ahora() + timedelta(hours=1)))

    ejecutadas = []
    transaccion_real = auth_mod.transaccion

    class _CursorQueGraba:
        def __init__(self, cur):
            self._cur = cur

        def __getattr__(self, nombre):
            return getattr(self._cur, nombre)

        async def execute(self, consulta, args=()):
            ejecutadas.append(consulta)
            return await self._cur.execute(consulta, args)

    @asynccontextmanager
    async def con_registro(*args, **kw):
        async with transaccion_real(*args, **kw) as cur:
            yield _CursorQueGraba(cur)

    monkeypatch.setattr(auth_mod, "transaccion", con_registro)
    r = client.post("/api/auth/reset-password", json={"token": token, "password": NUEVA})
    assert r.status_code == 200, r.text

    indice_usuario = next(i for i, q in enumerate(ejecutadas)
                          if q.startswith("SELECT status FROM jax_users") and "FOR UPDATE" in q)
    indice_token = next(i for i, q in enumerate(ejecutadas)
                        if q.startswith("UPDATE password_reset_tokens SET used = TRUE"))
    assert indice_usuario < indice_token, (
        "el SELECT ... FOR UPDATE del usuario tiene que ejecutarse ANTES que el "
        "UPDATE que reclama el token (Ruling U21, orden de bloqueo)"
    )


def test_enlace_a_inactivo_o_inexistente(client, usuarios, smtp_configurado):
    u, _ = usuarios(status="inactive")
    r = _enlace(client, u)
    assert (r.status_code, r.json()["detail"]) == (409, "usuario_no_activo")
    r = _enlace(client, 10**9)
    assert (r.status_code, r.json()["detail"]) == (404, "usuario_no_encontrado")


def test_enlace_solo_superadmin(client, usuarios, smtp_configurado):
    u, _ = usuarios()
    assert _enlace(client, u, auth(token_para(u))).status_code == 403


def test_completar_el_reset_cierra_las_sesiones_y_audita(client, usuarios, cortes):
    u, email = usuarios(password=CLAVE)
    viejo = token_para(u)
    token = str(uuid.uuid4())
    client.portal.call(sql, "INSERT INTO password_reset_tokens (user_id, token, expires_at, ip_address) "
                            "VALUES (%s, %s, %s, 'test')", (u, token, utc_ahora() + timedelta(hours=1)))
    r = client.post("/api/auth/reset-password", json={"token": token, "password": NUEVA})
    assert r.status_code == 200, r.text
    assert client.get("/api/auth/me", headers=auth(viejo)).status_code == 401
    assert client.portal.call(_version, u) == 1
    assert client.portal.call(_acciones, u) == ["password_reset_completed"]
    assert _login(client, email, NUEVA).status_code == 200
    # U9: el corte pasa DESPUÉS del commit y ve ya la versión confirmada.
    assert cortes == [("ws", str(u), 1), ("sse", str(u), 1)]
    r = client.post("/api/auth/reset-password", json={"token": token, "password": "otra-clave-789"})
    assert (r.status_code, r.json()["detail"]) == (400, "reset_token_usado")


def test_reset_con_token_usado_expirado_o_invalido_no_corta_conexiones(client, usuarios, cortes):
    u, _ = usuarios(password=CLAVE)

    usado = str(uuid.uuid4())
    client.portal.call(sql, "INSERT INTO password_reset_tokens (user_id, token, expires_at, ip_address, used) "
                            "VALUES (%s, %s, %s, 'test', TRUE)", (u, usado, utc_ahora() + timedelta(hours=1)))
    assert client.post("/api/auth/reset-password",
                       json={"token": usado, "password": NUEVA}).status_code == 400

    expirado = str(uuid.uuid4())
    client.portal.call(sql, "INSERT INTO password_reset_tokens (user_id, token, expires_at, ip_address) "
                            "VALUES (%s, %s, %s, 'test')", (u, expirado, utc_ahora() - timedelta(hours=1)))
    assert client.post("/api/auth/reset-password",
                       json={"token": expirado, "password": NUEVA}).status_code == 400

    assert client.post("/api/auth/reset-password",
                       json={"token": str(uuid.uuid4()), "password": NUEVA}).status_code == 400

    assert cortes == [], "ningún token usado/expirado/inválido corta conexiones"
    assert client.portal.call(_version, u) == 0


def test_reset_si_el_corte_falla_el_reset_confirmado_responde_igual(client, usuarios, monkeypatch):
    async def revienta(user_id, code=4001):
        raise RuntimeError("hub caído")

    monkeypatch.setattr(ws_hub, "close_user", revienta)
    monkeypatch.setattr(conexiones_mod, "close_user_streams", revienta)
    u, _ = usuarios(password=CLAVE)
    token = str(uuid.uuid4())
    client.portal.call(sql, "INSERT INTO password_reset_tokens (user_id, token, expires_at, ip_address) "
                            "VALUES (%s, %s, %s, 'test')", (u, token, utc_ahora() + timedelta(hours=1)))
    r = client.post("/api/auth/reset-password", json={"token": token, "password": NUEVA})
    assert r.status_code == 200, r.text
    assert client.portal.call(_version, u) == 1


def test_reset_reclama_el_token_bajo_el_bloqueo_del_usuario(client, usuarios, cortes, monkeypatch):
    """Fix ronda 1 (2026-09-15): si el token se marca `used` DURANTE la
    ventana de bcrypt (antes de entrar a la transacción), el UPDATE atómico
    (`WHERE id = %s AND used = FALSE` + rowcount != 1) lo detecta y revierte
    -- no pisa un token ya reclamado por otra carrera. Sin ese `AND used =
    FALSE` (o sin el chequeo de rowcount) este test falla: verificado a mano
    quitándolos y restaurándolos (ver task-3-report.md)."""
    u, email = usuarios(password=CLAVE)
    antes = client.portal.call(_hash_de, u)
    token = str(uuid.uuid4())
    client.portal.call(sql, "INSERT INTO password_reset_tokens (user_id, token, expires_at, ip_address) "
                            "VALUES (%s, %s, %s, 'test')", (u, token, utc_ahora() + timedelta(hours=1)))
    hash_real = auth_mod._hash

    def hashea_y_marca_usado(password):
        h = hash_real(password)
        # Simula la carrera: OTRA conexión reclama el token mientras este
        # request todavía está hasheando (bcrypt corre fuera de la transacción).
        client.portal.call(sql, "UPDATE password_reset_tokens SET used = TRUE WHERE token = %s", (token,))
        return h

    monkeypatch.setattr(auth_mod, "_hash", hashea_y_marca_usado)
    r = client.post("/api/auth/reset-password", json={"token": token, "password": NUEVA})
    assert (r.status_code, r.json()["detail"]) == (400, "reset_token_usado")
    assert client.portal.call(_version, u) == 0
    assert client.portal.call(_acciones, u) == []
    assert cortes == []
    assert client.portal.call(_hash_de, u) == antes, "la contraseña no cambia"
    assert _login(client, email, CLAVE).status_code == 200, "la vieja sigue sirviendo"


def test_reset_a_un_usuario_inactivo_es_400_y_no_consume_el_token(client, usuarios, cortes):
    """Ruling U21 (fix ronda 1, 2026-09-15): el usuario se bloquea ANTES que
    el token -- mismo orden que la cascada del viejo delete_user y que la baja
    de la etapa 5 (jax_users, después password_reset_tokens), para no formar
    un ciclo de espera con esa transacción. Un token válido para un usuario ya inactivo
    no se consume: 400 reset_token_invalido, sin escribir nada."""
    u, _ = usuarios(password=CLAVE, status="inactive")
    antes = client.portal.call(_hash_de, u)
    token = str(uuid.uuid4())
    client.portal.call(sql, "INSERT INTO password_reset_tokens (user_id, token, expires_at, ip_address) "
                            "VALUES (%s, %s, %s, 'test')", (u, token, utc_ahora() + timedelta(hours=1)))
    r = client.post("/api/auth/reset-password", json={"token": token, "password": NUEVA})
    assert (r.status_code, r.json()["detail"]) == (400, "reset_token_invalido")
    assert client.portal.call(_tokens, u) == [token], "el token sigue sin usar"
    assert client.portal.call(_hash_de, u) == antes
    assert client.portal.call(_acciones, u) == []
    assert cortes == []
