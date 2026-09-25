"""/api/chat con adjuntos POR REFERENCIA (frente D; RD3, 2026-09-17).

Antes (26c9cd5) ChatRequest no declaraba adjuntos y pydantic descartaba
image_base64/file_context en silencio. Después (frente D, 2026-09-16) el
cliente mandaba el base64 o el texto dentro del JSON. Desde RD3 manda solo
`adjuntos: [{"id": ...}]`: el servidor busca cada id en el almacén (atado al
dueño), lee el texto o codifica la imagen desde disco y la empalma en el
cuerpo del proveedor. Un id que no es del usuario, vencido, desconocido o
roto en disco es el MISMO 404, sin memoria, sin estado y sin proveedor."""
import base64
import json
import logging
import os
from datetime import timedelta

import pytest

import api.chat as chat_mod
import http_client
from adjuntos import almacen
from auth.models import AuthUser
from facet_resolver import ResolvedFacet
from tests.adjuntos_muestras import PNG
from tests.identidades import cabeceras, uid

pytestmark = pytest.mark.usefixtures("chat_sin_memoria")

_CONTRATO = '{"claim": [], "analysis": "recibido", "judgment": null}'
_ETIQUETA = "test-adjuntos-chat"


class _Grabador:
    """Registra (url, cuerpo) de cada POST y responde con las formas de
    Ollama, OpenAI, Gemini y authorize-facet a la vez."""

    def __init__(self):
        self.pedidos: list[tuple[str, dict]] = []

    @property
    def al_proveedor(self):
        return [(u, c) for u, c in self.pedidos if "authorize-facet" not in u]

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
                return {"allowed": True,
                        "message": {"content": _CONTRATO}, "prompt_eval_count": 1, "eval_count": 1,
                        "choices": [{"message": {"content": _CONTRATO}}],
                        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                        "candidates": [{"content": {"parts": [{"text": _CONTRATO}]}}]}
        return _R()


def _resuelta(key, transport, modalidades):
    return ResolvedFacet(key=key, provider_id="ollama", base_url="http://x.invalid/v1", model="modelo-test",
                         credential="", transport=transport, persona=None, params=None,
                         max_tokens_param="max_tokens", max_output_tokens=1000,
                         input_modalities=modalidades)


@pytest.fixture
def grabador(monkeypatch):
    # E-21 (jax master) quitó api_url de jax_local en config.toml: sin esto,
    # el camino de Ollama da 502 antes de armar el cuerpo que se prueba.
    import copy
    config = copy.deepcopy(chat_mod._load_config())
    config["personalities"]["jax_local"]["api_url"] = "http://ollama.invalid/api/chat"
    monkeypatch.setattr(chat_mod, "_load_config", lambda: config)
    g = _Grabador()
    original = http_client._client
    http_client._client = g
    yield g
    http_client._client = original


def _duenio(client) -> AuthUser:
    return AuthUser(user_id=uid(client, _ETIQUETA), tenant_id="1", role="operator")


def _guardar_texto(user, texto="ventas 42", nombre="i.pdf", origen="pdf", ahora=None):
    return almacen.guardar_texto(almacen.cargar_directorio(), texto, user=user, origen=origen, nombre=nombre,
                                 bytes_=len(texto.encode()), recortado=False, ttl_horas=1, ahora=ahora)


def _guardar_imagen(user, datos=PNG, nombre="f.png"):
    directorio = almacen.cargar_directorio()
    temporal = directorio / almacen.nombre_temporal()
    temporal.write_bytes(datos)
    return almacen.guardar_imagen(directorio, temporal, user=user, mime="image/png", nombre=nombre,
                                  bytes_=len(datos), ttl_horas=1)


def _chat(client, adjuntos, facet="jax_local", message="resumí"):
    return client.post("/api/chat", json={"message": message, "facet": facet, "adjuntos": adjuntos},
                       headers=cabeceras(client, _ETIQUETA))


def _resolver(monkeypatch, transport="ollama", modalidades=frozenset({"text"})):
    async def resolver(key):
        return _resuelta(key, transport, modalidades)
    monkeypatch.setattr(chat_mod, "resolve_facet", resolver)


