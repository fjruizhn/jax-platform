"""/api/chat con adjuntos (frente D, 2026-09-16). Antes (26c9cd5) ChatRequest
no declaraba nada de esto y pydantic descartaba image_base64/file_context en
silencio (green-verif-notocar-regla.md, punto 2)."""
import base64

import pytest

import api.chat as chat_mod
import http_client
from facet_resolver import ResolvedFacet
from tests.adjuntos_muestras import PNG
from tests.identidades import cabeceras

pytestmark = pytest.mark.usefixtures("chat_sin_memoria")

_CONTRATO = '{"claim": [], "analysis": "recibido", "judgment": null}'


class _Grabador:
    """Registra (url, json) de cada POST y responde con forma Ollama y OpenAI."""

    def __init__(self):
        self.pedidos: list[tuple[str, dict]] = []

    async def post(self, url, **kwargs):
        self.pedidos.append((url, kwargs.get("json")))

        class _R:
            def raise_for_status(self):
                pass

            def json(self):
                return {"message": {"content": _CONTRATO}, "prompt_eval_count": 1, "eval_count": 1,
                        "choices": [{"message": {"content": _CONTRATO}}],
                        "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
        return _R()


def _resuelta(key, transport, modalidades):
    return ResolvedFacet(key=key, provider_id="ollama", base_url="http://x.invalid/v1", model="modelo-test",
                         credential="", transport=transport, persona=None, params=None,
                         max_tokens_param="max_tokens", max_output_tokens=1000,
                         input_modalities=modalidades)


def _imagen():
    return {"tipo": "imagen", "nombre": "f.png", "mime": "image/png", "base64": base64.b64encode(PNG).decode()}


@pytest.fixture
def grabador():
    g = _Grabador()
    original = http_client._client
    http_client._client = g
    yield g
    http_client._client = original


def test_un_campo_desconocido_ya_no_se_descarta_en_silencio(client, grabador):
    # El bundle viejo mandaba image_base64: hoy es 422, no un turno sin la imagen.
    r = client.post("/api/chat", json={"message": "hola", "facet": "jax_local", "image_base64": "x"},
                    headers=cabeceras(client, "test-adjuntos-chat"))
    assert r.status_code == 422
    assert grabador.pedidos == []


def test_imagen_a_faceta_sin_vision_es_422_antes_del_proveedor(client, grabador, monkeypatch):
    estados = []

    async def resolver(_key):
        return _resuelta("jekyll", "http_openai_compat", frozenset({"text"}))

    async def espia_estado(facet, status, *a, **k):
        estados.append(status)

    monkeypatch.setattr(chat_mod, "resolve_facet", resolver)
    monkeypatch.setattr(chat_mod.engine_state, "set_facet_status", espia_estado)
    r = client.post("/api/chat", json={"message": "mirá", "facet": "jekyll", "adjuntos": [_imagen()]},
                    headers=cabeceras(client, "test-adjuntos-chat"))
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == {"code": "imagen_no_soportada", "facet": "jekyll"}
    assert grabador.pedidos == []      # ni authorize-facet ni proveedor
    assert estados == []               # la faceta nunca pasó a "thinking"


def test_hyde_con_adjunto_es_422(client, grabador):
    texto = {"tipo": "texto", "origen": "texto", "nombre": "n.txt", "contenido": "x"}
    r = client.post("/api/chat", json={"message": "hola", "facet": "hyde", "adjuntos": [texto]},
                    headers=cabeceras(client, "test-adjuntos-chat"))
    assert r.status_code == 422
    assert r.json()["detail"] == {"code": "adjuntos_no_soportados", "facet": "hyde"}


def test_adjunto_de_texto_llega_al_modelo_delimitado(client, grabador, monkeypatch):
    async def resolver(_key):
        return _resuelta("jax_local", "ollama", frozenset({"text"}))

    monkeypatch.setattr(chat_mod, "resolve_facet", resolver)
    texto = {"tipo": "texto", "origen": "pdf", "nombre": "i.pdf", "contenido": "ventas 42"}
    r = client.post("/api/chat", json={"message": "resumí", "facet": "jax_local", "adjuntos": [texto]},
                    headers=cabeceras(client, "test-adjuntos-chat"))
    assert r.status_code == 200, r.text
    (_, cuerpo), = grabador.pedidos
    ultimo = cuerpo["messages"][-1]
    assert ultimo["role"] == "user"
    assert ultimo["content"] == 'resumí\n\n<<<ADJUNTO nombre="i.pdf" origen="pdf">>>\nventas 42\n<<<FIN ADJUNTO>>>'
