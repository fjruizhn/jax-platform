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