class _Espias:
    """Memoria, bus de estado y contexto semántico espiados."""

    def __init__(self, monkeypatch):
        self.memoria: list[str] = []
        self.estados: list[tuple[str, str]] = []
        espias = self

        class _Memoria:
            def save_message(self, conv_uuid, role, content, **kw):
                espias.memoria.append(content)

        async def conversacion(*a, **k):
            return "conv-test-frente-d"

        async def sin_contexto(*a, **k):
            return chat_mod.PromptMemoryContext(())

        async def espia_estado(facet, status, tenant_id=None, user_id=None, message=""):
            espias.estados.append((status, message or ""))

        async def sin_shadow(*a, **k):
            return None

        import shadow_validation
        monkeypatch.setattr(chat_mod, "_get_conv_uuid", conversacion)
        monkeypatch.setattr(chat_mod, "_prompt_memory_context", sin_contexto)
        monkeypatch.setattr(chat_mod, "_memory", _Memoria())
        monkeypatch.setattr(chat_mod.engine_state, "set_facet_status", espia_estado)
        monkeypatch.setattr(shadow_validation, "run_shadow_validation", sin_shadow)


# ------------------------------------------------------------------ contrato

def test_un_campo_desconocido_ya_no_se_descarta_en_silencio(client, grabador):
    # El bundle viejo mandaba image_base64: hoy es 422, no un turno sin la imagen.
    r = client.post("/api/chat", json={"message": "hola", "facet": "jax_local", "image_base64": "x"},
                    headers=cabeceras(client, _ETIQUETA))
    assert r.status_code == 422
    assert grabador.pedidos == []


@pytest.mark.parametrize("viejo", [
    {"tipo": "texto", "origen": "texto", "nombre": "n.txt", "contenido": "x"},
    {"tipo": "imagen", "nombre": "f.png", "mime": "image/png", "base64": base64.b64encode(PNG).decode()},
])
def test_el_contrato_en_linea_viejo_es_422(client, grabador, monkeypatch, viejo):
    espias = _Espias(monkeypatch)
    r = _chat(client, [viejo])
    assert r.status_code == 422, r.text
    tipos = {e["type"] for e in r.json()["detail"]}
    assert "extra_forbidden" in tipos and "missing" in tipos
    assert grabador.pedidos == [] and espias.memoria == [] and espias.estados == []


@pytest.mark.parametrize("malo", ["../../etc/passwd", "a" * 31, "a" * 33, "a" * 31 + ".", "a" * 31 + "=", 12345, None])
def test_un_id_con_formato_invalido_se_corta_en_el_borde(client, grabador, monkeypatch, malo):
    espias = _Espias(monkeypatch)

    async def prohibido(*a, **k):
        raise AssertionError("un id malformado no llega al almacén")

    monkeypatch.setattr(almacen, "obtener", prohibido)
    monkeypatch.setattr(almacen, "leer", prohibido)
    r = _chat(client, [{"id": malo}])
    assert r.status_code == 422, r.text
    (error,) = r.json()["detail"]
    assert error["loc"][-1] == "id"
    assert grabador.pedidos == [] and espias.memoria == [] and espias.estados == []


def test_hyde_con_adjunto_es_422(client, grabador):
    meta = _guardar_texto(_duenio(client))
    r = _chat(client, [{"id": meta["id"]}], facet="hyde", message="hola")
    assert r.status_code == 422
    assert r.json()["detail"] == {"code": "adjuntos_no_soportados", "facet": "hyde"}


def test_mas_adjuntos_que_el_tope_es_422_sin_tocar_el_almacen(client, grabador, monkeypatch):
    monkeypatch.setenv("JAX_ADJUNTO_MAX_POR_MENSAJE", "1")
    espias = _Espias(monkeypatch)
    user = _duenio(client)
    ids = [{"id": _guardar_texto(user)["id"]}, {"id": _guardar_texto(user)["id"]}]

    async def prohibido(*a, **k):
        raise AssertionError("pasado el tope no se busca ningún id")

    monkeypatch.setattr(almacen, "obtener", prohibido)
    r = _chat(client, ids)
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == {"code": "adjuntos_demasiados", "max": 1}
    assert grabador.pedidos == [] and espias.memoria == [] and espias.estados == []


# ------------------------------------------------------- lo que ve el modelo

