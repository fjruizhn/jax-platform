"""Fijar contraseña por admin y cambio obligatorio (2026-09-15, admin usuarios,
DECISIONES de Fernando que revierten U2; Ruling U34).

El superadmin le fija la contraseña a OTRO usuario; ese usuario queda con
jax_users.must_change_password y, mientras la tenga, su sesión sólo sirve para
/me, /me/password, /refresh y /logout (403 cambio_de_password_requerido en
todo lo demás, y WS/SSE rechazados). Mi cuenta y el reset por enlace la limpian.
"""
import uuid
from datetime import timedelta

import pytest

from auth.middleware import verificar_sesion
from tests.identidades import auth, sql, token_para
from tiempo import utc_ahora

CLAVE = "clave-vieja-123"
NUEVA = "clave-nueva-456"
FIJADA = "clave-fijada-789"


def _admin():
    return auth(token_para(1, role="superadmin"))


async def _marcar(user_id, valor=True):
    await sql("UPDATE jax_users SET must_change_password = %s WHERE user_id = %s", (valor, user_id))


async def _marca(user_id):
    ((m,),) = await sql("SELECT must_change_password FROM jax_users WHERE user_id = %s", (user_id,), True)
    return bool(m)


# --------------------------------------------------------------- esquema

def test_columna_must_change_password_no_nula_y_falsa_por_defecto(client):
    filas = client.portal.call(
        sql,
        "SELECT DATA_TYPE, IS_NULLABLE, COLUMN_DEFAULT FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'jax_users' AND COLUMN_NAME = 'must_change_password'",
        (), True)
    assert [tuple(f) for f in filas] == [("tinyint", "NO", "0")]


def test_verificar_sesion_informa_la_marca(client, usuarios):
    u, _ = usuarios()
    payload = {"type": "access", "user_id": str(u), "tenant_id": "1", "tv": 0}
    assert client.portal.call(verificar_sesion, payload, "access").must_change_password is False
    client.portal.call(_marcar, u)
    # En esta tarea sólo se INFORMA; la Task 2 la hace cumplir.
    assert client.portal.call(verificar_sesion, payload, "access").must_change_password is True
