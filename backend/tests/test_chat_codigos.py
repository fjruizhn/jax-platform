"""Frente A (2026-09-16), api/chat.py.
A-53: las respuestas enlatadas eran texto en español; ahora `aviso` con codigo
y params (i18n en el frontend). `response` lleva una marca sin idioma para el
historial y la memoria. A-51: los 400/502 llevan detail.code. A-13: el evento
facet_response_completed ya no repite la respuesta entera. A-16: una sola
llamada a _call_ollama. A-22: los sets llevan el nombre de jax/core/router.py
para entrar a check_mirror_sync. Puros salvo el ultimo."""
import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

import http_client
from api import chat as chat_mod
from facet_resolver import FacetUnavailableError
from tests.identidades import cabeceras
from tests.test_chat_contract_wrapper import _FakePostClient, _FakeResponse

BACKEND = Path(__file__).resolve().parent.parent
CONFIG = {"personalities": {"jax_local": {"system_prompt": "x"}, "thot": {"system_prompt": "x"},
                            "otra": {"system_prompt": "x"}}}


def _despachar(monkeypatch, faceta, resuelta, mensaje="hola"):
    async def resolver(_clave):
        if resuelta is None:
            raise FacetUnavailableError(faceta)
        return resuelta
    monkeypatch.setattr(chat_mod, "resolve_facet", resolver)
    return asyncio.run(chat_mod._invoke_facet_dispatch(faceta, CONFIG, "u-codigos", mensaje))


def test_sin_binding_es_un_aviso_con_codigo(monkeypatch):
    texto, usage, _ = _despachar(monkeypatch, "thot", None)
    assert usage is None
    assert texto == chat_mod.AvisoDeChat(code="faceta_sin_binding", params={"facet": "thot"})


def test_transporte_no_soportado_es_un_aviso(monkeypatch):
    f = SimpleNamespace(transport="motor_registry", model="m", provider_id="p")
    texto, _, _ = _despachar(monkeypatch, "thot", f)
    assert texto == chat_mod.AvisoDeChat(code="transporte_no_soportado",
                                        params={"facet": "thot", "transport": "motor_registry"})


def test_gate_denegado_es_un_aviso(monkeypatch):
    f = SimpleNamespace(transport="http_gemini", model="m", provider_id="gemini")
    original = http_client._client
    http_client._client = _FakePostClient(_FakeResponse({"allowed": False, "reason": "no"}))
    try:
        texto, _, _ = _despachar(monkeypatch, "thot", f)
    finally:
        http_client._client = original
    assert texto == chat_mod.AvisoDeChat(code="faceta_no_autorizada", params={"facet": "thot"})


def test_identidad_del_modelo_es_un_aviso_con_el_dato_real(monkeypatch):
    f = SimpleNamespace(transport="ollama", model="modelo-centinela", provider_id="ollama")
    texto, _, _ = _despachar(monkeypatch, "jax_local", f, "que modelo sos")
    assert texto == chat_mod.AvisoDeChat(
        code="identidad_del_modelo",
        params={"facet": "jax_local", "model": "modelo-centinela", "provider": "ollama"})


def test_la_marca_de_un_aviso_no_tiene_idioma():
    aviso = chat_mod.AvisoDeChat(code="faceta_sin_binding", params={"facet": "thot"})
    assert aviso.como_texto() == "[faceta_sin_binding facet=thot]"
    assert chat_mod.AvisoDeChat(code="hyde_usa_modo_comando").como_texto() == "[hyde_usa_modo_comando]"


def test_no_quedan_textos_enlatados_en_español():
    fuente = (BACKEND / "api/chat.py").read_text(encoding="utf-8")
    for resto in ("no está disponible", "Hyde opera", "Corro con", "_MODEL_IDENTITY_HOSTING",
                  "Error en ", "Error HTTP ", "faceta desconocida"):
        assert resto not in fuente, resto


def test_el_502_http_es_un_codigo_con_motivo_redactado():
    req = httpx.Request("POST", "https://x.test/v1")
    exc = httpx.HTTPStatusError("x", request=req, response=httpx.Response(400, text="malo", request=req))
    assert chat_mod._detalle_502_http("hipatia", exc) == {
        "code": "proveedor_error_http", "facet": "hipatia", "status": 400, "motivo": "malo"}


def test_el_502_generico_es_un_codigo():
    assert chat_mod._detalle_502_generico("thot", RuntimeError("se cayó")) == {
        "code": "faceta_error", "facet": "thot", "motivo": "se cayó"}


def test_facet_response_completed_solo_lleva_la_faceta(monkeypatch):
    publicados = []

    async def capturar(evento):
        publicados.append(evento)

    monkeypatch.setattr(chat_mod.event_bus, "publish", capturar)
    asyncio.run(chat_mod._fire_completed("thot", "1", "5"))
    (evento,) = publicados
    assert (evento.event_type, evento.payload) == ("facet_response_completed", {"facet": "thot"})


async def _capturar_prompts(monkeypatch, faceta):
    prompts = []

    async def ollama(system_prompt, history, message, config, model, *, imagenes=()):
        assert imagenes == ()   # un turno sin adjuntos no manda imágenes
        prompts.append(system_prompt)
        return "ok", 1, 1

    async def resolver(_clave):
        return SimpleNamespace(transport="ollama", model="qwen-x", provider_id="ollama")

    monkeypatch.setattr(chat_mod, "_call_ollama", ollama)
    monkeypatch.setattr(chat_mod, "resolve_facet", resolver)
    await chat_mod._invoke_facet_dispatch(faceta, CONFIG, "u-ollama", "hola")
    return prompts[0]


async def test_solo_jax_local_recibe_el_dato_de_su_modelo(monkeypatch):
    assert "qwen-x" in await _capturar_prompts(monkeypatch, "jax_local")
    assert "qwen-x" not in await _capturar_prompts(monkeypatch, "otra")


def test_una_sola_llamada_a_call_ollama_en_el_dispatch():
    arbol = ast.parse((BACKEND / "api/chat.py").read_text(encoding="utf-8"))
    (funcion,) = [n for n in arbol.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "_invoke_facet_dispatch"]
    llamadas = [n for n in ast.walk(funcion)
                if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "_call_ollama"]
    assert len(llamadas) == 1


def test_los_sets_llevan_los_nombres_del_espejo():
    for nombre in ("KIMI_KW", "KIMI_STRONG", "HIPATIA_KW", "HIPATIA_STRONG", "JEKYLL_KW",
                   "JEKYLL_STRONG", "THOT_KW", "THOT_STRONG", "ADA_KW", "ADA_STRONG"):
        assert isinstance(getattr(chat_mod, nombre), frozenset), nombre
        assert not hasattr(chat_mod, "_" + nombre)
    assert chat_mod._TIEBREAK == ("hipatia", "thot", "ada", "kimi", "jekyll")
    assert set(chat_mod._KW_SETS) == {"kimi", "hipatia", "jekyll", "thot", "ada"}


def test_hyde_responde_un_aviso(client, chat_sin_memoria):
    resp = client.post("/api/chat", json={"message": "hola", "facet": "hyde"},
                       headers=cabeceras(client, "chat-codigos-hyde", "operator"))
    assert resp.status_code == 200, resp.text
    cuerpo = resp.json()
    assert cuerpo["aviso"] == {"code": "hyde_usa_modo_comando", "params": {}}
    assert cuerpo["response"] == "[hyde_usa_modo_comando]"
