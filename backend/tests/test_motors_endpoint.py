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


def test_capabilities_expone_has_tool_access_por_motor(client):
    """T1 (diagnostico pipeline 19ad2c42-cdf): has_tool_access vivia solo
    como un `if motor == "jax_local"` literal en worker.py:488 -- nada podia
    preguntarle al sistema que motores ejecutan tools. Ahora es columna en
    `motor` (single source of truth), y este endpoint la expone para que el
    picker del frontend (PipelineModal.jsx) pueda consultarla en vez de
    usar el mapa hardcodeado GOVERNED_FACET_CAPABILITY que causo el
    incidente.

    Task 5 (2026-09-18, historial-y-arreglos-de-pipeline): `jax_local` dejo
    de ser el unico. `jacobs` ya tenia 'jacobs' en `capability.allowed_
    callers` de file_read/file_write desde GAP2 Fase2 (2026-08-19) -- el
    hueco real era que `ada`/`kimi`/`thot` nunca recibian el catalogo de
    tools (`has_tool_access=0`), asi que el permiso del caller nunca llegaba
    a importar para ellos. Medido con una llamada real por faceta contra su
    proveedor real: `ada` (glm-5.3, zhipu) y `kimi` (kimi-k3, moonshot)
    devuelven HTTP 200 y llaman a `read_file`; `thot` (gpt-6-astra, openai)
    devuelve HTTP 400 ("Function tools with reasoning_effort are not
    supported for gpt-6-astra in /v1/chat/completions") -- prendersela no
    habilitaria nada, solo agregaria un 400 al dispatch. `thot` ademas es la
    faceta arbitro (juzga lo que otros steps produjeron, no necesita leer el
    workspace). `_seed_ada_kimi_has_tool_access` (migrations.py) sube
    `ada`/`kimi` a TRUE y deja `thot` explicitamente afuera, con esa
    evidencia citada en el docstring."""
    resp = client.get("/api/motors/capabilities", headers=_auth_headers())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "motors" in body, "el endpoint debe exponer una lista 'motors' con has_tool_access"
    by_key = {m["key"]: m for m in body["motors"]}
    assert "jax_local" in by_key, by_key
    assert by_key["jax_local"]["has_tool_access"] is True, by_key["jax_local"]
    assert "ada" in by_key, by_key
    assert by_key["ada"]["has_tool_access"] is True, by_key["ada"]
    assert "kimi" in by_key, by_key
    assert by_key["kimi"]["has_tool_access"] is True, by_key["kimi"]
    assert "thot" in by_key, by_key
    assert by_key["thot"]["has_tool_access"] is False, by_key["thot"]
