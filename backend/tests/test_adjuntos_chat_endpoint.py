"""/api/chat con adjuntos POR REFERENCIA (frente D; RD3, 2026-09-17).

Antes (26c9cd5) ChatRequest no declaraba adjuntos y pydantic descartaba
image_base64/file_context en silencio. Después (frente D, 2026-09-16) el
cliente mandaba el base64 o el texto dentro del JSON. Desde RD3 manda solo
`adjuntos: [{"id": ...}]`: el servidor busca cada id en el almacén (atado al
dueño), lee el texto o codifica la imagen desde disco y la empalma en el
cuerpo del proveedor. Un id que no es del usuario, vencido, desconocido o
roto en disco es el MISMO 404, sin memoria, sin estado y sin proveedor."""
import asyncio
import base64
import json
import logging
import math
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
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
        self.delay_s = 0.0

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
        if self.delay_s:
            import asyncio
            await asyncio.sleep(self.delay_s)

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
    assert r.json()["aviso"] is None
    assert r.json()["response"] == "The response could not be verified safely."
    assert r.json()["contract_state"] == "DEGRADED_STRUCTURED"


# Topes de la prueba de carga E3 (ver el comentario dentro de la prueba y el informe).
TECHO_SUBIDA_MS = 8000     # red absoluta de la subida p95 (ver el comentario en la prueba)
TOPE_CHAT_MS = 5000
LATIDO_P99_MAX_MS = 40     # retraso p99 tolerado del loop de la app (ver el comentario en la prueba)
_LATIDO_S = 0.01           # el latido duerme 10 ms en bucle y mide cuánto se atrasa cada despertar
_ESCALONADO_S = 0.05    # separación entre la llegada de un usuario y la del siguiente
_SONDEO_S = 0.1         # pausa entre vueltas de despacho + sondeo
_OCR_SIMULADO_S = 3.0  # lo que tarda el OCR remoto simulado: el PDF sigue en proceso mientras se chatea


