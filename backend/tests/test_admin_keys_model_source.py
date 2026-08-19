"""Bloque D (D0/D1.5) — cierra el bug real: admin/keys.py:18 mostraba
'gpt-4o' hardcodeado para thot cuando no habia fila activa en `facet_models`
(tabla legacy) y la realidad operativa (facet_binding, Bloque C) es
'gpt-5.5'. GET /api/admin/keys debe preferir facet_binding/model sobre el
literal de PROVIDERS.

jax_memory_test es persistente entre corridas de pytest (no se recrea) y
otro test (test_facet_model_wiring.py) escribe filas reales en
facet_models — por eso el escenario se fuerza explicitamente (desactivar
cualquier fila activa de 'thot' en facet_models) en vez de asumir que la
tabla legacy esta vacia. Restaura el estado previo en el finally.
"""
from auth.jwt import create_access_token

USER_ID = "test-admin-keys-model-source-user"
TENANT_ID = "test-admin-keys-model-source-tenant"


def _superadmin_headers():
    token = create_access_token(USER_ID, TENANT_ID, "superadmin")
    return {"Authorization": f"Bearer {token}"}


async def _thot_active_facet_models_ids():
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT id FROM facet_models WHERE facet='thot' AND is_active=TRUE"
            )
            return [r[0] for r in await cur.fetchall()]


async def _set_active(ids, active):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            for row_id in ids:
                await cur.execute(
                    "UPDATE facet_models SET is_active=%s WHERE id=%s", (active, row_id)
                )
        await conn.commit()


def test_list_keys_falls_back_to_facet_binding_when_legacy_table_has_no_active_row(client):
    """Reproduce el escenario real del bug D0: sin fila activa en
    facet_models (legacy), el endpoint debe mostrar el modelo REAL de
    facet_binding (gpt-5.5), nunca el literal hardcodeado 'gpt-4o'."""
    active_ids = client.portal.call(_thot_active_facet_models_ids)
    client.portal.call(_set_active, active_ids, False)
    try:
        resp = client.get("/api/admin/keys", headers=_superadmin_headers())
    finally:
        client.portal.call(_set_active, active_ids, True)

    assert resp.status_code == 200, resp.text
    by_id = {p["id"]: p for p in resp.json()["providers"]}
    assert by_id["openai"]["model"] == "gpt-5.5"  # NO "gpt-4o" (bug D0)
