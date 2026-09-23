"""Bloque D (D1.5 tab 2) — GET/POST /api/admin/models y /proposals.
Mismo patron de auth que test_admin_keys_n1.py (superadmin JWT). El sync
real (D1.3) se fake-ea a nivel de model_catalog (ya probado aparte en
test_model_catalog_sync.py) para no depender de red real en este archivo.
"""
import model_catalog
from auth.jwt import create_access_token

USER_ID = "1"  # jax_users.user_id real (unico seed en jax_memory_test) — approve_proposal hace int(user.user_id) y escribe approved_by/decided_by, FK real contra jax_users
TENANT_ID = "1"  # DB-backed tenant of user_id=1.


def _superadmin_headers():
    token = create_access_token(USER_ID, TENANT_ID, "superadmin")
    return {"Authorization": f"Bearer {token}"}


async def _modelo_de_thot():
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT m.provider_id, m.model_id FROM facet_binding b JOIN model m ON m.id = b.model_ref "
                "WHERE b.facet_key='thot' AND b.role='primary'"
            )
            return await cur.fetchone()


async def _contrato_en_db(model_ref):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT max_tokens_param, max_output_tokens FROM model WHERE id=%s", (model_ref,))
            return await cur.fetchone()


def test_list_models_returns_the_seeded_catalog(client):
    resp = client.get("/api/admin/models", headers=_superadmin_headers())
    assert resp.status_code == 200, resp.text
    models = resp.json()["models"]
    assert len(models) >= 7  # las 7 filas sembradas por Bloque C
    # La fila de thot es la de su binding ACTUAL, leída de la base, no un
    # literal (PR-L ronda 1): la semilla de base vacía pasó de gpt-5.5 a
    # gpt-5.6-terra, así que en la base virgen de CI gpt-5.5 no existe y en
    # el jax_memory_test local (sembrado antes) gpt-5.6-terra puede no existir.
    thot_provider, thot_model = client.portal.call(_modelo_de_thot)
    thot_row = next(m for m in models if m["provider_id"] == thot_provider and m["model_id"] == thot_model)
    assert thot_row["status"] == "available"
    assert "source" in thot_row and "source_checked_at" in thot_row  # procedencia siempre visible
    # max_tokens_param siempre visible: NULL es el estado que hace fallar el
    # dispatch de esa fila (incidente thot 2026-08-24), el superadmin tiene que
    # poder verlo en el catalogo antes de que una faceta se caiga.
    assert "max_tokens_param" in thot_row
    # max_output_tokens (par del anterior): mismo motivo, mismo estado roto.
    assert "max_output_tokens" in thot_row
    # El endpoint muestra lo que la fila TIENE (PR-L ronda 2): se compara con
    # la base, no con el valor sembrado -- la fila es mutable (el endpoint de
    # PR-L la escribe) y afirmar 131072 hacía depender el test de quién la
    # tocó antes. Que la semilla ponga 131072 lo fija test_model_max_output_tokens.
    seeded = next(m for m in models if m["model_id"] == "deepseek-v4-flash")
    en_db = client.portal.call(_contrato_en_db, seeded["id"])
    assert (seeded["max_tokens_param"], seeded["max_output_tokens"]) == en_db


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
    assert body["providers_fallidos"] == [] and body["enrich_fallido"] is False
    assert "code" not in body
    assert set(calls["providers"]) == {"openai", "deepseek", "gemini", "moonshot", "zhipu", "anthropic", "ollama"}
    assert calls["enrich"] == 1


def test_sync_con_todos_los_providers_caidos_no_dice_ok(client, monkeypatch):
    """Task 3 (2026-09-15), clase (b): el `except` por provider esta bien (uno
    caido no frena a los demas), pero la respuesta decia `ok: True` aunque
    fallaran TODOS -- y el frontend no leia el cuerpo."""
    async def falla(provider_id):
        raise RuntimeError(f"{provider_id} caido")

    async def fake_enrich():
        return {"enriched": 1}

    monkeypatch.setattr(model_catalog, "sync_provider_models", falla)
    monkeypatch.setattr(model_catalog, "enrich_from_models_dev", fake_enrich)

    resp = client.post("/api/admin/models/sync", headers=_superadmin_headers())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is False
    assert body["code"] == "sync_con_errores"
    assert set(body["providers_fallidos"]) == {"openai", "deepseek", "gemini", "moonshot", "zhipu", "anthropic", "ollama"}
    assert body["enrich_fallido"] is False


