"""Cada transporte de chat.py ya recibe la respuesta completa de la API —
el fix es dejar de descartar los campos de tokens que ya estan ahi, no
agregar requests nuevos. Shapes reales verificadas con evidencia el
2026-08-10 (curl real contra las 3 APIs)."""
import http_client
from api.chat import _call_openai_compat, _call_gemini, _call_ollama


class _FakeResponse:
    def __init__(self, json_data):
        self._json_data = json_data

    def json(self):
        return self._json_data

    def raise_for_status(self):
        pass


class _FakePostClient:
    def __init__(self, response):
        self._response = response

    async def post(self, url, **kwargs):
        return self._response


def test_call_openai_compat_devuelve_tokens_reales(client):
    fake = _FakePostClient(_FakeResponse({
        "choices": [{"message": {"content": "hola"}}],
        "usage": {"prompt_tokens": 42, "completion_tokens": 17},
    }))
    original = http_client._client
    http_client._client = fake
    try:
        text, tokens_in, tokens_out = client.portal.call(
            _call_openai_compat, "https://api.example.com/v1", "sk-fake", "modelo-x",
            "system", [], "hola", "max_tokens", None,
        )
    finally:
        http_client._client = original
    assert text == "hola"
    assert tokens_in == 42
    assert tokens_out == 17


def test_call_gemini_devuelve_tokens_reales(client):
    fake = _FakePostClient(_FakeResponse({
        "candidates": [{"content": {"parts": [{"text": "hola"}]}}],
        "usageMetadata": {"promptTokenCount": 20, "candidatesTokenCount": 8},
    }))
    original = http_client._client
    http_client._client = fake
    try:
        text, tokens_in, tokens_out = client.portal.call(
            _call_gemini, "fake-key", "gemini-2.5-flash", "system", [], "hola", None,
        )
    finally:
        http_client._client = original
    assert text == "hola"
    assert tokens_in == 20
    assert tokens_out == 8


def test_call_ollama_devuelve_tokens_reales(client):
    fake = _FakePostClient(_FakeResponse({
        "message": {"content": "hola"},
        "prompt_eval_count": 31,
        "eval_count": 36,
    }))
    original = http_client._client
    http_client._client = fake
    try:
        text, tokens_in, tokens_out = client.portal.call(
            _call_ollama, "system", [], "hola",
            {"personalities": {"jax_local": {"api_url": "http://localhost:11434/api/chat"}}},
            "qwen3-coder:30b",
        )
    finally:
        http_client._client = original
    assert text == "hola"
    assert tokens_in == 31
    assert tokens_out == 36
