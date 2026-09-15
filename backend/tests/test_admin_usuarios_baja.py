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
