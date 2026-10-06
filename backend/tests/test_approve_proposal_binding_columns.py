import asyncio

import api.admin.models as admin_models


def test_aprobacion_copia_provider_y_model_id_desde_model_ref():
    class Cursor:
        def __init__(self):
            self.sql = None
            self.parameters = None

        async def execute(self, sql, parameters):
            self.sql = sql
            self.parameters = parameters

    cursor = Cursor()
    asyncio.run(admin_models._actualizar_binding_aprobado(
        cursor, facet_key="jekyll", model_ref=73, approved_by=9,
    ))

    compact_sql = " ".join(cursor.sql.split())
    assert "provider_id=m.provider_id" in compact_sql
    assert "model_id=m.model_id" in compact_sql
    assert cursor.parameters == (73, 9, "jekyll")


async def _q(sql, parameters=(), commit=False):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(sql, parameters)
            rows = await cur.fetchall()
        if commit:
            await conn.commit()
    return rows


async def _crear_modelo_y_propuesta():
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO model (provider_id, model_id, source, source_checked_at) "
                "VALUES ('ollama', 'test-approve-binding-columns', 'manual', NOW())"
            )
            model_ref = cur.lastrowid
            await cur.execute(
                "SELECT model_ref, provider_id, model_id, approved_by, approved_at "
                "FROM facet_binding WHERE facet_key='jax_local' AND role='primary'"
            )
            binding_before = await cur.fetchone()
            await cur.execute(
                "INSERT INTO model_binding_proposal "
                "(facet_key, current_model_ref, proposed_model_ref, reason, detail) "
                "VALUES ('jax_local', %s, %s, 'drift_detected', 'binding columns regression')",
                (binding_before[0], model_ref),
            )
            proposal_id = cur.lastrowid
        await conn.commit()
    return model_ref, proposal_id, binding_before


async def _binding_identity():
    rows = await _q(
        "SELECT model_ref, provider_id, model_id FROM facet_binding "
        "WHERE facet_key='jax_local' AND role='primary'"
    )
    return rows[0]


async def _limpiar(model_ref, proposal_id, binding_before):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE facet_binding SET model_ref=%s, provider_id=%s, model_id=%s, "
                "approved_by=%s, approved_at=%s WHERE facet_key='jax_local' AND role='primary'",
                binding_before,
            )
            await cur.execute("DELETE FROM model_catalog_audit WHERE proposal_id=%s", (proposal_id,))
            await cur.execute("DELETE FROM model_binding_proposal WHERE id=%s", (proposal_id,))
            await cur.execute("DELETE FROM model WHERE id=%s", (model_ref,))
        await conn.commit()


def test_approve_sincroniza_identidad_del_binding_en_db_real(client, monkeypatch):
    """Comprueba las columnas persistidas después de aprobar una propuesta real."""
    import jax_engine.background as background
    from auth.jwt import create_access_token

    monkeypatch.setattr(background, "add_safe_task", lambda *args, **kwargs: None)
    model_ref, proposal_id, binding_before = client.portal.call(_crear_modelo_y_propuesta)
    try:
        response = client.post(
            f"/api/admin/models/proposals/{proposal_id}/approve",
            headers={"Authorization": f"Bearer {create_access_token('1', '1', 'superadmin')}"},
        )
        assert response.status_code == 200, response.text
        assert client.portal.call(_binding_identity) == (
            model_ref, "ollama", "test-approve-binding-columns",
        )
    finally:
        client.portal.call(_limpiar, model_ref, proposal_id, binding_before)