@pytest.mark.skipif(os.getenv("JAX_CI_NO_DB") == "1", reason="requiere MariaDB desechable de CI")
def test_carga_e3_20_usuarios_chatean_mientras_se_procesan_sus_pdf(
        client, grabador, monkeypatch, tmp_path, request, ajustes_en_db):
    """Mide E3 como lo vive el usuario: chatear MIENTRAS su PDF escaneado se procesa.

    MariaDB, workspace, autorización, upload, despachador y endpoint de estado
    son reales. Solo se sustituye LAS MANOS (servicio OCR remoto), que tarda
    `_OCR_SIMULADO_S` por trabajo para que el PDF siga en proceso mientras
    corren los turnos de chat.

    Qué se mide (latencia POR PETICIÓN, no el tiempo de pared del lote):
      - p95 de cada subida `POST /api/chat/upload` (20 PDF al tope, concurrentes);
      - p95 de cada turno `POST /api/chat` (5 por usuario, 100 en total), medidos
        aparte, mientras el hilo principal corre `despachador.ciclo` (despacho y
        sondeo reales) y consulta el estado de cada documento.
    Un bloqueo del event loop en la ruta de subida (p. ej. `time.sleep` dentro de
    `encolar_pdf_desde_chat`) sube las dos cifras: los chats esperan el loop.

    Qué falla: (1) el LATIDO del loop de la app (p99 del atraso de una tarea que duerme
    10 ms en bucle en el mismo loop) -- vigila el bloqueo del loop sin depender de los núcleos;
    (2) el techo absoluto de la subida p95 y (3) el tope de chat, como red.
    Topes (JAX_E3_LATIDO_P99_MAX_MS / JAX_E3_SUBIDA_TECHO_MS / JAX_E3_CHAT_P95_MAX_MS): ver el
    comentario de los valores por defecto y el informe.
    """
    import uuid
    from db.connection import get_pool
    from proyectos_documentos import despachador
    from tests.adjuntos_muestras import pdf_con_texto
    from tests.test_proyectos_documentos_api import Entorno
    from tests.identidades import sql
    import ajustes
    from adjuntos.limites import cargar_limites

    usuarios, turnos = 20, 5
    # LÍNEA BASE medida (hall9000, MariaDB 12.3.3 efímera, 2026-10-06; ver
    # docs/carga-e3-respaldo-chat-2026-10-06.md):
    #   chat   p95  1021-1623 ms (peor corrida: 1623 ms; es cola: 100 turnos en ~2 s sobre un loop)
    # TOPE DE CHAT = 1623 ms x 3 -> 5000 ms. La base ya es de cola, no de ruido absoluto.
    #
    # LO QUE SE VIGILA es que la ruta de subida no BLOQUEE el event loop de la app. Se mide
    # directo con un LATIDO: una tarea en el MISMO loop donde corre la app (el del portal de
    # TestClient) duerme `_LATIDO_S` en bucle y registra cuánto se atrasa cada despertar. Un
    # bloqueo síncrono de X ms en el loop atrasa el latido >= X ms con CUALQUIER número de
    # núcleos, y el trabajo legítimo en `to_thread` no lo atrasa. Se exige el p99 de los atrasos
    # <= `LATIDO_P99_MAX_MS` (40 ms): sin regresión el p99 midió 2-16 ms con 1, 2, 4 y 32 CPU;
    # con un `time.sleep` de 0,05 s REAL dentro de `encolar_pdf_desde_chat`, 97-146 ms. El p99 (y
    # no el máximo) tolera un tirón aislado del runner (GC, CPU robada) sin dejar pasar una
    # regresión, que atrasa decenas de despertares (ver el informe).
    # Un tope de p95 de subida NO sirve para esto: depende de los núcleos (hall9000 con 32 CPU
    # 32-94 ms; runner de CI 1215-1669 ms sin regresión) y una vara tomada de la misma ruta
    # (un costo aislado) se infla con la regresión igual que lo medido.
    # RED ABSOLUTA: `TECHO_SUBIDA_MS` (8000 ms) sobre la subida p95. El `time.sleep(0.5)` en
    # `encolar_pdf_desde_chat` da ~9800-10100 ms; el techo no ve regresiones pequeñas, para eso
    # está el latido.
    # El `time.sleep(0.5)` da chat p95 2100-3900 ms (bajo su tope de 5000): el bloqueo
    # no está en la ruta del chat y esa cifra es cola; el tope de chat solo ve regresiones GRANDES de su ruta (ver «Límites conocidos de los topes» en el informe).
    techo_subida_ms = int(os.getenv("JAX_E3_SUBIDA_TECHO_MS", str(TECHO_SUBIDA_MS)))
    tope_chat_ms = int(os.getenv("JAX_E3_CHAT_P95_MAX_MS", str(TOPE_CHAT_MS)))
    latido_p99_max_ms = float(os.getenv("JAX_E3_LATIDO_P99_MAX_MS", str(LATIDO_P99_MAX_MS)))
    mutante_s = float(os.getenv("JAX_E3_MUTANT_SLEEP_S", "0"))
    # Los topes tienen que poder ver el mutante: con `time.sleep(0.5)` los 20 bloqueos se
    # serializan en el loop y la subida 19 (p95 de 20) espera >= 19 x 0.5 s. Un techo por
    # encima de eso no detectaría ni el mutante de referencia; un latido por encima de 500 ms tampoco.
    assert 0 < techo_subida_ms < 19 * 500
    assert 0 < latido_p99_max_ms < 500
    assert 0 < tope_chat_ms

    workspace = tmp_path / "workspace"
    (workspace / "proyectos").mkdir(parents=True)
    os.chmod(workspace / "proyectos", 0o2770)
    monkeypatch.setenv("JAX_WORKSPACE_DIR", str(workspace))
    adjuntos = tmp_path / "adjuntos"
    adjuntos.mkdir(mode=0o700)
    monkeypatch.setenv("JAX_ADJUNTOS_DIR", str(adjuntos))
    # El cupo de subidas simultaneas (por defecto 2 por usuario y 4 globales) alcanza a la puerta del
    # chat y responde 429: aquí se mide la latencia del loop, no el rechazo del cupo, así que se sube
    # al máximo permitido (ajustes_en_db lo repone al terminar). El 429 tiene su propia prueba.
    ajustes_en_db.poner(**{"proyectos.documentos.subidas_por_usuario": "10",
                           "proyectos.documentos.subidas_globales": "50"})

    limites = cargar_limites()
    pdf_base = pdf_con_texto([""] * limites.max_paginas)
    entorno = Entorno(client)
    proyecto = entorno.proyecto()
    identidades = [entorno.miembro(proyecto, f"load-{n}", "CONTRIBUTOR") for n in range(usuarios)]
    max_bytes = min(limites.max_bytes, int(client.portal.call(ajustes.valor, ajustes.DOC_MAX_BYTES_ARCHIVO)))
    assert max_bytes >= len(pdf_base)
    payloads = []
    for n in range(usuarios):
        marca = f"e3-user-{n} ".encode()
        relleno = max_bytes - len(pdf_base) - len(marca) - 3  # `% ` y salto de línea del comentario
        assert relleno > 0
        for _ in range(4):  # startxref crece de longitud al insertar el comentario
            payload = pdf_con_texto([""] * limites.max_paginas, comentario=marca + b"x" * relleno)
            diferencia = max_bytes - len(payload)
            if diferencia == 0:
                break
            relleno += diferencia
        assert len(payload) == max_bytes
        payloads.append(payload)

    trabajos = {}
    despachado_en = {}
    class _Accepted:
        status_code = 202
        def __init__(self, job_id): self.job_id = job_id
        def json(self): return {"job_id": self.job_id}
    class _Processing:
        async def post(self, _url, **kwargs):
            job_id = "e3-" + uuid.uuid4().hex
            trabajos[job_id] = kwargs["json"]["rutas"][0]
            despachado_en[job_id] = time.perf_counter()
            return _Accepted(job_id)
        async def get(self, url, **_kwargs):
            job_id = url.rsplit("/", 1)[-1]
            # El OCR remoto tarda: mientras tanto el trabajo está `running` y el documento
            # sigue `procesando`, que es cuando el usuario chatea.
            if time.perf_counter() - despachado_en[job_id] < _OCR_SIMULADO_S:
                return type("Response", (), {"status_code": 200, "json": lambda self: {
                    "estado": "running", "resultados": []}})()
            return type("Response", (), {"status_code": 200, "json": lambda self: {
                "estado": "completed", "resultados": [{"archivo": trabajos[job_id], "estado": "ok"}]}})()
    async def processing_client(): return _Processing()
    monkeypatch.setattr(despachador, "get_http_client", processing_client)
    monkeypatch.setattr(despachador, "despachar_ahora", lambda: None)

    _resolver(monkeypatch)
    grabador.delay_s = 0.03
    if mutante_s:
        from api import proyectos_documentos as api_documentos
        original_encolar = api_documentos.encolar_pdf_desde_chat
        async def encolar_mutante(*args, **kwargs):
            time.sleep(mutante_s)  # bloquea el event loop, como lo haría un acceso síncrono
            return await original_encolar(*args, **kwargs)
        monkeypatch.setattr(api_documentos, "encolar_pdf_desde_chat", encolar_mutante)

    subidos: list[int] = []        # document_id, en orden de llegada
    terminales: list[int] = []     # documentos ya vistos en estado final por el sondeo
    lat_subida: list[float] = []
    lat_chat: list[float] = []
    chats_con_documentos_abiertos: list[bool] = []
    ciclos_en_vuelo = {"n": 0, "durante_chats": 0}
    chats_activos = {"n": 0}
    candado = threading.Lock()

    def usuario(n):
        time.sleep(n * _ESCALONADO_S)  # llegadas escalonadas: unos chatean mientras otros todavía suben
        inicio = time.perf_counter()
        response = client.post("/api/chat/upload", headers=identidades[n],
            data={"project_id": str(proyecto.id)},
            files={"file": (f"escaneo-{n}.pdf", payloads[n], "application/pdf")})
        lat_subida.append((time.perf_counter() - inicio) * 1000)
        assert response.status_code == 200, response.text
        documento = response.json()
        assert documento["tipo"] == "pdf_procesando"
        subidos.append((documento["document_id"], inicio))
        for turno in range(turnos):
            with candado:
                chats_activos["n"] += 1
            chats_con_documentos_abiertos.append(len(subidos) - len(terminales) > 0)
            t0 = time.perf_counter()
            try:
                chat = client.post("/api/chat", headers=identidades[n], json={
                    "message": f"consulta {n}.{turno}", "facet": "jax_local", "project_id": proyecto.id})
            finally:
                with candado:
                    chats_activos["n"] -= 1
            lat_chat.append((time.perf_counter() - t0) * 1000)
            assert chat.status_code == 200, chat.text
        return documento["document_id"]

    pool = client.portal.call(get_pool)
    atrasos_ms: list[float] = []
    latido_activo = {"v": True}

    async def latido():
        while latido_activo["v"]:
            t0 = time.perf_counter()
            await asyncio.sleep(_LATIDO_S)
            atrasos_ms.append((time.perf_counter() - t0 - _LATIDO_S) * 1000)
    pendientes: dict[int, float] = {}
    duraciones: list[float] = []
    estados_terminales: list[str] = []
    vistos = 0
    with ThreadPoolExecutor(max_workers=usuarios) as executor:
        # El cleanup queda registrado antes de cualquier upload concurrente: si
        # uno falla a mitad, no quedan filas de esta prueba en MariaDB.
        request.addfinalizer(lambda project_id=proyecto.id: client.portal.call(
            sql, "DELETE FROM project_documents WHERE project_id=%s", (project_id,)))
        tarea_latido = client.portal.start_task_soon(latido)  # en el loop de la app
        futuros = [executor.submit(usuario, n) for n in range(usuarios)]
        # Mientras los usuarios suben y chatean: despacho real (POST durable a LAS MANOS),
        # sondeo real (GET externo + persistencia) y consulta de estado por documento.
        deadline = time.perf_counter() + 90
        while time.perf_counter() < deadline:
            while vistos < len(subidos):
                document_id, inicio = subidos[vistos]
                pendientes[document_id] = inicio
                vistos += 1
            hubo_chats = chats_activos["n"] > 0
            ciclos_en_vuelo["n"] += 1
            client.portal.call(despachador.ciclo, pool)
            if hubo_chats or chats_activos["n"] > 0:
                ciclos_en_vuelo["durante_chats"] += 1
            for document_id, inicio in list(pendientes.items()):
                response = client.get(f"/api/proyectos/{proyecto.id}/documentos/{document_id}",
                                      headers=entorno.dueno)
                assert response.status_code == 200, response.text
                estado = response.json()["estado"]
                if estado in {"listo", "parcial", "error", "sin_extractor", "cancelado"}:
                    estados_terminales.append(estado)
                    terminales.append(document_id)
                    duraciones.append((time.perf_counter() - inicio) * 1000)
                    del pendientes[document_id]
            if all(f.done() for f in futuros) and vistos == len(subidos) and not pendientes:
                break
            time.sleep(_SONDEO_S)
        for futuro in futuros:
            futuro.result(timeout=60)  # propaga el fallo de un usuario
        latido_activo["v"] = False
        tarea_latido.result(timeout=10)

    def p95(valores):
        ordenados = sorted(valores)
        return ordenados[math.ceil(0.95 * len(ordenados)) - 1]

    subida_p95, chat_p95 = p95(lat_subida), p95(lat_chat)
    assert len(atrasos_ms) >= 50, f"E3 loop: el latido casi no corrió ({len(atrasos_ms)} despertares)"
    atraso_max = max(atrasos_ms)
    atraso_p99 = sorted(atrasos_ms)[math.ceil(0.99 * len(atrasos_ms)) - 1]
    atrasos_sobre_tope = sum(1 for a in atrasos_ms if a > latido_p99_max_ms)
    print(f"E3_LOAD users={usuarios} turns={turnos} pages={limites.max_paginas} upload_bytes={max_bytes} "
          f"dispatched={len(trabajos)} mutante_s={mutante_s} "
          f"subida_p95_ms={subida_p95:.1f} subida_max_ms={max(lat_subida):.1f} subida_techo_ms={techo_subida_ms} "
          f"latido_n={len(atrasos_ms)} latido_max_ms={atraso_max:.1f} latido_p99_ms={atraso_p99:.1f} "
          f"latido_sobre_tope={atrasos_sobre_tope} latido_p99_tope_ms={latido_p99_max_ms:g} "
          f"chat_p95_ms={chat_p95:.1f} chat_max_ms={max(lat_chat):.1f} chat_tope_ms={tope_chat_ms} "
          f"chats_con_docs_abiertos={sum(chats_con_documentos_abiertos)}/{len(chats_con_documentos_abiertos)} "
          f"ciclos={ciclos_en_vuelo['n']} ciclos_durante_chats={ciclos_en_vuelo['durante_chats']} "
          f"terminal_p95_ms={p95(duraciones) if duraciones else float('nan'):.1f}", flush=True)
    # El escenario es el pedido: chats mientras hay documentos en proceso y despacho/sondeo corriendo.
    assert len(lat_subida) == usuarios and len(lat_chat) == usuarios * turnos
    assert sum(chats_con_documentos_abiertos) >= 0.9 * len(chats_con_documentos_abiertos), \
        "E3: los turnos de chat no ocurrieron mientras había documentos en proceso"
    assert ciclos_en_vuelo["durante_chats"] >= 3, "E3: el despacho y el sondeo no corrieron durante los chats"
    assert atraso_p99 <= latido_p99_max_ms, (
        f"E3 loop: el event loop de la app se bloqueó (p99 de atraso del latido {atraso_p99:.1f} ms, "
        f"máx {atraso_max:.1f} ms; tope de p99 {latido_p99_max_ms:g} ms)")
    assert subida_p95 <= techo_subida_ms, f"E3 subida p95 {subida_p95:.1f} ms supera el techo de {techo_subida_ms} ms"
    assert chat_p95 <= tope_chat_ms, f"E3 chat p95 {chat_p95:.1f} ms supera el tope de {tope_chat_ms} ms"
    assert len(trabajos) == usuarios, f"E3 despachó {len(trabajos)} de {usuarios} PDFs"
    assert len(estados_terminales) == usuarios and all(e == "listo" for e in estados_terminales), \
        f"E3 no completó los {usuarios} PDFs: {estados_terminales}"
    assert not pendientes, f"E3 no llegó a estado terminal: {sorted(pendientes)}"


