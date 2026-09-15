"""Editar y dar de baja (2026-09-12, administración de usuarios, etapa 5, spec §3.5).

Eliminar = dar de baja (decisión de Fernando): la cuenta queda inutilizable,
sale de la lista y libera el correo; se conserva el historial. Nada de DELETE:
9 tablas tienen user_id y solo 3 con FK a jax_users, así que borrar de verdad
dejaba memoria, costos y pipelines huérfanos o fallaba (spec §1, hallazgo 7).
"""
import json
import uuid
from datetime import date

from api.admin import users as users_mod
from tests.identidades import auth, borrar_usuario, sql, token_para


def _admin():
    return auth(token_para(1, role="superadmin"))


def test_columnas_de_la_baja_y_ancho_del_correo(client):
    filas = client.portal.call(
        sql,
        "SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'jax_users' "
        "AND COLUMN_NAME IN ('email', 'deleted_at', 'deleted_by') ORDER BY COLUMN_NAME", (), True)
    assert [tuple(f) for f in filas] == [("deleted_at", "datetime", None), ("deleted_by", "int", None),
                                        ("email", "varchar", 320)]


def test_indice_unico_de_email_se_conserva(client):
    filas = client.portal.call(
        sql,
        "SELECT NON_UNIQUE FROM information_schema.STATISTICS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'jax_users' "
        "AND COLUMN_NAME = 'email'", (), True)
    assert [tuple(f) for f in filas] == [(0,)]


# --------------------------------------------------------------- editar

async def _fila(user_id):
    filas = await sql("SELECT email, role, status, token_version, deleted_at, deleted_by FROM jax_users "
                      "WHERE user_id = %s", (user_id,), True)
    return tuple(filas[0]) if filas else None


async def _acciones(target):
    return [tuple(f) for f in await sql("SELECT action, detail FROM user_admin_audit WHERE target_user_id = %s "
                                        "ORDER BY id", (target,), True)]


def _put(client, target, **cuerpo):
    return client.put(f"/api/admin/users/{target}", json=cuerpo, headers=_admin())


def test_editar_el_correo_cambia_y_audita_sin_cerrar_sesiones(client, usuarios):
    u, email = usuarios()
    nuevo = f"test-nuevo-{uuid.uuid4().hex[:10]}@example.invalid"
    r = _put(client, u, email=f"  {nuevo}  ")
    assert r.status_code == 200, r.text
    fila = client.portal.call(_fila, u)
    assert (fila[0], fila[3]) == (nuevo, 0)
    ((accion, detalle),) = client.portal.call(_acciones, u)
    assert (accion, json.loads(detalle)) == ("update_email", {"from": email, "to": nuevo})


def test_correo_invalido(client, usuarios):
    u, email = usuarios()
    for malo in ("sin-arroba", "a@b", "a" * 250 + "@x.io"):
        r = _put(client, u, email=malo)
        assert (r.status_code, r.json()["detail"]) == (400, "email_invalido"), malo
    assert client.portal.call(_fila, u)[0] == email


def test_correo_repetido(client, usuarios):
    _, email_a = usuarios()
    b, email_b = usuarios()
    r = _put(client, b, email=email_a)
    assert (r.status_code, r.json()["detail"]) == (409, "email_ya_existe")
    assert client.portal.call(_fila, b)[0] == email_b


def test_alta_con_codigos_estables(client, usuarios):
    _, existente = usuarios()

    def alta(email, role="viewer"):
        r = client.post("/api/admin/users", json={"email": email, "role": role, "password": "clave-larga-1"},
                        headers=_admin())
        return r.status_code, r.json().get("detail")

    assert alta(existente) == (409, "email_ya_existe")
    assert alta("no-es-un-correo") == (400, "email_invalido")
    assert alta(f"test-rol-{uuid.uuid4().hex[:8]}@example.invalid", role="dios") == (400, "rol_invalido")


# ----------------------------------------------------------------- baja

import pytest  # noqa: E402

import smtp_config  # noqa: E402


def _baja(client, target, cabeceras=None):
    return client.post(f"/api/admin/users/{target}/baja", headers=cabeceras or _admin())


def test_email_de_baja_libera_la_direccion_y_cabe_en_la_columna():
    assert users_mod.email_de_baja("ana@x.io", 42, date(2026, 9, 12)) == "ana@x.io#baja-42-20260912"
    maximo = "a" * 249 + "@x.io"  # 254: el máximo aceptado
    assert len(users_mod.email_de_baja(maximo, 2**31 - 1, date(2026, 9, 12))) <= 320


