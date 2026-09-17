"""/api/chat con adjuntos (frente D, 2026-09-16). Antes (26c9cd5) ChatRequest
no declaraba nada de esto y pydantic descartaba image_base64/file_context en
silencio (green-verif-notocar-regla.md, punto 2)."""
import base64
import json

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
        if "content" in kwargs:
            # Con imagen el cuerpo va de un solo uso (R16): se lee del stream.
            cuerpo = json.loads(b"".join([p async for p in kwargs["content"]]))
        else:
            cuerpo = kwargs.get("json")
        self.pedidos.append((url, cuerpo))

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


def test_imagen_a_faceta_con_vision_llega_y_el_base64_no_queda_en_logs_memoria_ni_historial(
        client, grabador, monkeypatch, caplog):
    import logging

    import shadow_validation

    b64 = base64.b64encode(PNG + b"MARCA-UNICA-DEL-BASE64-FRENTE-D" * 8).decode()
    guardados: list[str] = []

    class _Memoria:
        def save_message(self, conv_uuid, role, content, **kw):
            guardados.append(content)

    async def resolver(_key):
        return _resuelta("jax_local", "ollama", frozenset({"text", "image"}))

    async def conversacion(*a, **k):
        return "conv-test-frente-d"

    async def sin_contexto(*a, **k):
        return []

    async def sin_shadow(*a, **k):
        return None

    estados: list[str] = []

    async def espia_estado(facet, status, tenant_id, user_id, message=""):
        estados.append(message)

    monkeypatch.setattr(chat_mod, "resolve_facet", resolver)
    monkeypatch.setattr(chat_mod, "_get_conv_uuid", conversacion)
    monkeypatch.setattr(chat_mod, "_semantic_context", sin_contexto)
    monkeypatch.setattr(chat_mod, "_memory", _Memoria())
    monkeypatch.setattr(chat_mod.engine_state, "set_facet_status", espia_estado)
    monkeypatch.setattr(shadow_validation, "run_shadow_validation", sin_shadow)
    caplog.set_level(logging.DEBUG)

    imagen = {"tipo": "imagen", "nombre": "f.png", "mime": "image/png", "base64": b64}
    r = client.post("/api/chat", json={"message": "describí", "facet": "jax_local", "adjuntos": [imagen]},
                    headers=cabeceras(client, "test-adjuntos-chat"))
    assert r.status_code == 200, r.text

    (_, cuerpo), = grabador.pedidos
    assert cuerpo["messages"][-1]["images"] == [b64]          # llegó al proveedor
    muestra = b64[40:120]
    assert muestra not in caplog.text                        # logs
    assert all(muestra not in g for g in guardados)          # memoria
    assert guardados[0].endswith("tipo=\"image/png\" bytes=%d]" % len(base64.b64decode(b64)))
    assert all(muestra not in (m or "") for m in estados)    # bus de estado
    turnos = [h for v in chat_mod._conversations.values() for h in v if "describí" in h["content"]]
    assert turnos and all(muestra not in h["content"] for h in turnos)   # historial


def test_el_adjunto_que_habla_de_un_modelo_se_lee_y_no_dispara_el_aviso_de_identidad(
        client, grabador, monkeypatch):
    # Revisión final (I1): la heurística de identidad miraba el mensaje YA
    # compuesto con el adjunto. Un documento que dice "we use a linear
    # regression model" recibía el aviso enlatado en vez de leerse.
    contenido = "We are using a linear regression model for the forecast."
    assert chat_mod._is_model_identity_question(contenido)       # el contenido sí dispara
    assert not chat_mod._is_model_identity_question("resumí")    # el mensaje del usuario no

    async def resolver(_key):
        return _resuelta("jax_local", "ollama", frozenset({"text"}))

    llamadas: list[str] = []

    async def ollama(system_prompt, history, message, config, model, *, imagenes=()):
        llamadas.append(message)
        return _CONTRATO, 1, 1

    monkeypatch.setattr(chat_mod, "resolve_facet", resolver)
    monkeypatch.setattr(chat_mod, "_call_ollama", ollama)
    texto = {"tipo": "texto", "origen": "texto", "nombre": "informe.txt", "contenido": contenido}
    r = client.post("/api/chat", json={"message": "resumí", "facet": "jax_local", "adjuntos": [texto]},
                    headers=cabeceras(client, "test-adjuntos-chat"))
    assert r.status_code == 200, r.text
    assert r.json().get("aviso") is None
    assert llamadas == [f'resumí\n\n<<<ADJUNTO nombre="informe.txt" origen="texto">>>\n{contenido}\n<<<FIN ADJUNTO>>>']


def test_la_pregunta_de_identidad_del_usuario_sigue_recibiendo_el_aviso_con_adjunto(
        client, grabador, monkeypatch):
    # Contracara de la anterior: el usuario sí pregunta; el adjunto no lo tapa.
    async def resolver(_key):
        return _resuelta("jax_local", "ollama", frozenset({"text"}))

    async def ollama(*a, **k):
        raise AssertionError("la pregunta de identidad no llama al proveedor")

    monkeypatch.setattr(chat_mod, "resolve_facet", resolver)
    monkeypatch.setattr(chat_mod, "_call_ollama", ollama)
    texto = {"tipo": "texto", "origen": "texto", "nombre": "n.txt", "contenido": "ventas 42"}
    r = client.post("/api/chat", json={"message": "que modelo sos", "facet": "jax_local", "adjuntos": [texto]},
                    headers=cabeceras(client, "test-adjuntos-chat"))
    assert r.status_code == 200, r.text
    assert r.json()["aviso"]["code"] == "identidad_del_modelo"


@pytest.mark.parametrize("tipo", ["texto", "imagen"])
def test_nombre_de_adjunto_desmedido_es_422_antes_de_cualquier_trabajo(client, grabador, monkeypatch, tipo):
    # Revisión final (I2): sin max_length, un nombre de 10 MB llegaba a
    # nombre_seguro y bloqueaba el loop ~455 ms filtrando carácter a carácter.
    from adjuntos import contrato, tipos

    def no_llamar(*a, **k):
        raise AssertionError("nombre_seguro no debe correr con un nombre desmedido")

    monkeypatch.setattr(contrato, "nombre_seguro", no_llamar)
    nombre = "a" * (tipos.NOMBRE_CRUDO_MAX + 1)
    if tipo == "texto":
        adjunto = {"tipo": "texto", "origen": "texto", "nombre": nombre, "contenido": "x"}
    else:
        adjunto = {**_imagen(), "nombre": nombre}
    r = client.post("/api/chat", json={"message": "hola", "facet": "jax_local", "adjuntos": [adjunto]},
                    headers=cabeceras(client, "test-adjuntos-chat"))
    assert r.status_code == 422, r.text
    (error,) = r.json()["detail"]
    assert error["type"] == "string_too_long"
    assert error["loc"][-1] == "nombre"
    assert grabador.pedidos == []
