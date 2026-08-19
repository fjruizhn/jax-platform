"""R4 Task 9 (ultima tarea del plan) — GET/POST /api/admin/motors.

Cablea el mismo mecanismo ya probado por INSERT directo en Task 4/8 (motor
kimi/ada/jax_local/thot sembrados via db/migrations.py): una fila en
`motor` + N filas en `capability_motor`, cero codigo de despacho nuevo.
Mismo patron de auth/estructura que test_admin_models_endpoints.py y
test_admin_facet_bindings_endpoints.py (superadmin JWT, alcance minimo
crear+listar, sin editar/borrar).
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
