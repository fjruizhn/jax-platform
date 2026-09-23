"""Easter egg IDE1990 en la Mesa web (2026-09-23).

Existía solo en el REPL (jax/core/router.py::route, paso 1). En la Mesa no
había nada que lo reconociera: el 2026-08-09 Fernando lo escribió acá, no
pasó nada, y el modelo de turno inventó un "IDE2024" que terminó guardado
como hecho verificado (#106). Mismo contrato que el REPL: se chequea ANTES
que todo (faceta fijada o adjuntos incluidos), no llama a ningún modelo, y la
respuesta no va a la memoria persistente.

El CRITERIO de disparo (es_easter_egg) lo prueba a fondo jax
(tests/test_router_easter_egg.py) y check_mirror_sync exige que esta copia
sea idéntica. Acá se prueba el cableado en el endpoint, más los falsos
positivos que la auditoría encontró en el criterio viejo.
"""
from collections import OrderedDict

import pytest

pytestmark = pytest.mark.usefixtures("chat_sin_memoria")

import http_client
from adjuntos import almacen
from api import chat as chat_mod
from tests.identidades import token_de
from tests.test_chat_contract_wrapper import _FakePostClient, _FakeResponse


class _NingunProveedor:
    """Cualquier llamada saliente es un fallo: el easter egg no gasta modelo."""

    async def post(self, url, **kwargs):
        raise AssertionError(f"el easter egg no debe llamar a nadie: {url}")


def _post(client, token, mensaje, facet=None, adjuntos=None):
    cuerpo = {"message": mensaje}
    if facet is not None:
        cuerpo["facet"] = facet
    if adjuntos is not None:
        cuerpo["adjuntos"] = adjuntos
    return client.post("/api/chat", json=cuerpo, headers={"Authorization": f"Bearer {token}"})


@pytest.fixture
def sin_proveedor():
    original = http_client._client
    http_client._client = _NingunProveedor()
    yield
    http_client._client = original


def _es_el_easter_egg(resp):
    assert resp.status_code == 200, resp.text
    cuerpo = resp.json()
    assert cuerpo["response"] == chat_mod.EASTER_EGG_TEXT
    assert cuerpo["facet"] == "jax_local"
    assert cuerpo["contract_degraded"] is False


@pytest.mark.parametrize("mensaje", [
    "IDE1990", "ide1990", "IDE 1990", "  Ide1990  ", "hola IDE1990 que tal",
])
def test_ide1990_suelta_el_mensaje_sin_llamar_al_modelo(client, sin_proveedor, mensaje):
    token = token_de(client, "test-easter-egg-user", "operator", "1")
    _es_el_easter_egg(_post(client, token, mensaje))


@pytest.mark.parametrize("facet", ["jekyll", "hyde"])
def test_ide1990_gana_aunque_se_haya_fijado_otra_faceta(client, sin_proveedor, facet):
    # "Antes que todo", igual que el REPL. hyde incluido: tiene su propio
    # intercept sin LLM y el easter egg va antes que él.
    token = token_de(client, "test-easter-egg-facet-user", "operator", "1")
    _es_el_easter_egg(_post(client, token, "IDE1990", facet=facet))


def test_ide1990_gana_aunque_venga_un_adjunto(client, sin_proveedor, monkeypatch):
    # Un id bien formado que no es del usuario daría 404 del almacén: el
    # easter egg va antes, así que ni se busca.
    async def prohibido(*a, **k):
        raise AssertionError("el easter egg no toca el almacén de adjuntos")

    monkeypatch.setattr(almacen, "obtener", prohibido)
    monkeypatch.setattr(almacen, "leer", prohibido)
    token = token_de(client, "test-easter-egg-adjunto-user", "operator", "1")
    _es_el_easter_egg(_post(client, token, "IDE1990", adjuntos=[{"id": almacen.nuevo_id()}]))


@pytest.mark.parametrize("mensaje", [
    # Falsos positivos del criterio viejo (subcadena sin espacios), que en la
    # Mesa multiusuario le quitaban la respuesta a cualquiera.
    "el cliente pide 1990 unidades", "Please provide 1990 census data",
    # El caso real del 2026-08-09 y el inventado.
    "IDE|990", "IDE2024",
])
def test_lo_que_no_es_el_disparador_va_al_modelo(client, mensaje):
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


def test_ide1990_va_al_hilo_pero_no_a_la_memoria_persistente(client, sin_proveedor, monkeypatch):
    # Al hilo en RAM sí: el turno siguiente tiene que saber qué pasó. A la
    # memoria persistente no, ni el mensaje ni la respuesta: el extractor de
    # hechos la lee sin filtro (auditoría 2026-09-23, MAJOR-2).
    guardados = []

    class _MemoriaFalsa:
        def save_message(self, *a, **k):
            guardados.append((a, k))

    async def _conv(*a, **k):
        return "conv-easter-egg"

    monkeypatch.setattr(chat_mod, "_memory", _MemoriaFalsa())
    monkeypatch.setattr(chat_mod, "_get_conv_uuid", _conv)
    # Hilo en RAM propio: es global del módulo y lo comparten los tests.
    monkeypatch.setattr(chat_mod, "_conversations", OrderedDict())
    token = token_de(client, "test-easter-egg-mem-user", "operator", "1")
    _es_el_easter_egg(_post(client, token, "IDE1990"))

    assert guardados == []
    [hilo] = chat_mod._conversations.values()
    assert hilo == [{"role": "user", "content": "IDE1990"},
                    {"role": "assistant", "content": chat_mod.EASTER_EGG_TEXT}]


def test_ide1990_no_deja_la_faceta_pensando(client, sin_proveedor, monkeypatch):
    # Nunca pasa a "thinking": no hay nada que dejar colgado.
    estados = []

    async def _registrar(facet, estado, *a, **k):
        estados.append((facet, estado))

    monkeypatch.setattr(chat_mod.engine_state, "set_facet_status", _registrar)
    token = token_de(client, "test-easter-egg-estado-user", "operator", "1")
    _es_el_easter_egg(_post(client, token, "IDE1990"))
    assert all(estado != "thinking" for _, estado in estados)