def test_baja_inutiliza_la_cuenta_renombra_el_correo_y_audita(client, usuarios):
    u, email = usuarios(password="clave-de-la-baja-1")
    viejo = token_para(u)
    r = _baja(client, u)
    assert r.status_code == 200, r.text
    email_f, _rol, estado, tv, deleted_at, deleted_by = client.portal.call(_fila, u)
    assert (estado, tv, deleted_by) == ("deleted", 1, 1) and deleted_at is not None
    assert email_f == users_mod.email_de_baja(email, u, deleted_at.date())
    assert client.get("/api/auth/me", headers=auth(viejo)).status_code == 401
    r = client.post("/api/auth/login", json={"email": email, "password": "clave-de-la-baja-1"})
    client.cookies.clear()
    assert r.status_code == 401
    ((accion, detalle),) = client.portal.call(_acciones, u)
    assert (accion, json.loads(detalle)) == ("baja", {"email": email})


def test_la_baja_libera_el_correo(client, usuarios):
    u, email = usuarios()
    assert _baja(client, u).status_code == 200
    r = client.post("/api/admin/users", json={"email": email, "role": "viewer", "password": "clave-larga-1"},
                    headers=_admin())
    assert r.status_code == 200, r.text
    client.portal.call(borrar_usuario, r.json()["user_id"])


def test_la_lista_no_muestra_a_los_dados_de_baja(client, usuarios):
    vivo, _ = usuarios()
    ido, _ = usuarios()
    assert _baja(client, ido).status_code == 200
    ids = [x["user_id"] for x in client.get("/api/admin/users", headers=_admin()).json()["users"]]
    assert vivo in ids and ido not in ids


def test_nadie_se_da_de_baja_a_si_mismo_ni_al_ultimo_superadmin(client, usuarios, monkeypatch):
    s, _ = usuarios(role="superadmin")
    r = _baja(client, s, auth(token_para(s, role="superadmin")))
    assert (r.status_code, r.json()["detail"]) == (403, "auto_accion_prohibida")

    async def ninguno(cur, excluido):
        return 0

    monkeypatch.setattr(users_mod, "otros_superadmins_activos", ninguno)
    r = _baja(client, s)
    assert (r.status_code, r.json()["detail"]) == (409, "ultimo_superadmin")
    assert client.portal.call(_fila, s)[2] == "active"


def test_un_dado_de_baja_ya_no_se_toca(client, usuarios, monkeypatch):
    async def cargar():
        return smtp_config.SmtpSettings(host="mail.example.test", port=587, encryption="tls", user="u",
                                        password="p", from_name="Axioma", from_email="no-reply@example.test")

    monkeypatch.setattr(smtp_config, "cargar_settings", cargar)
    u, _ = usuarios()
    assert _baja(client, u).status_code == 200
    r = _baja(client, u)
    assert (r.status_code, r.json()["detail"]) == (404, "usuario_no_encontrado")
    assert _put(client, u, role="viewer").json()["detail"] == "usuario_no_encontrado"
    for accion in ("unlock", "revoke-sessions", "reset-link"):
        r = client.post(f"/api/admin/users/{u}/{accion}", headers=_admin())
        assert (r.status_code, r.json()["detail"]) == (404, "usuario_no_encontrado"), accion


def test_delete_ya_no_existe(client, usuarios):
    u, _ = usuarios()
    assert client.delete(f"/api/admin/users/{u}", headers=_admin()).status_code == 405
    assert client.portal.call(_fila, u) is not None


def test_baja_solo_superadmin(client, usuarios):
    u, _ = usuarios()
    otro, _ = usuarios()
    assert _baja(client, otro, auth(token_para(u))).status_code == 403


# ------------------------------------------- baja: dispatch de la Task 3

async def _tokens_pendientes(user_id):
    return [f[0] for f in await sql("SELECT token FROM password_reset_tokens WHERE user_id = %s AND used = FALSE",
                                    (user_id,), True)]


def test_baja_de_un_usuario_inexistente_es_404(client):
    r = _baja(client, 10**9)
    assert (r.status_code, r.json()["detail"]) == (404, "usuario_no_encontrado")


def test_la_baja_borra_los_enlaces_de_recuperacion_pendientes(client, usuarios):
    """Ningún enlace vivo sobrevive a la baja: se borran en la MISMA
    transacción, después de bloquear la fila del usuario (orden usuario ->
    token, el de /reset-password, U21)."""
    u, _ = usuarios()
    otro, _ = usuarios()
    for dueno in (u, otro):
        client.portal.call(sql, "INSERT INTO password_reset_tokens (user_id, token, expires_at, ip_address) "
                                "VALUES (%s, %s, UTC_TIMESTAMP() + INTERVAL 1 HOUR, '127.0.0.1')",
                           (dueno, str(uuid.uuid4())))
    assert _baja(client, u).status_code == 200
    assert client.portal.call(_tokens_pendientes, u) == []
    assert len(client.portal.call(_tokens_pendientes, otro)) == 1, "el de otro usuario no se toca"


