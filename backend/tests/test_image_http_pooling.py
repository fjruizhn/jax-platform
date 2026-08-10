import os

import http_client
from auth.jwt import create_access_token

# record_usage (api/admin/usage.py) hace int(user_id)/int(tenant_id) antes del
# INSERT — IDs no numericos hacian que ese cast reventara dentro de un bare
# `except Exception: pass`, tragandose el error y dejando la tabla sin fila
# nueva. tenant_id=88 es exclusivo de este archivo (test_usage_pricing.py y
# los demas tests de axioma_usage usan tenant_id="1") para poder filtrar por
# el sin colisionar con filas de otros tests.
USER_ID = "1"
TENANT_ID = "88"


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


async def _fetch_max_usage_id():
    """Ultimo id de axioma_usage ANTES de que este test dispare su request —
    baseline para probar que la fila que se lee despues es la que este test
    escribio, no un residuo de otro archivo de test."""
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT COALESCE(MAX(id), 0) FROM axioma_usage")
            (max_id,) = await cur.fetchone()
            return max_id


async def _fetch_usage_row_after(min_id):
    """Fetch filtrado por tenant_id=88 (exclusivo de este archivo) Y id >
    min_id (capturado antes del request). Un `ORDER BY id DESC LIMIT 1` sin
    filtro pasa en falso contra la fila mas nueva de OTRO archivo de test
    (ej. test_usage_pricing.py, que usa los mismos facet/model/cost) cuando
    el INSERT de este test falla en silencio (record_usage con IDs no
    numericos, ver el fix de USER_ID/TENANT_ID arriba)."""
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT id, facet, model, cost_usd, request_type FROM axioma_usage "
                "WHERE tenant_id = %s AND id > %s ORDER BY id DESC LIMIT 1",
                (TENANT_ID, min_id),
            )
            return await cur.fetchone()


def test_generate_image_registra_uso_con_costo_plano(client, monkeypatch):
    """image.py nunca llamaba record_usage — costo de imagenes sin trackear.
    gpt-image-1 es costo plano por imagen, no por token: cost_usd_override."""
    import api.image as image_module

    async def fake_credential(provider_id):
        return "sk-fake"

    # Patchear en api.image (donde el nombre quedo bindeado por el `from
    # credential_resolver import resolve_credential_instrumented` de ese
    # modulo), no en credential_resolver (el modulo fuente) — patchear la
    # fuente no afecta la referencia ya bindeada en api.image.
    monkeypatch.setattr(image_module, "resolve_credential_instrumented", fake_credential)

    fake = _FakeClient(_FakeResponse({
        "data": [{"b64_json": "ZmFrZQ==", "revised_prompt": "un gato"}]
    }))
    original = http_client._client
    http_client._client = fake
    max_id_before = client.portal.call(_fetch_max_usage_id)
    try:
        resp = client.post(
            "/api/image/generate",
            headers=_headers(),
            json={"prompt": "un gato"},
        )
    finally:
        http_client._client = original

    assert resp.status_code == 200
    row = client.portal.call(_fetch_usage_row_after, max_id_before)
    assert row is not None, "record_usage no escribio ninguna fila nueva para tenant_id=88"
    row_id, facet, model, cost_usd, request_type = row
    assert row_id > max_id_before
    assert facet == "thot_image"
    assert model == "gpt-image-1"
    assert request_type == "imagen"
    assert abs(float(cost_usd) - 0.04) < 1e-9
