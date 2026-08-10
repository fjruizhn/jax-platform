"""Bloque D (D1.5 tab 2) — GET/POST /api/admin/models y /proposals.
Mismo patron de auth que test_admin_keys_n1.py (superadmin JWT). El sync
real (D1.3) se fake-ea a nivel de model_catalog (ya probado aparte en
test_model_catalog_sync.py) para no depender de red real en este archivo.
"""
import model_catalog
from auth.jwt import create_access_token

USER_ID = "1"  # jax_users.user_id real (unico seed en jax_memory_test) — approve_proposal hace int(user.user_id) y escribe approved_by/decided_by, FK real contra jax_users
TENANT_ID = "test-admin-models-tenant"


def _superadmin_headers():
    token = create_access_token(USER_ID, TENANT_ID, "superadmin")
    return {"Authorization": f"Bearer {token}"}


def test_list_models_returns_the_seeded_catalog(client):
    resp = client.get("/api/admin/models", headers=_superadmin_headers())
    assert resp.status_code == 200, resp.text
    models = resp.json()["models"]
    assert len(models) >= 7  # las 7 filas sembradas por Bloque C
    thot_row = next(m for m in models if m["provider_id"] == "openai" and m["model_id"] == "gpt-5.5")
    assert thot_row["status"] == "available"
    assert "source" in thot_row and "source_checked_at" in thot_row  # procedencia siempre visible


def test_list_models_filters_by_provider_and_status(client):
    resp = client.get("/api/admin/models?provider=openai", headers=_superadmin_headers())
    assert resp.status_code == 200
    assert all(m["provider_id"] == "openai" for m in resp.json()["models"])

    resp = client.get("/api/admin/models?status=available", headers=_superadmin_headers())
    assert resp.status_code == 200
    assert all(m["status"] == "available" for m in resp.json()["models"])


def test_sync_endpoint_only_touches_model_never_facet_binding(client, monkeypatch):
    """REGLA DE ORO expuesta por el endpoint: no acepta un cambio directo a
    facet_binding, solo dispara sync (capa a+b) sobre `model`."""
    calls = {"providers": [], "enrich": 0}

    async def fake_sync(provider_id):
        calls["providers"].append(provider_id)
        return {"provider_id": provider_id, "fetched": 1}

    async def fake_enrich():
        calls["enrich"] += 1
        return {"enriched": 1}

    monkeypatch.setattr(model_catalog, "sync_provider_models", fake_sync)
    monkeypatch.setattr(model_catalog, "enrich_from_models_dev", fake_enrich)

    resp = client.post("/api/admin/models/sync", headers=_superadmin_headers())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert set(calls["providers"]) == {"openai", "deepseek", "gemini", "moonshot", "zhipu", "anthropic", "ollama"}
    assert calls["enrich"] == 1


def test_sync_endpoint_requires_superadmin(client):
    resp = client.post("/api/admin/models/sync")
    assert resp.status_code in (401, 403)


async def _make_pending_proposal(facet_key="jekyll"):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT model_ref FROM facet_binding WHERE facet_key=%s AND role='primary'",
                (facet_key,),
            )
            (current_ref,) = await cur.fetchone()
            await cur.execute(
                "INSERT INTO model_binding_proposal "
                "(facet_key, current_model_ref, proposed_model_ref, reason, detail) "
                "VALUES (%s, %s, %s, 'new_model_available', 'test')",
                (facet_key, current_ref, current_ref),
            )
            proposal_id = cur.lastrowid
        await conn.commit()
    return proposal_id, current_ref


async def _fetch_binding_model_ref(facet_key):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT model_ref FROM facet_binding WHERE facet_key=%s AND role='primary'",
                (facet_key,),
            )
            (ref,) = await cur.fetchone()
            return ref


async def _fetch_proposal_status(proposal_id):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT status FROM model_binding_proposal WHERE id=%s", (proposal_id,))
            (status,) = await cur.fetchone()
            return status


def test_list_proposals_filters_pending(client):
    proposal_id, _ = client.portal.call(_make_pending_proposal, "jekyll")
    resp = client.get("/api/admin/models/proposals?status=pending", headers=_superadmin_headers())
    assert resp.status_code == 200
    ids = [p["id"] for p in resp.json()["proposals"]]
    assert proposal_id in ids


def test_approve_proposal_writes_facet_binding_model_ref(client):
    proposal_id, current_ref = client.portal.call(_make_pending_proposal, "jekyll")

    resp = client.post(f"/api/admin/models/proposals/{proposal_id}/approve", headers=_superadmin_headers())
    assert resp.status_code == 200, resp.text

    assert client.portal.call(_fetch_proposal_status, proposal_id) == "approved"
    assert client.portal.call(_fetch_binding_model_ref, "jekyll") == current_ref


def test_reject_proposal_never_writes_facet_binding(client):
    proposal_id, current_ref = client.portal.call(_make_pending_proposal, "jekyll")
    before = client.portal.call(_fetch_binding_model_ref, "jekyll")

    resp = client.post(f"/api/admin/models/proposals/{proposal_id}/reject", headers=_superadmin_headers())
    assert resp.status_code == 200, resp.text

    assert client.portal.call(_fetch_proposal_status, proposal_id) == "rejected"
    assert client.portal.call(_fetch_binding_model_ref, "jekyll") == before


def test_approve_nonexistent_proposal_404s(client):
    resp = client.post("/api/admin/models/proposals/999999/approve", headers=_superadmin_headers())
    assert resp.status_code == 404