@pytest.mark.skipif(os.getenv("JAX_CI_NO_DB") == "1", reason="requiere MariaDB desechable de CI")
@pytest.mark.parametrize("caso,esperado", [
    ("otro_tenant", 404), ("sin_membresia", 404), ("viewer", 403),
    ("archivado", 409), ("inexistente", 404),
])
def test_upload_pdf_escaneado_revalida_autorizacion_antes_de_escribir(
        caso, esperado, client, monkeypatch, tmp_path):
    """Las cinco denegaciones deben ocurrir dentro de la entrada nueva del chat."""
    from tests.test_proyectos_documentos_api import Entorno
    from tests.adjuntos_muestras import pdf_con_texto
    from api import proyectos_documentos as api_documentos

    llamadas_papel = []
    original_con_papel = api_documentos._con_papel
    async def con_papel_spy(*args, **kwargs):
        llamadas_papel.append((args[1], kwargs.get("escribe"), kwargs.get("activo")))
        return await original_con_papel(*args, **kwargs)
    monkeypatch.setattr(api_documentos, "_con_papel", con_papel_spy)

    escrituras = []
    original_abrir = api_documentos.almacen.abrir_carpeta_lote
    def abrir_spy(*args, **kwargs):
        escrituras.append("abrir")
        return original_abrir(*args, **kwargs)
    monkeypatch.setattr(api_documentos.almacen, "abrir_carpeta_lote", abrir_spy)
    original_stream = api_documentos.almacen.escribir_streaming
    async def stream_spy(*args, **kwargs):
        escrituras.append("stream")
        return await original_stream(*args, **kwargs)
    monkeypatch.setattr(api_documentos.almacen, "escribir_streaming", stream_spy)

    workspace = tmp_path / "workspace"
    (workspace / "proyectos").mkdir(parents=True)
    os.chmod(workspace / "proyectos", 0o2770)
    adjuntos = tmp_path / "adjuntos"
    adjuntos.mkdir(mode=0o700)
    monkeypatch.setenv("JAX_WORKSPACE_DIR", str(workspace))
    monkeypatch.setenv("JAX_ADJUNTOS_DIR", str(adjuntos))

    entorno = Entorno(client)
    proyecto = entorno.proyecto()
    if caso == "otro_tenant":
        atacante = cabeceras(client, f"attacker-{time.time_ns()}", tenant_id=f"otro-{time.time_ns()}")
        target_id = proyecto.id
    elif caso == "sin_membresia":
        atacante = entorno.usuario(f"sin-membresia-{time.time_ns()}")
        target_id = proyecto.id
    elif caso == "viewer":
        atacante = entorno.miembro(proyecto, f"viewer-{time.time_ns()}", "VIEWER")
        target_id = proyecto.id
    elif caso == "archivado":
        proyecto = entorno.proyecto(archivar=True)
        atacante, target_id = entorno.dueno, proyecto.id
    else:
        atacante, target_id = entorno.dueno, proyecto.id + 9_000_000

    response = client.post("/api/chat/upload", headers=atacante,
        data={"project_id": str(target_id)},
        files={"file": ("escaneo.pdf", pdf_con_texto([""]), "application/pdf")})
    assert response.status_code == esperado, response.text
    assert llamadas_papel == [(target_id, True, True)], llamadas_papel
    assert escrituras == [], escrituras
    assert list((workspace / "proyectos").iterdir()) == [], "una solicitud denegada escribió en el workspace"


