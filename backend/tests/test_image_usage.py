"""image.py nunca llamaba record_usage — costo de imagenes sin trackear.
gpt-image-1 es costo plano por imagen, no por token: cost_usd_override."""
import http_client
from auth.jwt import create_access_token


class _FakeImageResponse:
    def json(self):
        return {"data": [{"b64_json": "ZmFrZQ==", "revised_prompt": "un gato"}]}

    def raise_for_status(self):
        pass


class _FakeImagePostClient:
    async def post(self, url, **kwargs):
        return _FakeImageResponse()


async def _fetch_last_usage_row():
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
    import credential_resolver

    async def fake_credential(provider_id):
        return "sk-fake"

    monkeypatch.setattr(credential_resolver, "resolve_credential_instrumented", fake_credential)
    original = http_client._client
    http_client._client = _FakeImagePostClient()
    try:
        token = create_access_token("1", "test-tenant", "user")
        resp = client.post("/api/image/generate", json={"prompt": "un gato"}, headers={"Authorization": f"Bearer {token}"})
    finally:
        http_client._client = original

    assert resp.status_code == 200
    row = client.portal.call(_fetch_last_usage_row)
    facet, model, cost_usd, request_type = row
    assert model == "gpt-image-1"
    assert request_type == "imagen"
    assert abs(float(cost_usd) - 0.04) < 1e-9