def test_adjunto_de_texto_por_id_llega_al_modelo_delimitado(client, grabador, monkeypatch):
    _resolver(monkeypatch)
    meta = _guardar_texto(_duenio(client))
    r = _chat(client, [{"id": meta["id"]}])
    assert r.status_code == 200, r.text
    (_, cuerpo), = grabador.al_proveedor
    ultimo = cuerpo["messages"][-1]
    assert ultimo["role"] == "user"
    assert ultimo["content"] == 'resumí\n\n<<<ADJUNTO nombre="i.pdf" origen="pdf">>>\nventas 42\n<<<FIN ADJUNTO>>>'


def _imagen_en_el_cuerpo(transport, cuerpo):
    if transport == "ollama":
        (b64,) = cuerpo["messages"][-1]["images"]
        return b64
    if transport == "http_openai_compat":
        texto, imagen = cuerpo["messages"][-1]["content"]
        assert texto == {"type": "text", "text": "describí"}
        prefijo = "data:image/png;base64,"
        assert imagen["type"] == "image_url" and imagen["image_url"]["url"].startswith(prefijo)
        return imagen["image_url"]["url"][len(prefijo):]
    texto, imagen = cuerpo["contents"][-1]["parts"]
    assert texto == {"text": "describí"}
    assert imagen["inline_data"]["mime_type"] == "image/png"
    return imagen["inline_data"]["data"]


@pytest.mark.parametrize("transport", ["ollama", "http_openai_compat", "http_gemini"])
def test_imagen_por_id_llega_al_proveedor_en_su_formato(client, grabador, monkeypatch, transport):
    _resolver(monkeypatch, transport, frozenset({"text", "image"}))
    # Más de dos tramos de codificación: el empalme junta varios pedazos.
    datos = PNG + os.urandom(2 * almacen.TRAMO_DE_BASE64 + 11)
    meta = _guardar_imagen(_duenio(client), datos)
    r = _chat(client, [{"id": meta["id"]}], message="describí")
    assert r.status_code == 200, r.text
    (_, cuerpo), = grabador.al_proveedor
    assert _imagen_en_el_cuerpo(transport, cuerpo) == base64.b64encode(datos).decode()


def test_imagen_a_faceta_sin_vision_es_422_antes_de_leerla_y_del_proveedor(client, grabador, monkeypatch):
    _resolver(monkeypatch, "http_openai_compat", frozenset({"text"}))
    espias = _Espias(monkeypatch)
    meta = _guardar_imagen(_duenio(client))

    async def prohibido(*a, **k):
        raise AssertionError("la imagen no se codifica para una faceta que no la ve")

    monkeypatch.setattr(almacen, "leer_imagen_en_base64", prohibido)
    r = _chat(client, [{"id": meta["id"]}], facet="jekyll", message="mirá")
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == {"code": "imagen_no_soportada", "facet": "jekyll"}
    assert grabador.pedidos == []      # ni authorize-facet ni proveedor
    assert espias.estados == [] and espias.memoria == []


# ------------------------------------------------------ todos los "no": un 404

def _caso_de_404(client, caso):
    user = _duenio(client)
    directorio = almacen.cargar_directorio()
    if caso == "ajeno":
        return _guardar_texto(AuthUser(user_id="999999991", tenant_id="1", role="operator"))["id"]
    if caso == "otro_tenant":
        return _guardar_texto(AuthUser(user_id=user.user_id, tenant_id="2", role="operator"))["id"]
    if caso == "vencido":
        return _guardar_texto(user, ahora=almacen._ahora() - timedelta(hours=2))["id"]
    if caso == "desconocido":
        return almacen.nuevo_id()
    if caso == "sidecar_corrupto":
        id_ = _guardar_texto(user)["id"]
        (almacen.carpeta_de_usuario(directorio, user.user_id) / f"{id_}.json").write_text("{no es json")
        return id_
    if caso == "dato_de_texto_borrado":
        id_ = _guardar_texto(user)["id"]
        (almacen.carpeta_de_usuario(directorio, user.user_id) / f"{id_}.dato").unlink()
        return id_
    if caso == "dato_de_imagen_borrado":
        id_ = _guardar_imagen(user)["id"]
        (almacen.carpeta_de_usuario(directorio, user.user_id) / f"{id_}.dato").unlink()
        return id_
    raise AssertionError(caso)