@pytest.mark.skipif(os.getenv("JAX_CI_NO_DB") == "1", reason="requiere MariaDB desechable de CI")
def test_freno_antes_del_insert_limpia_archivo_y_devuelve_423(client, monkeypatch, tmp_path):
    """Un freno activado tras escribir no debe dejar archivo huérfano ni ocultarse como 500."""
    from fastapi import HTTPException
    from tests.test_proyectos_documentos_api import Entorno
    from tests.adjuntos_muestras import pdf_con_texto
    from api import proyectos_documentos as api_documentos
    from proyectos_documentos import cupo_de_subidas

    workspace = tmp_path / "workspace"
    (workspace / "proyectos").mkdir(parents=True)
    os.chmod(workspace / "proyectos", 0o2770)
    adjuntos = tmp_path / "adjuntos"
    adjuntos.mkdir(mode=0o700)
    monkeypatch.setenv("JAX_WORKSPACE_DIR", str(workspace))
    monkeypatch.setenv("JAX_ADJUNTOS_DIR", str(adjuntos))
    entorno = Entorno(client)
    proyecto = entorno.proyecto()

    llamadas = 0
    def freno():
        nonlocal llamadas
        llamadas += 1
        if llamadas == 4:
            raise HTTPException(status_code=423, detail="kill_switch_activo")
    monkeypatch.setattr(api_documentos, "exigir_freno_suelto", freno)

    async def insertar_spy(*args, **kwargs):
        pytest.fail("repo.insertar no debe ejecutarse después de activarse el freno")
    monkeypatch.setattr(api_documentos.repo, "insertar", insertar_spy)
    response = client.post("/api/chat/upload", headers=entorno.dueno,
        data={"project_id": str(proyecto.id)},
        files={"file": ("escaneo.pdf", pdf_con_texto([""]), "application/pdf")})

    assert llamadas == 4
    assert response.status_code == 423, response.text
    assert not [path for path in (workspace / "proyectos").rglob("*") if path.is_file()], \
        "el freno dejó un archivo sin fila"
    # El camino de error también suelta el cupo de subidas simultáneas.
    assert cupo_de_subidas.en_uso() == (0, {}), "el 423 dejó el cupo tomado"