def test_enlace_de_recuperacion_a_un_dado_de_baja_es_404_y_no_crea_token(client, usuarios, monkeypatch):
    async def cargar():
        return smtp_config.SmtpSettings(host="mail.example.test", port=587, encryption="tls", user="u",
                                        password="p", from_name="Axioma", from_email="no-reply@example.test")

    enviados = []
    monkeypatch.setattr(smtp_config, "cargar_settings", cargar)
    monkeypatch.setattr(users_mod.auth_api, "_send_reset_email", lambda *a: enviados.append(a))
    u, _ = usuarios()
    assert _baja(client, u).status_code == 200
    r = client.post(f"/api/admin/users/{u}/reset-link", headers=_admin())
    assert (r.status_code, r.json()["detail"]) == (404, "usuario_no_encontrado")
    assert client.portal.call(_tokens_pendientes, u) == [] and enviados == []


def test_la_lista_sin_dados_de_baja_no_ordena_en_memoria(client):
    """LAS CUATRO (indexing), EXPLAIN sobre la consulta REAL: `status <>
    'deleted'` no es un prefijo de idx_jax_users_role_status (status es la 2ª
    columna) y la lista devuelve casi todas las filas, así que el plan correcto
    es recorrer la PK en el orden del ORDER BY y filtrar: sin filesort ni
    temporal."""
    filas = client.portal.call(sql, "EXPLAIN " + users_mod.SQL_LISTA_USUARIOS, (), True)
    ((_id, _sel, tabla, _tipo, _posibles, _clave, _largo, _ref, _filas, extra),) = [tuple(f) for f in filas]
    assert tabla == "jax_users"
    assert "filesort" not in (extra or "") and "temporary" not in (extra or ""), filas


# ------------------ fix ronda 1 (Ruling U31): enlace y baja, sin carrera
#
# Antes: send_reset_link y _procesar_recuperacion leían el estado SIN bloqueo
# y creaban el token después, en otra conexión. Una baja confirmada en ese
# hueco dejaba un token vivo para un dado de baja (y el correo salía). Ahora
# la lectura del usuario (FOR UPDATE por PK) y el token van en UNA
# transacción: o la baja confirma antes (404 / nada) o espera y, al entrar,
# borra el token que se acaba de crear. En ningún caso queda uno vivo.

import asyncio  # noqa: E402
import logging  # noqa: E402

from fastapi import HTTPException  # noqa: E402

from api import auth as auth_mod  # noqa: E402


def _pedido():
    from starlette.requests import Request
    return Request({"type": "http", "method": "POST", "path": "/", "headers": [], "client": ("testclient", 1)})


def _superadmin_1():
    from auth.models import AuthUser
    return AuthUser(user_id="1", tenant_id="1", role="superadmin")


@pytest.fixture
def smtp_y_buzon(monkeypatch):
    """SMTP configurado y un buzón en memoria en lugar del envío real."""
    async def cargar():
        return smtp_config.SmtpSettings(host="mail.example.test", port=587, encryption="tls", user="u",
                                        password="p", from_name="Axioma", from_email="no-reply@example.test")

    enviados = []
    monkeypatch.setattr(smtp_config, "cargar_settings", cargar)
    monkeypatch.setattr(auth_mod, "_send_reset_email", lambda s, to, link: enviados.append((to, link)))
    return enviados


def _baja_en_paralelo(user_id):
    return asyncio.ensure_future(users_mod.dar_de_baja(user_id, _pedido(), _superadmin_1()))


def test_baja_colada_antes_del_token_del_enlace_de_admin_no_deja_token_vivo(client, usuarios, smtp_y_buzon,
                                                                             monkeypatch):
    """La baja arranca justo antes del INSERT del token y se le dan 0,5 s para
    confirmar. Antes: confirmaba (nada la frenaba) y el token se creaba igual
    para un dado de baja. Ahora espera el bloqueo de la fila; el token se crea,
    la transacción confirma y la baja, al entrar, lo borra."""
    u, _ = usuarios()
    real, bajas = auth_mod._crear_enlace_de_recuperacion, []

    async def con_baja_colada(*args):
        bajas.append(_baja_en_paralelo(u))
        await asyncio.wait({bajas[0]}, timeout=0.5)
        return await real(*args)

    monkeypatch.setattr(auth_mod, "_crear_enlace_de_recuperacion", con_baja_colada)

    async def correr():
        try:
            resultado = await users_mod.send_reset_link(u, _pedido(), _superadmin_1())
        except HTTPException as exc:
            resultado = (exc.status_code, exc.detail)
        return resultado, await bajas[0]

    resultado, baja = client.portal.call(correr)
    assert baja == {"ok": True}
    assert client.portal.call(_tokens_pendientes, u) == [], "un dado de baja no puede quedar con un enlace vivo"
    assert resultado == (404, "usuario_no_encontrado") or resultado["ok"] is True, resultado