def test_sync_con_el_enriquecimiento_caido_no_dice_ok(client, monkeypatch):
    async def fake_sync(provider_id):
        return {"provider_id": provider_id, "fetched": 1}

    async def falla_enrich():
        raise RuntimeError("models.dev caido")

    monkeypatch.setattr(model_catalog, "sync_provider_models", fake_sync)
    monkeypatch.setattr(model_catalog, "enrich_from_models_dev", falla_enrich)

    body = client.post("/api/admin/models/sync", headers=_superadmin_headers()).json()
    assert body["ok"] is False
    assert body["code"] == "sync_con_errores"
    assert body["providers_fallidos"] == []
    assert body["enrich_fallido"] is True
    assert "error" in body["enrich"]


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

    # Un modelo DEL MISMO proveedor que jax_local (ollama). Hasta 2026-09-14
    # esto era "model.id 2 o 3" -- en jax_memory_test, 'sonnet' de anthropic:
    # el test aprobaba un binding con provider_id=ollama y un modelo de
    # anthropic, justo lo que el guard de la ronda 1 de PR-J rechaza (409
    # modelo_de_otro_proveedor). Lo que este test prueba (la vista sigue a
    # facet_binding) no depende del proveedor.
    other_ref = client.portal.call(_crear_modelo_ollama_de_prueba)
    try:
        _aprobar_y_verificar_vista(client, original_ref, other_ref)
    finally:
        client.portal.call(_borrar_modelo_de_prueba, other_ref)


async def _crear_modelo_ollama_de_prueba():
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT IGNORE INTO model (provider_id, model_id, source, source_checked_at) "
                "VALUES ('ollama', 'test-vista-motor-otro-modelo', 'manual', NOW())")
            await cur.execute(
                "SELECT id FROM model WHERE provider_id='ollama' AND model_id='test-vista-motor-otro-modelo'")
            (ref,) = await cur.fetchone()
        await conn.commit()
    return ref


async def _borrar_modelo_de_prueba(ref):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "DELETE FROM model_binding_proposal WHERE proposed_model_ref=%s OR current_model_ref=%s",
                (ref, ref))
            await cur.execute("DELETE FROM model WHERE id=%s", (ref,))
        await conn.commit()


def _aprobar_y_verificar_vista(client, original_ref, other_ref):
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


async def _crear_modelo_ajeno(provider_id, model_id):
    """Un `model.id` REAL y propio de esta prueba, no un entero adivinado.

    Hallazgo 2026-09-21 (CI del PR #140, `backend-tests-con-db`): este test
    usaba `rogue_ref = 2 if binding_ref != 2 else 3` -- asumía que uno de
    esos dos ids SIEMPRE existe. `model.id` no es un contrato de nadie:
    ningún seed promete que sea contiguo ni que empiece bajo, y MariaDB
    DEJA HUECOS a propósito en AUTO_INCREMENT (un `INSERT IGNORE` que choca
    con una fila que otro camino ya sembró consume igual un id, y ese id
    nunca se reusa -- comprobado reproduciendo contra una base virgen real:
    `model` quedó con ids (1, 3, 4, 5, 6, 7, 8), sin el 2). En la base de
    sesión de hall9000 (clonada con 16 filas de catálogo) el hueco no se
    nota porque 2 y 3 casi siempre están ocupados por filas reales; en una
    base virgen -- exactamente lo que corre en CI -- no hay ninguna garantía.
    Se crea la fila acá, se usa su id real, y se borra al terminar."""
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO model (provider_id, model_id, source, source_checked_at) "
                "VALUES (%s, %s, 'manual', NOW())", (provider_id, model_id),
            )
            model_ref = cur.lastrowid
        await conn.commit()
    return model_ref


async def _borrar_modelo(model_ref):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM model WHERE id=%s", (model_ref,))
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
    rogue_ref = client.portal.call(_crear_modelo_ajeno, "openai", "test-rogue-ref-motor")
    assert rogue_ref != binding_ref, "el id sintético coincidió con el del binding -- no prueba nada"

    try:
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
    finally:
        # limpiar: volver a NULL, estado que la migracion establece, y borrar
        # la fila sintética ANTES de que nada más pueda referenciarla (FK).
        client.portal.call(_raw_write_motor_model_ref, "jax_local", None)
        client.portal.call(_borrar_modelo, rogue_ref)
    assert client.portal.call(_fetch_motor_model_ref, "jax_local") is None
