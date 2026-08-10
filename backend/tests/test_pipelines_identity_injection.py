"""api/pipelines.py reenviaba el body del cliente sin inyectar identidad
real -- create_pipeline dependia de lo que mandara el front (hoy "Fernando"
fijo), resume_pipeline lo hardcodeaba en Python directamente. Ninguno de
los dos debe confiar en identidad que venga del cliente para algo que se
usa para atribuir costo."""
from auth.jwt import create_access_token

USER_ID = "1"
TENANT_ID = "test-pipelines-identity-tenant"


def _auth_headers():
    token = create_access_token(USER_ID, TENANT_ID, "user")
    return {"Authorization": f"Bearer {token}"}


def test_create_pipeline_inyecta_identidad_real(client, monkeypatch):
    captured = {}

    class _FakeClient:
        async def post(self, url, json=None, timeout=None):
            captured["url"] = url
            captured["json"] = json
            class _R:
                status_code = 200
                def json(self):
                    return {"pipeline_id": None}
            return _R()
        async def get(self, url, timeout=None):
            class _R:
                def json(self):
                    return {}
            return _R()

    async def _fake_get_http_client():
        return _FakeClient()

    import api.pipelines as pipelines_module
    monkeypatch.setattr(pipelines_module, "get_http_client", _fake_get_http_client)

    client.post(
        "/api/pipelines",
        json={
            "name": "test",
            "objective": "x",
            "invoked_by": "cliente-mintiendo",
            "mode": "supervised",
            # Client-supplied identity must be overridden, not merely
            # filled in when absent -- this is the security-relevant case.
            "user_id": "spoofed-user",
            "tenant_id": "spoofed-tenant",
        },
        headers=_auth_headers(),
    )

    assert captured["json"]["user_id"] == USER_ID
    assert captured["json"]["tenant_id"] == TENANT_ID


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

    import api.pipelines as pipelines_module
    monkeypatch.setattr(pipelines_module, "get_http_client", _fake_get_http_client)
    monkeypatch.setattr(pipelines_module, "_require_pipeline_owner", lambda pid, user: None)

    client.post(
        "/api/pipelines/00000000-0000-0000-0000-000000000000/resume",
        headers=_auth_headers(),
    )

    assert captured["json"]["user_id"] == USER_ID
    assert captured["json"]["tenant_id"] == TENANT_ID
    # "invoked_by": "Fernando" is kept as the human-readable label -- it's
    # not what cost attribution reads. What must never be "Fernando" is the
    # real identity fields themselves.
    assert captured["json"]["user_id"] != "Fernando"
    assert captured["json"]["tenant_id"] != "Fernando"
