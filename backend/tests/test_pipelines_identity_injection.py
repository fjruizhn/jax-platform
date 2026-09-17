"""api/pipelines.py reenviaba el body del cliente sin inyectar identidad
real -- create_pipeline dependia de lo que mandara el front (hasta
2026-09-14, "Fernando" fijo; ahora el rol "plataforma", puesto por el
backend), resume_pipeline lo hardcodeaba en Python directamente. Ninguno de
los dos debe confiar en identidad que venga del cliente para algo que se
usa para atribuir costo."""
from auth.jwt import create_access_token

USER_ID = "1"
TENANT_ID = "test-pipelines-identity-tenant"


def _auth_headers():
    token = create_access_token(USER_ID, TENANT_ID, "user")
    return {"Authorization": f"Bearer {token}"}


def test_create_pipeline_inyecta_identidad_real(client, monkeypatch):
    from tests.jacobs_falso import JacobsFalso, respuesta, veredicto

    falso = JacobsFalso({
        ("POST", "/preflight"): respuesta(200, veredicto(costo="0.00")),
        ("POST", "/pipeline"): respuesta(200, {"pipeline_id": None}),
    })

    async def _fake_get_http_client():
        return falso

    import api.pipelines as pipelines_module
    monkeypatch.setattr(pipelines_module, "get_http_client", _fake_get_http_client)
    monkeypatch.setattr(pipelines_module, "JACOBS_URL", "http://jacobs.test/jacobs")

    client.post(
        "/api/pipelines",
        json={
            "name": "test",
            "objective": "x",
            "steps": [{"facet": "thot", "capability": "critique", "prompt": "x"}],
            "invoked_by": "cliente-mintiendo",
            "mode": "supervised",
            # Client-supplied identity must be overridden, not merely
            # filled in when absent -- this is the security-relevant case.
            "user_id": "spoofed-user",
            "tenant_id": "spoofed-tenant",
        },
        headers=_auth_headers(),
    )

    (enviado,) = falso.cuerpos("POST", "/pipeline")
    assert enviado["user_id"] == USER_ID
    assert enviado["tenant_id"] == TENANT_ID
    # tanda A (2026-09-14): invoked_by es un ROL que pone el backend, igual que
    # la identidad; lo que mande el cliente se pisa.
    assert enviado["invoked_by"] == "plataforma"
    (prevuelo,) = falso.cuerpos("POST", "/preflight")
    assert (prevuelo["user_id"], prevuelo["tenant_id"], prevuelo["invoked_by"]) == (USER_ID, TENANT_ID, "plataforma")


def test_resume_pipeline_inyecta_identidad_real(client, monkeypatch):
    captured = {}

    class _FakeClient:
        async def post(self, url, json=None, timeout=None):
            captured["json"] = json
            class _R:
                def json(self):
                    return {"ok": True}
            return _R()

    async def _fake_get_http_client():
        return _FakeClient()

    async def _fake_require_pipeline_owner(pid, user):
        return None

    import api.pipelines as pipelines_module
    monkeypatch.setattr(pipelines_module, "get_http_client", _fake_get_http_client)
    monkeypatch.setattr(pipelines_module, "_require_pipeline_owner", _fake_require_pipeline_owner)

    client.post(
        "/api/pipelines/00000000-0000-0000-0000-000000000000/resume",
        headers=_auth_headers(),
    )

    assert captured["json"]["user_id"] == USER_ID
    assert captured["json"]["tenant_id"] == TENANT_ID
    # tanda A (2026-09-14): el backend declara el rol "plataforma" (Jacobs
    # exige ese rol para reanudar); la identidad real va en user_id/tenant_id.
    assert captured["json"]["invoked_by"] == "plataforma"
    assert captured["json"]["user_id"] != "Fernando"
    assert captured["json"]["tenant_id"] != "Fernando"
