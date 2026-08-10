import os

import http_client
from auth.jwt import create_access_token

USER_ID = "test-image-pooling-user"
TENANT_ID = "test-image-pooling-tenant"


def _headers():
    token = create_access_token(USER_ID, TENANT_ID, "operator")
    return {"Authorization": f"Bearer {token}"}


class _FakeResponse:
    def __init__(self, json_data, status_code=200):
        self._json_data = json_data
        self.status_code = status_code

    def json(self):
        return self._json_data

    def raise_for_status(self):
        pass


class _FakeClient:
    def __init__(self, response):
        self._response = response
        self.calls = []

    async def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self._response


def test_generate_image_uses_the_shared_client(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-123")
    fake = _FakeClient(_FakeResponse({
        "data": [{"url": "https://example.com/img.png", "revised_prompt": "a cat"}]
    }))
    original = http_client._client
    http_client._client = fake
    try:
        resp = client.post(
            "/api/image/generate",
            headers=_headers(),
            json={"prompt": "a cat"},
        )
    finally:
        http_client._client = original

    assert resp.status_code == 200
    assert resp.json() == {"url": "https://example.com/img.png", "revised_prompt": "a cat"}
    assert len(fake.calls) == 1
    url, kwargs = fake.calls[0]
    assert url == "https://api.openai.com/v1/images/generations"
    assert kwargs["timeout"] == 120.0


async def _fetch_last_usage_row():
    """Fetch the most recent axioma_usage row for verification."""
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT facet, model, cost_usd, request_type FROM axioma_usage "
                "ORDER BY id DESC LIMIT 1"
            )
            return await cur.fetchone()


def test_generate_image_registra_uso_con_costo_plano(client, monkeypatch):
    """image.py nunca llamaba record_usage — costo de imagenes sin trackear.
    gpt-image-1 es costo plano por imagen, no por token: cost_usd_override."""
    import credential_resolver

    async def fake_credential(provider_id):
        return "sk-fake"

    monkeypatch.setattr(credential_resolver, "resolve_credential_instrumented", fake_credential)

    fake = _FakeClient(_FakeResponse({
        "data": [{"b64_json": "ZmFrZQ==", "revised_prompt": "un gato"}]
    }))
    original = http_client._client
    http_client._client = fake
    try:
        resp = client.post(
            "/api/image/generate",
            headers=_headers(),
            json={"prompt": "un gato"},
        )
    finally:
        http_client._client = original

    assert resp.status_code == 200
    row = client.portal.call(_fetch_last_usage_row)
    facet, model, cost_usd, request_type = row
    assert model == "gpt-image-1"
    assert request_type == "imagen"
    assert abs(float(cost_usd) - 0.04) < 1e-9