_CASOS_404 = ["ajeno", "otro_tenant", "vencido", "desconocido", "sidecar_corrupto",
              "dato_de_texto_borrado", "dato_de_imagen_borrado"]


def test_ajeno_vencido_desconocido_y_roto_son_el_mismo_404_sin_efectos(client, grabador, monkeypatch):
    _resolver(monkeypatch, "ollama", frozenset({"text", "image"}))
    espias = _Espias(monkeypatch)
    cuerpos = set()
    for caso in _CASOS_404:
        id_ = _caso_de_404(client, caso)
        r = _chat(client, [{"id": id_}])
        assert r.status_code == 404, (caso, r.text)
        assert id_ not in r.text, caso
        cuerpos.add(r.content)
    assert cuerpos == {json.dumps({"detail": {"code": "adjunto_no_encontrado"}},
                                  separators=(",", ":")).encode()}
    assert grabador.pedidos == []
    assert espias.memoria == []
    assert espias.estados == []          # nunca "thinking"


def test_una_imagen_ajena_o_desconocida_a_faceta_sin_vision_es_el_404_y_no_el_422(client, grabador, monkeypatch):
    """Ruling R26 (2), fijado en el Final fix wave #2: el chequeo de visión usa
    el `tipo` del sidecar, así que solo puede correr DESPUÉS de una búsqueda
    atada al dueño. Un id bien formado de una imagen AJENA, o uno desconocido,
    mandado a una faceta SIN visión tiene que dar el mismo 404
    `adjunto_no_encontrado` byte a byte: un 422 `imagen_no_soportada` diría
    "existe y es una imagen". Sin memoria, sin estado, sin authorize-facet ni
    proveedor, y sin leer la imagen. Control: la misma clase de imagen, del
    propio usuario, sí es 422 (si no, el test no distinguiría nada)."""
    _resolver(monkeypatch, "http_openai_compat", frozenset({"text"}))
    espias = _Espias(monkeypatch)

    async def prohibido(*a, **k):
        raise AssertionError("no se lee ninguna imagen en un rechazo")

    monkeypatch.setattr(almacen, "leer_imagen_en_base64", prohibido)
    ajena = _guardar_imagen(AuthUser(user_id="999999992", tenant_id="1", role="operator"))["id"]
    desconocida = almacen.nuevo_id()
    cuerpo_404 = json.dumps({"detail": {"code": "adjunto_no_encontrado"}}, separators=(",", ":")).encode()
    for id_ in (ajena, desconocida):
        r = _chat(client, [{"id": id_}], facet="jekyll", message="mirá")
        assert r.status_code == 404, r.text
        assert r.content == cuerpo_404
    assert grabador.pedidos == []
    assert espias.memoria == [] and espias.estados == []

    propia = _guardar_imagen(_duenio(client))["id"]
    control = _chat(client, [{"id": propia}], facet="jekyll", message="mirá")
    assert control.status_code == 422, control.text
    assert control.json()["detail"]["code"] == "imagen_no_soportada"


def test_el_404_no_dice_cual_de_los_ids_fallo(client, grabador, monkeypatch):
    monkeypatch.setenv("JAX_ADJUNTO_MAX_POR_MENSAJE", "2")
    _resolver(monkeypatch)
    bueno = _guardar_texto(_duenio(client))["id"]
    malo = almacen.nuevo_id()
    a = _chat(client, [{"id": bueno}, {"id": malo}])
    b = _chat(client, [{"id": malo}, {"id": bueno}])
    assert a.status_code == b.status_code == 404
    assert a.content == b.content
    assert grabador.pedidos == []


def test_un_adjunto_que_borro_el_limpiador_entre_la_subida_y_el_chat_es_404(client, grabador, monkeypatch):
    _resolver(monkeypatch)
    hdrs = cabeceras(client, _ETIQUETA)
    subida = client.post("/api/chat/upload", files={"file": ("n.txt", b"ventas 42", "text/plain")}, headers=hdrs)
    assert subida.status_code == 200, subida.text
    id_ = subida.json()["id"]
    horas = almacen.cargar_ttl_horas()
    almacen.limpiar(almacen.cargar_directorio(), ahora=almacen._ahora() + timedelta(hours=horas + 1))
    assert not list(almacen.cargar_directorio().rglob(f"{id_}.json"))
    r = _chat(client, [{"id": id_}])
    assert r.status_code == 404, r.text
    assert r.json()["detail"] == {"code": "adjunto_no_encontrado"}
    assert grabador.pedidos == []


