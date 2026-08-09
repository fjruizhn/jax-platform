"""Bloque D (D1.5 tab 3) — GET/PUT /api/admin/facet-bindings.
D1.2 (contrato de capabilities, antes 'unknown' en C1.2) ya es real: compara
facet.requires_tool_use/min_context_tokens contra
model.supports_tool_use/context_window.
"""
from auth.jwt import create_access_token

USER_ID = "1"  # jax_users.user_id real — PUT hace int(user.user_id) para approved_by
TENANT_ID = "test-facet-bindings-tenant"


def _superadmin_headers():
    token = create_access_token(USER_ID, TENANT_ID, "superadmin")
    return {"Authorization": f"Bearer {token}"}


async def _fetch_model_ref(facet_key):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT provider_id, model_ref FROM facet_binding WHERE facet_key=%s AND role='primary'",
                (facet_key,),
            )
            return await cur.fetchone()


async def _fetch_model_id(provider_id, model_id_str):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT id FROM model WHERE provider_id=%s AND model_id=%s",
                (provider_id, model_id_str),
            )
            row = await cur.fetchone()
            return row[0] if row else None


async def _restore_binding(facet_key, provider_id, model_ref):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE facet_binding SET provider_id=%s, model_ref=%s WHERE facet_key=%s AND role='primary'",
                (provider_id, model_ref, facet_key),
            )
        await conn.commit()


def test_list_facet_bindings_includes_capability_check(client):
    resp = client.get("/api/admin/facet-bindings", headers=_superadmin_headers())
    assert resp.status_code == 200, resp.text
    bindings = {b["facet_key"]: b for b in resp.json()["bindings"]}
    assert "jekyll" in bindings
    assert bindings["jekyll"]["model_id"] == "deepseek-v4-flash"
    assert "capability_check" in bindings["jekyll"]
    assert bindings["jekyll"]["capability_check"] in ("ok", "warning", "unknown")


def test_put_facet_binding_rejects_nonexistent_model_ref(client):
    """D1.6: 'un binding contra un model_ref que no existe en el catalogo es
    rechazado, con motivo visible en la respuesta' — la FK real, no una
    validacion inventada aparte."""
    resp = client.put(
        "/api/admin/facet-bindings/jekyll",
        json={"provider_id": "deepseek", "model_ref": 999999},
        headers=_superadmin_headers(),
    )
    assert resp.status_code in (400, 404, 409, 422)
    assert resp.json().get("detail")  # motivo visible


def test_put_facet_binding_updates_to_a_real_model(client):
    before = client.portal.call(_fetch_model_ref, "jekyll")
    new_model_ref = client.portal.call(_fetch_model_id, "deepseek", "deepseek-v4-flash")
    assert new_model_ref is not None

    try:
        resp = client.put(
            "/api/admin/facet-bindings/jekyll",
            json={"provider_id": "deepseek", "model_ref": new_model_ref},
            headers=_superadmin_headers(),
        )
        assert resp.status_code == 200, resp.text
        after = client.portal.call(_fetch_model_ref, "jekyll")
        assert after == ("deepseek", new_model_ref)
    finally:
        client.portal.call(_restore_binding, "jekyll", before[0], before[1])


def test_put_facet_binding_requires_superadmin(client):
    resp = client.put("/api/admin/facet-bindings/jekyll", json={"provider_id": "deepseek", "model_ref": 1})
    assert resp.status_code in (401, 403)