@pytest.mark.skipif(os.getenv("JAX_CI_NO_DB") == "1", reason="requiere MariaDB desechable de CI")
def test_cancelar_la_escritura_del_pdf_del_chat_suelta_el_cupo_de_subidas(client, monkeypatch, tmp_path):
    """Una cancelación (cliente que corta) en plena escritura no puede dejar el cupo tomado: con
    N subidas así el area de proyectos y el chat quedarian en 429 `subidas_simultaneas` para siempre."""
    import asyncio
    from tests.test_proyectos_documentos_api import Entorno
    from tests.adjuntos_muestras import pdf_con_texto
    from tests.identidades import _tenant_db_id
    from api import proyectos_documentos as api_documentos
    from proyectos_documentos import cupo_de_subidas

    workspace = tmp_path / "workspace"
    (workspace / "proyectos").mkdir(parents=True)
    os.chmod(workspace / "proyectos", 0o2770)
    monkeypatch.setenv("JAX_WORKSPACE_DIR", str(workspace))
    entorno = Entorno(client)
    proyecto = entorno.proyecto()
    usuario = AuthUser(user_id=str(entorno._id("dueno")), tenant_id=str(_tenant_db_id(entorno.tenant)),
                       role="operator")
    pdf = tmp_path / "escaneo.pdf"
    contenido = pdf_con_texto([""])
    pdf.write_bytes(contenido)
    assert cupo_de_subidas.en_uso() == (0, {})

    async def escenario():
        escribiendo = asyncio.Event()

        async def retenida(*args, **kwargs):
            escribiendo.set()
            await asyncio.sleep(60)   # la cancelación llega mientras se escribe

        monkeypatch.setattr(api_documentos.almacen, "escribir_streaming", retenida)
        tarea = asyncio.create_task(api_documentos.encolar_pdf_desde_chat(
            pdf, nombre="escaneo.pdf", project_id=proyecto.id, user=usuario,
            bytes_=len(contenido), max_bytes=len(contenido) + 1024))
        await asyncio.wait_for(escribiendo.wait(), 20)
        durante = cupo_de_subidas.en_uso()
        tarea.cancel()
        with pytest.raises(asyncio.CancelledError):
            await tarea
        return durante, cupo_de_subidas.en_uso()

    durante, despues = client.portal.call(escenario)
    assert durante[0] == 1, "la escritura debia tener el cupo tomado"
    assert despues == (0, {}), "la cancelación dejó el cupo tomado"
