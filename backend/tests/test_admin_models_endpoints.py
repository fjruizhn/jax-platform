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
    # max_tokens_param siempre visible: NULL es el estado que hace fallar el
    # dispatch de esa fila (incidente thot 2026-08-24), el superadmin tiene que
    # poder verlo en el catalogo antes de que una faceta se caiga.
    assert "max_tokens_param" in thot_row
    seeded = next(m for m in models if m["model_id"] == "deepseek-v4-flash")
    assert seeded["max_tokens_param"] == "max_tokens"


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


async def _make_pending_proposal(facet_key="jekyll", proposed_ref=None):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT model_ref FROM facet_binding WHERE facet_key=%s AND role='primary'",
                (facet_key,),
            )
            (current_ref,) = await cur.fetchone()
            target_ref = proposed_ref if proposed_ref is not None else current_ref
            await cur.execute(
                "INSERT INTO model_binding_proposal "
                "(facet_key, current_model_ref, proposed_model_ref, reason, detail) "
                "VALUES (%s, %s, %s, 'new_model_available', 'test')",
                (facet_key, current_ref, target_ref),
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


async def _fetch_motor_model_ref(motor_key):
    """Columna cruda de `motor` -- desde 2026-08-24 queda NULL para toda
    clave con faceta homonima (ver
    db.migrations::_eliminate_motor_model_ref_denormalization). Ya no es
    la fuente de identidad; se conserva este helper para probar
    justamente que nadie vuelve a escribirla."""
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT model_ref FROM motor WHERE `key`=%s", (motor_key,))
            row = await cur.fetchone()
            return row[0] if row else None


async def _fetch_motor_resolved_model_ref(motor_key):
    """La vista que list_motors()/MotorCatalog.from_db() consultan de
    verdad -- resuelve por facet_binding.model_ref para una clave con
    faceta homonima, sin importar lo que diga motor.model_ref crudo."""
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT model_ref FROM motor_resolved WHERE `key`=%s", (motor_key,))
            row = await cur.fetchone()
            return row[0] if row else None


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


def test_approve_proposal_resolves_homonymous_motor_via_view_not_raw_column(client):
    """2026-08-19: facet_binding.model_ref / motor.model_ref eran dos
    punteros independientes al mismo recurso para motores homonimos de una
    faceta (jax_local/ada/thot) -- divergieron en horas (qwen 3.6) porque
    nada los sincronizaba. El fix de ese dia (sync de 2 escrituras en este
    endpoint + guard en update_motor) volvio a fallar 5 dias despues por
    un TERCER camino sin guardar (PUT /api/admin/facet-bindings/{key}).

    2026-08-24: en vez de otro guard, motor.model_ref deja de ser fuente
    de identidad para una clave con faceta homonima -- queda NULL a
    proposito (ver migrations.py) y approve_proposal ya NO la toca. Lo
    que hay que probar ahora no es "los dos punteros coinciden" (asuncion
    vieja) sino: (1) motor.model_ref cruda nunca se escribe -- sigue NULL
    -- y (2) motor_resolved (lo que list_motors()/MotorCatalog.from_db()
    consultan de verdad) refleja el cambio de todas formas, vía
    facet_binding."""
    original_ref = client.portal.call(_fetch_binding_model_ref, "jax_local")
    assert client.portal.call(_fetch_motor_model_ref, "jax_local") is None  # precondicion: NULL a proposito
    assert client.portal.call(_fetch_motor_resolved_model_ref, "jax_local") == original_ref

    other_ref = 2 if original_ref != 2 else 3  # cualquier model.id valido distinto del actual

    proposal_id, _ = client.portal.call(_make_pending_proposal, "jax_local", other_ref)
    resp = client.post(f"/api/admin/models/proposals/{proposal_id}/approve", headers=_superadmin_headers())
    assert resp.status_code == 200, resp.text

    assert client.portal.call(_fetch_binding_model_ref, "jax_local") == other_ref
    assert client.portal.call(_fetch_motor_model_ref, "jax_local") is None, (
        "motor.model_ref crudo se escribio -- ya no deberia tocarse, es la vista la que resuelve"
    )
    assert client.portal.call(_fetch_motor_resolved_model_ref, "jax_local") == other_ref, (
        "motor_resolved no siguio a facet_binding.model_ref tras la aprobacion"
    )

    # revertir al valor original para no dejar estado cruzado a otros tests
    revert_id, _ = client.portal.call(_make_pending_proposal, "jax_local", original_ref)
    resp = client.post(f"/api/admin/models/proposals/{revert_id}/approve", headers=_superadmin_headers())
    assert resp.status_code == 200, resp.text
    assert client.portal.call(_fetch_binding_model_ref, "jax_local") == original_ref
    assert client.portal.call(_fetch_motor_resolved_model_ref, "jax_local") == original_ref


async def _raw_write_motor_model_ref(motor_key, model_ref):
    """Simula un escritor futuro sin gobernanza (un '5to camino', ninguno
    de los 4 mapeados en la auditoria de 2026-08-24) escribiendo
    motor.model_ref directo, sin pasar por ningun endpoint ni guard."""
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("UPDATE motor SET model_ref=%s WHERE `key`=%s", (model_ref, motor_key))
        await conn.commit()


def test_raw_write_to_motor_model_ref_cannot_produce_observable_divergence(client):
    """El criterio de cierre real (2026-08-24): un escritor futuro que
    nadie gobierna -- ni guardo, ni sync, ni siquiera un endpoint conocido
    hoy -- no debe poder hacer que motor_resolved diverja de
    facet_binding. No se prueba llamando a un endpoint (ese es
    exactamente el punto: cualquier endpoint futuro que exista o no
    queda cubierto), se prueba escribiendo motor.model_ref crudo por
    fuera de todo el codigo de aplicacion, como si fuera un script, una
    migracion, o un admin con acceso directo a la DB."""
    binding_ref = client.portal.call(_fetch_binding_model_ref, "jax_local")
    rogue_ref = 2 if binding_ref != 2 else 3  # cualquier model.id valido, deliberadamente distinto

    client.portal.call(_raw_write_motor_model_ref, "jax_local", rogue_ref)

    # la escritura cruda SI se guardo -- no estamos probando un guard que
    # la rechace, estamos probando que no importa que se haya guardado
    assert client.portal.call(_fetch_motor_model_ref, "jax_local") == rogue_ref

    # pero nada que lea identidad de modelo para 'jax_local' la ve: la
    # vista sigue resolviendo por facet_binding, ignorando por completo
    # el valor que se acaba de escribir
    assert client.portal.call(_fetch_motor_resolved_model_ref, "jax_local") == binding_ref
    resp = client.get("/api/admin/motors", headers=_superadmin_headers())
    assert resp.status_code == 200, resp.text
    jax_local_row = next(m for m in resp.json()["motors"] if m["key"] == "jax_local")
    assert jax_local_row["model_id"] != None  # sanity: la fila resuelve, no desaparecio

    # limpiar: volver a NULL, estado que la migracion establece
    client.portal.call(_raw_write_motor_model_ref, "jax_local", None)
    assert client.portal.call(_fetch_motor_model_ref, "jax_local") is None
