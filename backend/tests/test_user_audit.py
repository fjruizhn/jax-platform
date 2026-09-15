"""Registro de acciones de administración (2026-09-12, admin usuarios etapa 3).

Hasta acá la única auditoría era credential_audit (credenciales de
proveedores): cambiar el rol de alguien, desactivarlo o borrarlo no dejaba
rastro (spec §1, hallazgo 5).
"""
import asyncio
import uuid

import pytest

import user_audit
from tests.identidades import sql


class _CursorFalso:
    def __init__(self):
        self.ejecutado = []

    async def execute(self, consulta, args=()):
        self.ejecutado.append((consulta, args))


def test_registrar_solo_acepta_acciones_conocidas():
    cur = _CursorFalso()
    asyncio.run(user_audit.registrar(cur, 1, 2, "update_role", {"from": "superadmin", "to": "operator"}, "203.0.113.5"))
    ((consulta, args),) = cur.ejecutado
    assert "INSERT INTO user_admin_audit" in consulta
    assert args == (1, 2, "update_role", '{"from": "superadmin", "to": "operator"}', "203.0.113.5")
    with pytest.raises(ValueError):
        asyncio.run(user_audit.registrar(cur, 1, 2, "borrar_todo"))
    assert len(cur.ejecutado) == 1, "una acción desconocida no se escribe"


def test_tabla_e_indices(client):
    filas = client.portal.call(
        sql,
        "SELECT TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX, COLUMN_NAME FROM information_schema.STATISTICS "
        "WHERE TABLE_SCHEMA = DATABASE() "
        "AND INDEX_NAME IN ('idx_user_admin_audit_target_ts', 'idx_jax_users_role_status') "
        "ORDER BY TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX", (), True)
    assert [tuple(f) for f in filas] == [
        ("jax_users", "idx_jax_users_role_status", 1, "role"),
        ("jax_users", "idx_jax_users_role_status", 2, "status"),
        ("user_admin_audit", "idx_user_admin_audit_target_ts", 1, "target_user_id"),
        ("user_admin_audit", "idx_user_admin_audit_target_ts", 2, "ts"),
    ]


def _explain(client, consulta, args):
    """EXPLAIN sobre la consulta REAL (LAS CUATRO, indexing): un índice que
    existe no es un índice que se usa. Devuelve {tabla: (type, key, Extra)}."""
    filas = client.portal.call(sql, "EXPLAIN " + consulta, args, True)
    # Columnas de EXPLAIN en MariaDB: id, select_type, table, type,
    # possible_keys, key, key_len, ref, rows, Extra.
    return {f[2]: (f[3], f[5], f[9] or "") for f in filas}


def test_historial_usa_el_indice_target_ts(client):
    plan = _explain(client, user_audit.SQL_HISTORIAL, (123456789, 50))
    tipo, clave, extra = plan["a"]
    assert clave == "idx_user_admin_audit_target_ts", plan
    assert "filesort" not in extra and "temporary" not in extra, plan
    tipo_u, clave_u, _ = plan["u"]
    assert (tipo_u, clave_u) == ("eq_ref", "PRIMARY"), plan


def test_conteo_de_superadmins_usa_el_indice_role_status(client):
    # La consulta REAL del conteo de la invariante (Task 2,
    # api/admin/users.py::otros_superadmins_activos), no una copia a mano.
    # Desde el fix ronda 1 es también la consulta que fija el orden de
    # bloqueos: tiene que recorrer el índice, sin filesort.
    from api.admin.users import SQL_SUPERADMINS_ACTIVOS
    plan = _explain(client, SQL_SUPERADMINS_ACTIVOS, ())
    _, clave, extra = plan["jax_users"]
    assert clave == "idx_jax_users_role_status", plan
    assert "filesort" not in extra and "temporary" not in extra, plan


def test_transaccion_revierte_todo_si_algo_falla(client):
    from db.transaccion import transaccion
    marca = f"test-tx-{uuid.uuid4().hex[:8]}"

    async def falla_a_mitad():
        async with transaccion() as cur:
            await cur.execute("INSERT INTO axioma_config (config_key, config_value) VALUES (%s, 'x')", (marca,))
            raise RuntimeError("a mitad de camino")

    with pytest.raises(RuntimeError):
        client.portal.call(falla_a_mitad)
    ((cuantas,),) = client.portal.call(sql, "SELECT COUNT(*) FROM axioma_config WHERE config_key = %s", (marca,), True)
    assert cuantas == 0
