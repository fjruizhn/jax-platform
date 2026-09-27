"""model_catalog_audit -- 'sync_forzado' + model_ref nullable (MAJOR-3(b),
segunda auditoría adversarial, 2026-09-27).

'sync_forzado' audita que un superadmin saltó el guardián de "lista
encogida" para uno o más proveedores -- una acción sobre un PROVEEDOR, no
sobre una fila puntual de `model`, así que `model_ref` (NOT NULL en las dos
acciones originales) se afloja a NULL en vez de inventar un valor de
relleno.
"""
from db import migrations


async def _column_info(cur, table, column):
    await cur.execute(
        "SELECT IS_NULLABLE, COLUMN_TYPE FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s",
        (table, column),
    )
    return await cur.fetchone()


def test_action_incluye_sync_forzado(client):
    async def _verificar():
        from db.connection import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                _nullable, tipo = await _column_info(cur, "model_catalog_audit", "action")
                assert "sync_forzado" in tipo
    client.portal.call(_verificar)


def test_model_ref_es_nullable(client):
    async def _verificar():
        from db.connection import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                nullable, _tipo = await _column_info(cur, "model_catalog_audit", "model_ref")
                assert nullable == "YES"
    client.portal.call(_verificar)


def test_correr_run_migrations_dos_veces_no_falla(client):
    async def _verificar():
        await migrations.run_migrations()  # segunda corrida, misma sesión
        from db.connection import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                nullable, tipo = await _column_info(cur, "model_catalog_audit", "model_ref")
                assert nullable == "YES"
                _n2, tipo_action = await _column_info(cur, "model_catalog_audit", "action")
                assert "sync_forzado" in tipo_action
    client.portal.call(_verificar)


def test_insertar_sync_forzado_con_model_ref_nulo(client):
    """El caso real: una fila 'sync_forzado' sin model_ref, con
    provider_id y los JSON de antes/después."""
    async def _insertar_y_leer():
        import json
        from db.connection import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO model_catalog_audit (action, model_ref, provider_id, valor_antes, "
                    "valor_despues, performed_by, performed_by_email, performed_from_ip) "
                    "VALUES ('sync_forzado', NULL, %s, %s, %s, %s, %s, %s)",
                    ("zhipu", json.dumps({"disponibles_antes": 1}), json.dumps({"fetched": 5}),
                     1, "test@example.com", "127.0.0.1"),
                )
                id_fila = cur.lastrowid
            await conn.commit()
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT action, model_ref, provider_id, valor_antes, valor_despues "
                    "FROM model_catalog_audit WHERE id=%s", (id_fila,))
                fila = await cur.fetchone()
            async with conn.cursor() as cur:
                await cur.execute("DELETE FROM model_catalog_audit WHERE id=%s", (id_fila,))
            await conn.commit()
        return fila

    action, model_ref, provider_id, valor_antes, valor_despues = client.portal.call(_insertar_y_leer)
    assert action == "sync_forzado"
    assert model_ref is None
    assert provider_id == "zhipu"
    assert '"disponibles_antes": 1' in valor_antes
    assert '"fetched": 5' in valor_despues
