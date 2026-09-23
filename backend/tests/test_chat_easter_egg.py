"""Easter egg IDE1990 en la Mesa web (2026-09-23).

Existía solo en el REPL (jax/core/router.py::route, paso 1). En la Mesa no
había nada que lo reconociera: el 2026-08-09 Fernando lo escribió acá, no
pasó nada, y el modelo de turno inventó un "IDE2024" que terminó guardado
como hecho verificado (#106). Mismo contrato que el REPL: se chequea ANTES
que todo, no llama a ningún modelo y suelta el mensaje de Jairo Urbina.
"""
from collections import OrderedDict

import pytest

pytestmark = pytest.mark.usefixtures("chat_sin_memoria")

import http_client
from api import chat as chat_mod
from tests.identidades import token_de
from tests.test_chat_contract_wrapper import _FakePostClient, _FakeResponse


class _NingunProveedor:
    """Cualquier llamada saliente es un fallo: el easter egg no gasta modelo."""

    async def post(self, url, **kwargs):
        raise AssertionError(f"el easter egg no debe llamar a nadie: {url}")


def _post(client, token, mensaje, facet=None):
    cuerpo = {"message": mensaje}
    if facet is not None:
        cuerpo["facet"] = facet
    return client.post("/api/chat", json=cuerpo, headers={"Authorization": f"Bearer {token}"})


@pytest.fixture
def sin_proveedor():
    original = http_client._client
    http_client._client = _NingunProveedor()
    yield
    http_client._client = original


@pytest.mark.parametrize("mensaje", [
    "IDE1990", "ide1990", "IDE 1990", "  Ide1990  ", "hola IDE1990 que tal",
])
def test_ide1990_suelta_el_mensaje_sin_llamar_al_modelo(client, sin_proveedor, mensaje):
    token = token_de(client, "test-easter-egg-user", "operator", "1")
    resp = _post(client, token, mensaje)
    assert resp.status_code == 200, resp.text
    cuerpo = resp.json()
    assert cuerpo["response"] == chat_mod.EASTER_EGG_TEXT
    assert cuerpo["facet"] == "jax_local"
    assert cuerpo["contract_degraded"] is False


def test_ide1990_gana_aunque_se_haya_fijado_otra_faceta(client, sin_proveedor):
    # "Antes que todo", igual que el REPL: una faceta fijada no lo tapa.
    token = token_de(client, "test-easter-egg-facet-user", "operator", "1")
    resp = _post(client, token, "IDE1990", facet="jekyll")
    assert resp.status_code == 200, resp.text
    assert resp.json()["response"] == chat_mod.EASTER_EGG_TEXT


@pytest.mark.parametrize("mensaje", ["IDE2024", "IDE|990", "ide199", "tengo un IDE de 1990"])
def test_lo_que_no_es_el_disparador_va_al_modelo(client, mensaje):
    # El caso real del 2026-08-09 ("IDE|990") y el inventado ("IDE2024") NO
    # disparan: siguen el camino normal, con su llamada al proveedor.
    token = token_de(client, "test-easter-egg-no-user", "operator", "1")
    fake = _FakePostClient(
        _FakeResponse({
            "choices": [{"message": {"content":
                '{"claim": [], "analysis": "respuesta normal del modelo", "judgment": null}'}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }),
        authorize_response=_FakeResponse({"allowed": True, "reason": "OK"}),
    )
    original = http_client._client
    http_client._client = fake
    try:
        resp = _post(client, token, mensaje, facet="jekyll")
    finally:
        http_client._client = original
    assert resp.status_code == 200, resp.text
    assert resp.json()["response"] != chat_mod.EASTER_EGG_TEXT
    assert "respuesta normal del modelo" in resp.json()["response"]


def test_ide1990_queda_en_el_hilo_y_en_la_memoria(client, sin_proveedor, monkeypatch):
    # En el hilo en RAM, para que el turno siguiente sepa lo que pasó. Y en
    # la memoria, con el texto REAL: es la evidencia que le faltó al modelo
    # el 2026-08-09 para no inventar.
    guardados = []

    class _MemoriaFalsa:
        def save_message(self, conv_uuid, role, content, facet=None, model=None, latency_ms=None):
            guardados.append((conv_uuid, role, content, facet, model))

    async def _conv(*a, **k):
        return "conv-easter-egg"

    monkeypatch.setattr(chat_mod, "_memory", _MemoriaFalsa())
    monkeypatch.setattr(chat_mod, "_get_conv_uuid", _conv)
    # Hilo en RAM propio: es global del módulo y lo comparten los tests.
    monkeypatch.setattr(chat_mod, "_conversations", OrderedDict())
    token = token_de(client, "test-easter-egg-mem-user", "operator", "1")
    resp = _post(client, token, "IDE1990")
    assert resp.status_code == 200, resp.text

    assert ("conv-easter-egg", "user", "IDE1990", None, None) in guardados
    assert ("conv-easter-egg", "jax_local", chat_mod.EASTER_EGG_TEXT, "jax_local", "easter_egg") in guardados
    [hilo] = chat_mod._conversations.values()
    assert hilo == [{"role": "user", "content": "IDE1990"},
                    {"role": "assistant", "content": chat_mod.EASTER_EGG_TEXT}]
