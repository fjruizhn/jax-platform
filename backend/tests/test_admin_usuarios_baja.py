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
