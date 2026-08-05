import api.admin.keys as keys_module
import http_client
from auth.jwt import create_access_token

USER_ID = "test-keys-pooling-user"
TENANT_ID = "test-keys-pooling-tenant"


def _superadmin_headers():
    token = create_access_token(USER_ID, TENANT_ID, "superadmin")
    return {"Authorization": f"Bearer {token}"}


class _FakeResponse:
    def __init__(self, status_code=200, text=""):
        self.status_code = status_code
        self.text = text


class _FakeClient:
    def __init__(self, response):
        self._response = response
        self.calls = []

    async def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self._response


def test_test_key_generic_branch_uses_the_shared_client(client, monkeypatch):
    """Exercises the generic (non-Gemini) branch of POST /keys/{id}/test,
    which reads test_url from PROVIDERS and hits it with a Bearer header."""
    async def fake_get_db_key(pool, user_id, provider_id):
        return "sk-fake-key"

    monkeypatch.setattr(keys_module, "_get_db_key", fake_get_db_key)

    fake = _FakeClient(_FakeResponse(status_code=200))
    original = http_client._client
    http_client._client = fake
    try:
        resp = client.post("/api/admin/keys/openai/test", headers=_superadmin_headers())
    finally:
        http_client._client = original

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert len(fake.calls) == 1
    url, kwargs = fake.calls[0]
    assert url == "https://api.openai.com/v1/models"
    assert kwargs["headers"] == {"Authorization": "Bearer sk-fake-key"}
    assert kwargs["timeout"] == 10.0


def test_test_key_gemini_branch_uses_the_shared_client(client, monkeypatch):
    async def fake_get_db_key(pool, user_id, provider_id):
        return "gk-fake-key"

    monkeypatch.setattr(keys_module, "_get_db_key", fake_get_db_key)

    fake = _FakeClient(_FakeResponse(status_code=200))
    original = http_client._client
    http_client._client = fake
    try:
        resp = client.post("/api/admin/keys/gemini/test", headers=_superadmin_headers())
    finally:
        http_client._client = original

    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert len(fake.calls) == 1
    url, kwargs = fake.calls[0]
    assert url == "https://generativelanguage.googleapis.com/v1beta/models?key=gk-fake-key"
    assert kwargs["timeout"] == 10.0
