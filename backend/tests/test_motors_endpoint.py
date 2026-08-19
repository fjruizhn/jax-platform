"""GET /api/motors/capabilities -- capability + allowed_motors en orden de
priority, para que el frontend arme el picker de Pipeline (R4)."""
from auth.jwt import create_access_token

USER_ID = "1"
TENANT_ID = "test-motors-endpoint-tenant"


def _auth_headers():
    token = create_access_token(USER_ID, TENANT_ID, "user")
    return {"Authorization": f"Bearer {token}"}


def test_capabilities_incluye_generate_con_kimi_y_ada_en_orden(client):
    resp = client.get("/api/motors/capabilities", headers=_auth_headers())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    by_key = {c["key"]: c for c in body["capabilities"]}
    assert "generate" in by_key
    # kimi y ada quedan sembrados por _seed_motors_and_capabilities (priority
    # 0 y 1); jax_local se agrega despues en _seed_jax_local_motor (Task 4,
    # priority 2) como motor real adicional para "generate" -- confirmado
    # contra jax_memory real, no solo jax_memory_test.
    assert by_key["generate"]["allowed_motors"] == ["kimi", "ada", "jax_local"]


def test_capabilities_critique_incluye_thot_y_ada_en_orden(client):
    """R4 Task 8: thot se da de alta como motor real via INSERT
    (_seed_thot_motor) y completa validate_consistency/critique, que
    Task 1 habia dejado sin su referencia a "thot" porque el motor
    todavia no existia -- confirmado contra jax_memory real, no solo
    jax_memory_test."""
    resp = client.get("/api/motors/capabilities", headers=_auth_headers())
    body = resp.json()
    by_key = {c["key"]: c for c in body["capabilities"]}
    assert by_key["critique"]["allowed_motors"] == ["thot", "ada"]
