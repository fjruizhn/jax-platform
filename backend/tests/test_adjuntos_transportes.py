"""Cada transporte manda la imagen en SU formato (frente D, 2026-09-16)."""
import asyncio

import pytest

import api.chat as chat_mod
import http_client
from adjuntos.contrato import ImagenNoSoportadaError, ImagenValidada
from facet_resolver import ResolvedFacet

IMG = (ImagenValidada("f.png", "image/png", "QUJD", 3),)


class _Grabador:
    def __init__(self, respuesta):
        self.respuesta = respuesta
        self.cuerpos: list[dict] = []

    async def post(self, url, **kwargs):
        self.cuerpos.append(kwargs["json"])
        datos = self.respuesta

        class _R:
            def raise_for_status(self):
                pass

            def json(self):
                return datos
        return _R()


def _con(grabador, corrutina):
    original = http_client._client
    http_client._client = grabador
    try:
        return asyncio.run(corrutina)
    finally:
        http_client._client = original


def test_openai_compat_manda_image_url_con_data_uri():
    g = _Grabador({"choices": [{"message": {"content": "ok"}}], "usage": {}})
    _con(g, chat_mod._call_openai_compat("https://x.invalid/v1", "k", "m", "sys", [], "mirá",
                                         "max_tokens", 100, imagenes=IMG))
    assert g.cuerpos[0]["messages"][-1] == {"role": "user", "content": [
        {"type": "text", "text": "mirá"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}},
    ]}


def test_gemini_manda_inline_data():
    g = _Grabador({"candidates": [{"content": {"parts": [{"text": "ok"}]}}]})
    _con(g, chat_mod._call_gemini("k", "gemini-x", "sys", [], "mirá", imagenes=IMG))
    assert g.cuerpos[0]["contents"][-1] == {"role": "user", "parts": [
        {"text": "mirá"}, {"inline_data": {"mime_type": "image/png", "data": "QUJD"}},
    ]}


def test_ollama_manda_images_sin_prefijo():
    g = _Grabador({"message": {"content": "ok"}})
    config = {"personalities": {"jax_local": {"api_url": "http://x.invalid/api/chat"}}}
    _con(g, chat_mod._call_ollama("sys", [], "mirá", config, "m", imagenes=IMG))
    assert g.cuerpos[0]["messages"][-1] == {"role": "user", "content": "mirá", "images": ["QUJD"]}


def test_sin_imagenes_el_cuerpo_no_cambia():
    g = _Grabador({"choices": [{"message": {"content": "ok"}}], "usage": {}})
    _con(g, chat_mod._call_openai_compat("https://x.invalid/v1", "k", "m", "sys", [], "hola", "max_tokens", 100))
    assert g.cuerpos[0]["messages"][-1] == {"role": "user", "content": "hola"}


def test_el_dispatch_se_niega_si_el_binding_ya_no_acepta_imagen(monkeypatch):
    async def resolver(_key):
        return ResolvedFacet(key="jax_local", provider_id="ollama", base_url=None, model="m", credential="",
                             transport="ollama", persona=None, params=None, max_tokens_param=None,
                             max_output_tokens=None, input_modalities=frozenset({"text"}))

    llamadas = []

    async def proveedor(*a, **k):
        llamadas.append(k)
        return "no", 1, 1

    monkeypatch.setattr(chat_mod, "resolve_facet", resolver)
    monkeypatch.setattr(chat_mod, "_call_ollama", proveedor)
    config = {"personalities": {"jax_local": {"system_prompt": "x", "api_url": "http://x.invalid"}}}
    with pytest.raises(ImagenNoSoportadaError):
        asyncio.run(chat_mod._invoke_facet_dispatch("jax_local", config, "u", "mirá", imagenes=IMG))
    assert llamadas == []
