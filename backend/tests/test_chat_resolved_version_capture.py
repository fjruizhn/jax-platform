"""Bloque D (D1.2) — captura de resolved_version desde la Mesa web real.
_call_openai_compat/_call_gemini exponen un on_response opcional (no rompe
las llamadas existentes sin ese kwarg, ver test_chat_http_pooling.py) que
_invoke_facet usa para alimentar model_catalog.record_resolved_version.
Best-effort: nunca debe romper la respuesta al usuario si falla.
"""
import http_client
import model_catalog
from api.chat import _call_openai_compat, _call_gemini, _invoke_facet


class _FakeResponse:
    def __init__(self, json_data):
        self._json_data = json_data

    def json(self):
        return self._json_data

    def raise_for_status(self):
        pass


class _FakeClient:
    def __init__(self, response, authorize_response=None):
        # authorize_response is opt-in and defaults to None so the
        # direct _call_openai_compat/_call_gemini tests (single outbound
        # call, no facet-governance gate involved) keep returning
        # `response` to every .post() unchanged. Pass it explicitly for
        # calls that go through _invoke_facet on a governed facet
        # (hipatia/jekyll/thot/ada), which now calls
        # /motor/authorize-facet before the real provider call.
        self._response = response
        self._authorize_response = authorize_response

    async def post(self, url, **kwargs):
        if self._authorize_response is not None and "/motor/authorize-facet" in url:
            return self._authorize_response
        return self._response


async def test_call_openai_compat_invokes_on_response_with_raw_json():
    fake = _FakeClient(_FakeResponse({
        "model": "deepseek-v4-flash",
        "choices": [{"message": {"content": "hola"}}],
    }))
    original = http_client._client
    http_client._client = fake
    captured = {}

    async def capture(data):
        captured["data"] = data

    try:
        content, _tin, _tout = await _call_openai_compat(
            "https://api.deepseek.com/v1", "sk-x", "deepseek-chat",
            "system", [], "hola", "max_tokens", 131072, on_response=capture,
        )
    finally:
        http_client._client = original

    assert content == "hola"
    assert captured["data"]["model"] == "deepseek-v4-flash"


async def test_call_openai_compat_without_on_response_still_works():
    """No debe romper las llamadas existentes que no pasan on_response."""
    fake = _FakeClient(_FakeResponse({"choices": [{"message": {"content": "ok"}}]}))
    original = http_client._client
    http_client._client = fake
    try:
        content, _tin, _tout = await _call_openai_compat("https://x", "k", "m", "s", [], "hola", "max_tokens", 131072)
    finally:
        http_client._client = original
    assert content == "ok"


async def test_call_gemini_invokes_on_response_with_raw_json():
    fake = _FakeClient(_FakeResponse({
        "modelVersion": "gemini-2.5-flash-002",
        "candidates": [{"content": {"parts": [{"text": "hola"}]}}],
    }))
    original = http_client._client
    http_client._client = fake
    captured = {}

    async def capture(data):
        captured["data"] = data

    try:
        content, _tin, _tout = await _call_gemini("k", "gemini-2.5-flash", "system", [], "hola", on_response=capture)
    finally:
        http_client._client = original

    assert content == "hola"
    assert captured["data"]["modelVersion"] == "gemini-2.5-flash-002"


def test_invoke_facet_records_resolved_version_for_openai_compat_facet(client, monkeypatch):
    """Integracion real via Jekyll (transport http_openai_compat, ver Bloque
    C facet seed). record_resolved_version se llama con el facet real y el
    'model' devuelto por el proveedor, best-effort (nunca rompe la respuesta
    aunque record_resolved_version explote)."""
    calls = []

    async def fake_record(facet_key, resolved_version):
        calls.append((facet_key, resolved_version))
        return {"drift": False, "proposal_id": None}

    monkeypatch.setattr(model_catalog, "record_resolved_version", fake_record)

    fake = _FakeClient(
        _FakeResponse({
            "model": "deepseek-v4-flash",
            "choices": [{"message": {"content": "hola desde jekyll"}}],
        }),
        authorize_response=_FakeResponse({"allowed": True, "reason": "OK"}),
    )
    original = http_client._client
    http_client._client = fake

    async def run():
        config = {"personalities": {
            "jekyll": {"system_prompt": "Sos Jekyll."},
            "jax_local": {"system_prompt": "Sos JAX."},
        }}
        return await _invoke_facet("jekyll", config, "test-user-resolved-version", "hola")

    try:
        result, _usage = client.portal.call(run)
    finally:
        http_client._client = original

    assert result == "hola desde jekyll"
    assert calls == [("jekyll", "deepseek-v4-flash")]


def test_invoke_facet_survives_record_resolved_version_failure(client, monkeypatch):
    """Si record_resolved_version explota, la respuesta al usuario NO se
    rompe (best-effort real, no solo declarado)."""
    async def boom(facet_key, resolved_version):
        raise RuntimeError("DB caida")

    monkeypatch.setattr(model_catalog, "record_resolved_version", boom)

    fake = _FakeClient(
        _FakeResponse({
            "model": "deepseek-v4-flash",
            "choices": [{"message": {"content": "sigo viva"}}],
        }),
        authorize_response=_FakeResponse({"allowed": True, "reason": "OK"}),
    )
    original = http_client._client
    http_client._client = fake

    async def run():
        config = {"personalities": {
            "jekyll": {"system_prompt": "Sos Jekyll."},
            "jax_local": {"system_prompt": "Sos JAX."},
        }}
        return await _invoke_facet("jekyll", config, "test-user-resolved-version-2", "hola")

    try:
        result, _usage = client.portal.call(run)
    finally:
        http_client._client = original

    assert result == "sigo viva"
