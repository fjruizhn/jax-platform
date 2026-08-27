import http_client
from api.chat import _call_ollama, _call_openai_compat, _call_gemini


class _FakeResponse:
    def __init__(self, json_data):
        self._json_data = json_data

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


async def test_call_ollama_uses_the_shared_client():
    fake = _FakeClient(_FakeResponse({"message": {"content": "hola"}}))
    original = http_client._client
    http_client._client = fake
    try:
        result, _tin, _tout = await _call_ollama(
            "system prompt", [], "hola",
            {"personalities": {"jax_local": {"api_url": "http://127.0.0.1:11434/api/chat"}}},
            "qwen3-coder:30b",
        )
    finally:
        http_client._client = original

    assert result == "hola"
    assert len(fake.calls) == 1
    url, kwargs = fake.calls[0]
    assert url == "http://127.0.0.1:11434/api/chat"
    assert kwargs["timeout"] == 180.0
    assert kwargs["json"]["keep_alive"] == -1


async def test_call_openai_compat_uses_the_shared_client():
    fake = _FakeClient(_FakeResponse({"choices": [{"message": {"content": "respuesta"}}]}))
    original = http_client._client
    http_client._client = fake
    try:
        result, _tin, _tout = await _call_openai_compat(
            "https://api.deepseek.com/v1", "sk-test", "deepseek-v4-flash",
            "system prompt", [], "hola", "max_tokens",
        )
    finally:
        http_client._client = original

    assert result == "respuesta"
    assert len(fake.calls) == 1
    url, kwargs = fake.calls[0]
    assert url == "https://api.deepseek.com/v1/chat/completions"
    assert kwargs["timeout"] == 120.0


async def test_call_gemini_uses_the_shared_client():
    fake = _FakeClient(_FakeResponse({
        "candidates": [{"content": {"parts": [{"text": "respuesta gemini"}]}}]
    }))
    original = http_client._client
    http_client._client = fake
    try:
        result, _tin, _tout = await _call_gemini("test-key", "gemini-2.5-flash", "system prompt", [], "hola")
    finally:
        http_client._client = original

    assert result == "respuesta gemini"
    assert len(fake.calls) == 1
    url, kwargs = fake.calls[0]
    assert url.startswith("https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent")
    assert kwargs["timeout"] == 120.0