def test_enlace_de_admin_y_baja_concurrentes_sin_deadlock_ni_token_vivo(client, usuarios, smtp_y_buzon):
    u, _ = usuarios()

    async def ambos():
        return await asyncio.gather(users_mod.send_reset_link(u, _pedido(), _superadmin_1()),
                                    users_mod.dar_de_baja(u, _pedido(), _superadmin_1()), return_exceptions=True)

    enlace, baja = client.portal.call(ambos)
    assert baja == {"ok": True}, f"nada de 500 (deadlock): {baja!r}"
    assert (isinstance(enlace, HTTPException) and (enlace.status_code, enlace.detail) == (404, "usuario_no_encontrado")
            ) or (isinstance(enlace, dict) and enlace["ok"] is True), repr(enlace)
    assert client.portal.call(_tokens_pendientes, u) == []


def test_baja_entre_la_busqueda_y_el_token_del_forgot_password_no_crea_ni_envia(client, usuarios, smtp_y_buzon,
                                                                               monkeypatch):
    """Forgot-password público: la búsqueda por correo no bloquea; la baja
    confirma mientras se cargan los ajustes SMTP. Antes: el token se creaba y
    el correo salía igual. Ahora el re-bloqueo por PK ve `deleted` y termina
    en silencio."""
    u, email = usuarios()
    cargar_real = smtp_config.cargar_settings

    async def cargar_con_baja():
        assert await users_mod.dar_de_baja(u, _pedido(), _superadmin_1()) == {"ok": True}
        return await cargar_real()

    monkeypatch.setattr(smtp_config, "cargar_settings", cargar_con_baja)
    client.portal.call(auth_mod._procesar_recuperacion, email, "203.0.113.5")
    assert client.portal.call(_tokens_pendientes, u) == []
    assert smtp_y_buzon == [], "no sale un correo para un dado de baja"


def test_forgot_password_y_baja_concurrentes_sin_deadlock_ni_token_vivo(client, usuarios, smtp_y_buzon, caplog):
    u, email = usuarios()

    async def ambos():
        return await asyncio.gather(auth_mod._procesar_recuperacion(email, "203.0.113.5"),
                                    users_mod.dar_de_baja(u, _pedido(), _superadmin_1()), return_exceptions=True)

    with caplog.at_level(logging.ERROR, logger="api.auth"):
        recuperacion, baja = client.portal.call(ambos)
    assert (recuperacion, baja) == (None, {"ok": True})
    fallos = [r.getMessage() for r in caplog.records if r.name == "api.auth"]
    assert fallos == [], f"el procesamiento en segundo plano falló (¿deadlock?): {fallos}"
    assert client.portal.call(_tokens_pendientes, u) == []
    assert len(smtp_y_buzon) <= 1


# ------------- fix ronda 1 (Ruling U32): un correo renombrado no es un correo
#
# El dominio de email_valido admitía `#`: "ana@x.io#baja-42-20260912" pasaba.
# Un superadmin podía crear o editar a alguien con el correo renombrado que
# OTRO usuario va a recibir en su baja, y esa baja daba 500 contra el UNIQUE.

def test_un_correo_renombrado_por_la_baja_no_es_un_correo_valido():
    from validacion import email_valido
    assert not email_valido(users_mod.email_de_baja("ana@x.io", 42, date(2026, 9, 12)))


def test_no_se_puede_crear_ni_editar_un_usuario_con_el_correo_renombrado_de_otro(client, usuarios):
    a, email_a = usuarios()
    b, email_b = usuarios()
    ocupado = users_mod.email_de_baja(email_a, a, date(2026, 9, 15))
    r = client.post("/api/admin/users", json={"email": ocupado, "role": "viewer", "password": "clave-larga-1"},
                    headers=_admin())
    if r.status_code == 200:  # rojo: no dejar la fila colgada en la base de tests
        client.portal.call(borrar_usuario, r.json()["user_id"])
    assert (r.status_code, r.json()["detail"]) == (400, "email_invalido")
    r = _put(client, b, email=ocupado)
    assert (r.status_code, r.json()["detail"]) == (400, "email_invalido")
    assert client.portal.call(_fila, b)[0] == email_b


def test_la_baja_libera_el_correo_tambien_para_editar_a_otro(client, usuarios):
    a, email_a = usuarios()
    b, _ = usuarios()
    assert _baja(client, a).status_code == 200
    r = _put(client, b, email=email_a)
    assert r.status_code == 200, r.text
    assert client.portal.call(_fila, b)[0] == email_a
