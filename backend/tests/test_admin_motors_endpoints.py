"""R4 Task 9 (ultima tarea del plan) — GET/POST/PATCH /api/admin/motors.

Cablea el mismo mecanismo ya probado por INSERT directo en Task 4/8 (motor
kimi/ada/jax_local/thot sembrados via db/migrations.py): una fila en
`motor` + N filas en `capability_motor`, cero codigo de despacho nuevo.
Mismo patron de auth/estructura que test_admin_models_endpoints.py y
test_admin_facet_bindings_endpoints.py (superadmin JWT).

PATCH agregado 2026-08-19 (edicion, hueco real del alcance original
crear+listar) — ver docstring de update_motor en el router para el guard
anti-divergencia con facet_binding.
"""
from auth.jwt import create_access_token

USER_ID = "1"
TENANT_ID = "test-admin-motors-tenant"


def _superadmin_headers():
    token = create_access_token(USER_ID, TENANT_ID, "superadmin")
    return {"Authorization": f"Bearer {token}"}


def _user_headers():
    token = create_access_token(USER_ID, TENANT_ID, "user")
    return {"Authorization": f"Bearer {token}"}


async def _cleanup_motor(key):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM capability_motor WHERE motor_key=%s", (key,))
            await cur.execute("DELETE FROM motor WHERE `key`=%s", (key,))
        await conn.commit()


async def _fetch_motor_row(key):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT m.transport, mo.provider_id, mo.model_id, m.status "
                "FROM motor m JOIN model mo ON mo.id = m.model_ref WHERE m.`key`=%s",
                (key,),
            )
            return await cur.fetchone()


async def _db_fetch_one(sql, params=()):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(sql, params)
            return await cur.fetchone()


async def _db_exec(sql, params=()):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(sql, params)
        await conn.commit()


async def _fetch_capability_motor_rows(key):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT capability_key, priority FROM capability_motor WHERE motor_key=%s ORDER BY capability_key",
                (key,),
            )
            return await cur.fetchall()


def test_list_motors_includes_the_four_seeded_motors(client):
    resp = client.get("/api/admin/motors", headers=_superadmin_headers())
    assert resp.status_code == 200, resp.text
    motors = {m["key"]: m for m in resp.json()["motors"]}
    for expected_key in ("kimi", "ada", "jax_local", "thot"):
        assert expected_key in motors, motors.keys()
    assert motors["kimi"]["transport"] == "http_openai_compat"
    assert motors["kimi"]["dispatchable"] is True
    assert motors["jax_local"]["transport"] == "ollama"
    assert motors["jax_local"]["dispatchable"] is True
    # thot completa validate_consistency/critique (Task 8, criterio #4)
    thot_caps = {c["capability_key"] for c in motors["thot"]["capabilities"]}
    assert "critique" in thot_caps
    assert "validate_consistency" in thot_caps


def test_list_motors_requires_superadmin(client):
    resp = client.get("/api/admin/motors")
    assert resp.status_code in (401, 403)

    resp = client.get("/api/admin/motors", headers=_user_headers())
    assert resp.status_code == 403


def test_create_motor_requires_superadmin(client):
    resp = client.post(
        "/api/admin/motors",
        json={"key": "should_not_exist", "provider_id": "deepseek", "model_id": "deepseek-v4-flash",
              "transport": "http_openai_compat"},
    )
    assert resp.status_code in (401, 403)