# ---------------------------------- ni el id ni el base64 salen del camino

def test_ni_el_id_ni_el_base64_quedan_en_logs_memoria_historial_ni_estado(
        client, grabador, monkeypatch, caplog):
    monkeypatch.setenv("JAX_ADJUNTO_MAX_POR_MENSAJE", "2")
    _resolver(monkeypatch, "ollama", frozenset({"text", "image"}))
    espias = _Espias(monkeypatch)
    caplog.set_level(logging.DEBUG)
    user = _duenio(client)
    datos = PNG + b"MARCA-UNICA-DEL-BASE64-FRENTE-D" * 64
    imagen = _guardar_imagen(user, datos, nombre="foto.png")
    texto = _guardar_texto(user, texto="CONTENIDO-DEL-DOCUMENTO", nombre="n.txt", origen="texto")

    r = _chat(client, [{"id": imagen["id"]}, {"id": texto["id"]}], message="describí")
    assert r.status_code == 200, r.text

    b64 = base64.b64encode(datos).decode()
    (_, cuerpo), = grabador.al_proveedor
    assert cuerpo["messages"][-1]["images"] == [b64]          # llegó al proveedor
    muestra = b64[40:120]
    secretos = [imagen["id"], texto["id"], muestra]
    turnos = [h["content"] for v in chat_mod._conversations.values() for h in v if "describí" in h["content"]]
    assert turnos
    for secreto in secretos:
        assert secreto not in caplog.text, secreto                                 # logs
        assert all(secreto not in m for m in espias.memoria), secreto              # memoria
        assert all(secreto not in t for t in turnos), secreto                      # historial
        assert all(secreto not in m for _, m in espias.estados), secreto           # bus de estado
    assert espias.memoria[0].splitlines() == [
        "describí",
        '[adjunto nombre="n.txt" tipo="texto" caracteres=23]',
        f'[adjunto nombre="foto.png" tipo="image/png" bytes={len(datos)}]',
    ]
    # El historial en RAM sí conserva el texto (la pregunta siguiente lo usa).
    assert "CONTENIDO-DEL-DOCUMENTO" in turnos[-1]
    assert all("CONTENIDO-DEL-DOCUMENTO" not in m for m in espias.memoria)


# ------------------------------------------------ identidad (revisión final I1)

def test_el_adjunto_que_habla_de_un_modelo_se_lee_y_no_dispara_el_aviso_de_identidad(
        client, grabador, monkeypatch):
    contenido = "We are using a linear regression model for the forecast."
    assert chat_mod._is_model_identity_question(contenido)       # el contenido sí dispara
    assert not chat_mod._is_model_identity_question("resumí")    # el mensaje del usuario no
    _resolver(monkeypatch)
    llamadas: list[str] = []

    async def ollama(system_prompt, history, message, config, model, *, imagenes=()):
        llamadas.append(message)
        return _CONTRATO, 1, 1

    monkeypatch.setattr(chat_mod, "_call_ollama", ollama)
    meta = _guardar_texto(_duenio(client), texto=contenido, nombre="informe.txt", origen="texto")
    r = _chat(client, [{"id": meta["id"]}])
    assert r.status_code == 200, r.text
    assert r.json().get("aviso") is None
    assert llamadas == [f'resumí\n\n<<<ADJUNTO nombre="informe.txt" origen="texto">>>\n{contenido}\n<<<FIN ADJUNTO>>>']


def test_la_pregunta_de_identidad_del_usuario_sigue_recibiendo_el_aviso_con_adjunto(
        client, grabador, monkeypatch):
    _resolver(monkeypatch)

    async def ollama(*a, **k):
        raise AssertionError("la pregunta de identidad no llama al proveedor")

    monkeypatch.setattr(chat_mod, "_call_ollama", ollama)
    meta = _guardar_texto(_duenio(client))
    r = _chat(client, [{"id": meta["id"]}], message="que modelo sos")
    assert r.status_code == 200, r.text
    assert r.json()["aviso"]["code"] == "identidad_del_modelo"
