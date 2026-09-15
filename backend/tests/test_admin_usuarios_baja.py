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