def test_create_motor_inserts_row_and_attaches_capability(client):
    """Verificacion decisiva del plan (misma forma que Task 8): dar de alta
    un motor real (deepseek, distinto de kimi/ada/jax_local/thot) via el
    endpoint deja una fila en `motor` y N en `capability_motor`, listas
    para que MotorCatalog.from_db() (las_manos) las despache -- sin tocar
    ningun codigo de dispatch."""
    key = "deepseek_admin_test"
    try:
        resp = client.post(
            "/api/admin/motors",
            json={
                "key": key,
                "provider_id": "deepseek",
                "model_id": "deepseek-v4-flash",
                "transport": "http_openai_compat",
                "max_tokens": 4000,
                "default_timeout_seconds": 300,
                "capabilities": [{"capability_key": "generate", "priority": 9}],
            },
            headers=_superadmin_headers(),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ok"] is True
        assert body["key"] == key
        assert body["dispatchable"] is True

        row = client.portal.call(_fetch_motor_row, key)
        assert row == ("http_openai_compat", "deepseek", "deepseek-v4-flash", "active")

        cap_rows = client.portal.call(_fetch_capability_motor_rows, key)
        assert cap_rows == (("generate", 9),)
    finally:
        client.portal.call(_cleanup_motor, key)


def test_create_motor_documents_known_transport_limitation(client):
    """R4 Task 9 Step 7: el form acepta cualquier transport del ENUM (misma
    paridad que motor.transport en DB) pero solo 'http_openai_compat' y
    'ollama' tienen dispatcher implementado hoy (worker.py
    _TRANSPORT_DISPATCH) -- limitacion conocida, documentada via el campo
    'dispatchable' en la respuesta en vez de bloquear el alta."""
    key = "gemini_admin_test"
    try:
        resp = client.post(
            "/api/admin/motors",
            json={
                "key": key,
                "provider_id": "gemini",
                "model_id": "gemini-2.5-flash",
                "transport": "http_gemini",
            },
            headers=_superadmin_headers(),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["dispatchable"] is False
    finally:
        client.portal.call(_cleanup_motor, key)


def test_create_motor_rejects_duplicate_key(client):
    resp = client.post(
        "/api/admin/motors",
        json={"key": "kimi", "provider_id": "moonshot", "model_id": "kimi-k3", "transport": "http_openai_compat"},
        headers=_superadmin_headers(),
    )
    assert resp.status_code == 409
    assert resp.json().get("detail")


def test_create_motor_rejects_unknown_model(client):
    resp = client.post(
        "/api/admin/motors",
        json={"key": "nope_motor", "provider_id": "openai", "model_id": "does-not-exist",
              "transport": "http_openai_compat"},
        headers=_superadmin_headers(),
    )
    assert resp.status_code == 400
    assert "catalogo" in resp.json()["detail"] or "catálogo" in resp.json()["detail"]


def test_update_motor_requires_superadmin(client):
    resp = client.patch("/api/admin/motors/kimi", json={"max_tokens": 1})
    assert resp.status_code in (401, 403)


def test_update_motor_404s_for_unknown_key(client):
    resp = client.patch(
        "/api/admin/motors/no_such_motor", json={"max_tokens": 1}, headers=_superadmin_headers(),
    )
    assert resp.status_code == 404


def test_update_motor_requires_provider_id_and_model_id_together(client):
    resp = client.patch(
        "/api/admin/motors/kimi", json={"model_id": "kimi-k3"}, headers=_superadmin_headers(),
    )
    assert resp.status_code == 422


def test_update_motor_rejects_unknown_model(client):
    key = "deepseek_admin_test"
    try:
        client.portal.call(_cleanup_motor, key)
        client.post(
            "/api/admin/motors",
            json={"key": key, "provider_id": "deepseek", "model_id": "deepseek-v4-flash",
                  "transport": "http_openai_compat"},
            headers=_superadmin_headers(),
        )
        resp = client.patch(
            f"/api/admin/motors/{key}",
            json={"provider_id": "deepseek", "model_id": "does-not-exist"},
            headers=_superadmin_headers(),
        )
        assert resp.status_code == 400
        assert "catalogo" in resp.json()["detail"] or "catálogo" in resp.json()["detail"]
    finally:
        client.portal.call(_cleanup_motor, key)


def test_update_motor_changes_model_for_a_motor_without_facet(client):
    """El hueco real que esto cierra: un motor SIN faceta homonima (todo
    motor sembrado hoy -- kimi/ada/jax_local/thot -- tiene faceta; este es
    el caso que antes solo se podia arreglar con SQL a mano)."""
    key = "deepseek_admin_test"
    try:
        client.portal.call(_cleanup_motor, key)
        client.post(
            "/api/admin/motors",
            json={"key": key, "provider_id": "deepseek", "model_id": "deepseek-v4-flash",
                  "transport": "http_openai_compat"},
            headers=_superadmin_headers(),
        )
        resp = client.patch(
            f"/api/admin/motors/{key}",
            json={"provider_id": "openai", "model_id": "gpt-5.5", "max_tokens": 999},
            headers=_superadmin_headers(),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["ok"] is True

        row = client.portal.call(_fetch_motor_row, key)
        assert row == ("http_openai_compat", "openai", "gpt-5.5", "active")
    finally:
        client.portal.call(_cleanup_motor, key)


def test_update_motor_rejects_model_change_for_a_key_with_homonymous_facet(client):
    """El guard central: jax_local tiene faceta -- cambiar su modelo via
    este endpoint reintroduciria en horas la misma divergencia
    motor/facet_binding que se encontro y corrigio el 2026-08-19. Debe
    rechazarse, y motor.model_ref debe quedar intacto."""
    before = client.portal.call(_fetch_motor_row, "jax_local")

    resp = client.patch(
        "/api/admin/motors/jax_local",
        json={"provider_id": "openai", "model_id": "gpt-5.5"},
        headers=_superadmin_headers(),
    )
    assert resp.status_code == 409, resp.text
    assert "faceta" in resp.json()["detail"]

    after = client.portal.call(_fetch_motor_row, "jax_local")
    assert after == before  # nada cambio


def test_update_motor_allows_non_model_fields_for_a_key_with_homonymous_facet(client):
    """El guard es solo sobre el modelo -- transport/timeout/etc no los
    rastrea facet_binding, se pueden editar libremente."""
    before = client.portal.call(_fetch_motor_row, "jax_local")
    original_max_tokens = client.portal.call(
        _db_fetch_one, "SELECT max_tokens FROM motor WHERE `key`='jax_local'",
    )[0]
    try:
        resp = client.patch(
            "/api/admin/motors/jax_local", json={"max_tokens": 12345}, headers=_superadmin_headers(),
        )
        assert resp.status_code == 200, resp.text
        row = client.portal.call(
            _db_fetch_one, "SELECT max_tokens FROM motor WHERE `key`='jax_local'",
        )
        assert row == (12345,)
    finally:
        # revertir para no dejar estado cruzado a otros tests
        client.portal.call(
            _db_exec, "UPDATE motor SET max_tokens=%s WHERE `key`='jax_local'", (original_max_tokens,),
        )
        assert client.portal.call(_fetch_motor_row, "jax_local") == before


def test_create_motor_rejects_unknown_capability(client):
    key = "orphan_cap_motor"
    try:
        resp = client.post(
            "/api/admin/motors",
            json={
                "key": key,
                "provider_id": "deepseek",
                "model_id": "deepseek-v4-flash",
                "transport": "http_openai_compat",
                "capabilities": [{"capability_key": "no_such_capability", "priority": 0}],
            },
            headers=_superadmin_headers(),
        )
        assert resp.status_code == 400
        assert "no_such_capability" in resp.json()["detail"]
        # No debe haber quedado la fila motor a medias (validacion antes del INSERT)
        assert client.portal.call(_fetch_motor_row, key) is None
    finally:
        client.portal.call(_cleanup_motor, key)
