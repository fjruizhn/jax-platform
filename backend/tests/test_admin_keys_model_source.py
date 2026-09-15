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
from tests.identidades import cabeceras

TENANT_ID = "test-admin-keys-model-source-tenant"


def _superadmin_headers(client):
    return cabeceras(client, "admin-keys-model-source", "superadmin", TENANT_ID)


async def _thot_active_facet_models_ids():
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT id FROM facet_models WHERE facet='thot' AND is_active=TRUE"
            )
            return [r[0] for r in await cur.fetchall()]


async def _modelo_real_de_thot():
    """El model_id que facet_binding (via model_ref) le da hoy a thot en ESTA
    base: en jax_memory_test local es el que haya dejado su historia, en la
    base virgen de CI el de la semilla (gpt-5.6-terra desde PR-L ronda 1). El
    test fija la regla -- se muestra el binding real, no un literal --, no un
    nombre de modelo."""
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT m.model_id FROM facet_binding b JOIN model m ON m.id = b.model_ref "
                "WHERE b.facet_key='thot' AND b.role='primary'"
            )
            (model_id,) = await cur.fetchone()
            return model_id


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
        resp = client.get("/api/admin/keys", headers=_superadmin_headers(client))
    finally:
        client.portal.call(_set_active, active_ids, True)

    assert resp.status_code == 200, resp.text
    by_id = {p["id"]: p for p in resp.json()["providers"]}
    real = client.portal.call(_modelo_real_de_thot)
    assert real != "gpt-4o"
    assert by_id["openai"]["model"] == real  # el binding real, NO "gpt-4o" (bug D0)


# PR-L ronda 2 (2026-09-14, punto 3 de la revisión): el modelo de cada
# proveedor sale SOLO de facet_binding. Sin binding, `model` es None y la UI
# muestra "sin dato" traducido: nunca un literal (gpt-4o / kimi-k2.7-code /
# glm-5.2, ya desfasados) ni la tabla legacy facet_models.

def test_providers_no_tiene_literales_de_modelo():
    from api.admin import keys
    assert all("model" not in p for p in keys.PROVIDERS), keys.PROVIDERS


def test_sin_binding_el_modelo_es_none_aunque_haya_fila_legacy(client, monkeypatch):
    from api.admin import keys

    async def sin_bindings(pool, facets):
        return {}
    monkeypatch.setattr(keys, "_get_binding_models_batch", sin_bindings)
    ids = client.portal.call(_thot_active_facet_models_ids)
    if not ids:  # que haya una fila legacy activa para thot, o el test no prueba nada
        ids = client.portal.call(_crear_fila_legacy_activa_de_thot)
        creada = True
    else:
        creada = False
    try:
        resp = client.get("/api/admin/keys", headers=_superadmin_headers(client))
        assert resp.status_code == 200, resp.text
        modelos = {p["id"]: p["model"] for p in resp.json()["providers"]}
        assert set(modelos.values()) == {None}, modelos
    finally:
        if creada:
            client.portal.call(_borrar_facet_models, ids)


async def _crear_fila_legacy_activa_de_thot():
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO facet_models (facet, provider_id, model_name, is_active) "
                "VALUES ('thot', 'openai', 'modelo-legacy-prl-r2', TRUE)")
            nuevo = cur.lastrowid
        await conn.commit()
    return [nuevo]


async def _borrar_facet_models(ids):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            for row_id in ids:
                await cur.execute("DELETE FROM facet_models WHERE id=%s", (row_id,))
        await conn.commit()
