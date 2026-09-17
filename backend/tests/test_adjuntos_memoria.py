"""El cuerpo de un despacho con imagen no sobrevive al despacho (frente D, R16, 2026-09-17).

Medido en staging: 500 chats seguidos con una imagen de 10 MB dejaron el
proceso en 6,8 GB, 13,4 MB por pedido, sin meseta. Un gc.collect() lo bajó a
136 MB. El cuerpo JSON que va al proveedor (~14 MB) queda colgado de
httpx.Request (`_content` y su ByteStream), y la Request de un ciclo
Response <-> BoundAsyncStream que arma httpx en cada pedido. El GC cíclico de
CPython se dispara por cantidad de objetos, no por bytes: con pocos objetos
por pedido, esos ciclos se acumulan cientos de pedidos antes de una pasada
completa.

El contrato que se prueba: después de mandarlo, el cuerpo con imagen no lo
referencia ningún objeto, ni siquiera basura cíclica pendiente. Por eso los
tests corren con el GC apagado: con el GC apagado la basura cíclica sigue en
gc.get_objects(), que es exactamente el estado que llenaba la memoria.
"""
import asyncio
import base64
import gc
import json

import httpx
import pytest

import api.chat as chat_mod
import http_client
from adjuntos.contrato import ImagenValidada

_GRANDE = 1_000_000
_DATOS = b"\x89PNG\r\n\x1a\n" + bytes(2_000_000)
# RD3: la imagen llega como tramos de bytes (almacen.leer_imagen_en_base64).
# Uno solo de 2,7 MB, para que el test de buffers >= 1 MB lo vea.
_TRAMO = base64.b64encode(_DATOS)
_B64 = _TRAMO.decode()
IMG = (ImagenValidada("f.png", "image/png", (_TRAMO,), len(_DATOS)),)
_CONFIG = {"personalities": {"jax_local": {"api_url": "http://ollama.invalid/api/chat"}}}

_TRANSPORTES = {
    "ollama": (
        lambda: chat_mod._call_ollama("sys", [], "mirá", _CONFIG, "m", imagenes=IMG),
        {"message": {"content": "ok"}},
    ),
    "openai_compat": (
        lambda: chat_mod._call_openai_compat("https://x.invalid/v1", "k", "m", "sys", [], "mirá",
                                             "max_tokens", 100, imagenes=IMG),
        {"choices": [{"message": {"content": "ok"}}], "usage": {}},
    ),
    "gemini": (
        lambda: chat_mod._call_gemini("k", "gemini-x", "sys", [], "mirá", imagenes=IMG),
        {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]},
    ),
}


def _bytes_grandes_alcanzables() -> int:
    """Buffers de bytes >= 1 MB que cuelgan de algún objeto que el GC rastrea
    (vivo o basura cíclica todavía sin juntar)."""
    return sum(
        1
        for o in gc.get_objects()
        for r in gc.get_referents(o)
        if isinstance(r, (bytes, bytearray)) and len(r) >= _GRANDE
    )


def _contiene_b64(obj) -> bool:
    # Sin json.dumps: un test de abajo espía dumps y no tiene que verse a sí mismo.
    if isinstance(obj, str):
        return obj.endswith(_B64)
    if isinstance(obj, dict):
        return any(_contiene_b64(v) for v in obj.values())
    if isinstance(obj, list):
        return any(_contiene_b64(v) for v in obj)
    return False


class _TransporteQueConsume(httpx.AsyncBaseTransport):
    def __init__(self, manejador):
        self._manejador = manejador

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return await self._manejador(request)


def _despachar(transporte: str) -> dict:
    llamar, respuesta = _TRANSPORTES[transporte]
    visto: dict = {}

    async def proveedor(request: httpx.Request) -> httpx.Response:
        # Consume el stream como httpcore, SIN request.aread(): aread (y
        # httpx.MockTransport, que lo llama) guardaría el cuerpo en
        # request._content y sería el propio test el que lo retiene.
        recibido = 0
        partes = []
        async for parte in request.stream:
            recibido += len(parte)
            partes.append(parte)
        cuerpo = json.loads(b"".join(partes))
        del partes
        visto["imagen_intacta"] = _contiene_b64(cuerpo)
        del cuerpo
        visto["recibido"] = recibido
        visto["content_length"] = request.headers.get("content-length")
        visto["transfer_encoding"] = request.headers.get("transfer-encoding")
        return httpx.Response(200, json=respuesta)

    async def correr():
        cliente = httpx.AsyncClient(transport=_TransporteQueConsume(proveedor))
        original = http_client._client
        http_client._client = cliente
        try:
            visto["resultado"] = await llamar()
        finally:
            http_client._client = original
            await cliente.aclose()

    asyncio.run(correr())
    return visto


@pytest.mark.parametrize("transporte", sorted(_TRANSPORTES))
def test_el_cuerpo_con_imagen_no_queda_vivo_despues_del_despacho(transporte):
    gc.collect()
    antes = _bytes_grandes_alcanzables()
    gc.disable()
    try:
        visto = _despachar(transporte)
        despues = _bytes_grandes_alcanzables()
    finally:
        gc.enable()
        gc.collect()
    assert visto["resultado"][0] == "ok"
    assert visto["imagen_intacta"] is True
    assert despues == antes, f"{despues - antes} buffer(s) >= 1 MB siguen alcanzables tras el despacho"


@pytest.mark.parametrize("transporte", sorted(_TRANSPORTES))
def test_el_base64_no_queda_colgado_de_basura_despues_del_despacho(transporte):
    # Medido en staging (R16, bloqueo del loop): una closure recursiva al
    # empalmar el cuerpo dejaba una lista con el base64 en un ciclo; a c=25
    # llegaron a juntarse 456 copias (5,9 GB) antes de que pasara el GC.
    gc.collect()
    antes = len(gc.get_referrers(_TRAMO))
    gc.disable()
    try:
        _despachar(transporte)
        despues = len(gc.get_referrers(_TRAMO))
    finally:
        gc.enable()
        gc.collect()
    assert despues == antes


@pytest.mark.parametrize("transporte", sorted(_TRANSPORTES))
def test_el_cuerpo_de_un_uso_declara_su_largo_y_no_va_en_chunks(transporte):
    visto = _despachar(transporte)
    assert visto["transfer_encoding"] is None
    assert visto["content_length"] == str(visto["recibido"])


def test_el_cuerpo_de_un_uso_no_se_puede_mandar_dos_veces():
    cuerpo = http_client.CuerpoJsonDeUnUso({"a": "ñ"})
    assert cuerpo.cabeceras == {"Content-Type": "application/json",
                                "Content-Length": str(len('{"a":"ñ"}'.encode()))}

    async def leer():
        return [p async for p in cuerpo]

    assert asyncio.run(leer()) == ['{"a":"ñ"}'.encode()]
    with pytest.raises(httpx.StreamConsumed):
        asyncio.run(leer())


# --- Bloqueo del event loop (R16, 2026-09-17) --------------------------------
# json.dumps de un str de 14 MB es UNA llamada en C que retiene el GIL ~20 ms:
# medido, ni en asyncio.to_thread deja correr al loop. El base64 que produjo
# b64encode no necesita escape JSON: sus tramos se empalman como bytes y dumps
# solo ve lo chico (RD3: nunca existe como str).

def _strings_grandes(obj, umbral=100_000):
    if isinstance(obj, str):
        return [len(obj)] if len(obj) >= umbral else []
    if isinstance(obj, dict):
        return [n for v in obj.values() for n in _strings_grandes(v, umbral)]
    if isinstance(obj, (list, tuple)):
        return [n for v in obj for n in _strings_grandes(v, umbral)]
    return []


@pytest.mark.parametrize("transporte", sorted(_TRANSPORTES))
def test_el_cuerpo_con_imagen_no_pasa_la_imagen_por_json_dumps(transporte, monkeypatch):
    vistos: list[int] = []
    original = http_client.json.dumps

    def dumps_espia(obj, *a, **k):
        vistos.extend(_strings_grandes(obj))
        return original(obj, *a, **k)

    monkeypatch.setattr(http_client.json, "dumps", dumps_espia)
    visto = _despachar(transporte)
    assert visto["imagen_intacta"] is True
    assert vistos == []


def test_el_cuerpo_empalmado_es_identico_a_json_dumps():
    datos = bytes(range(256)) * 400
    b64 = base64.b64encode(datos).decode()
    tramos = tuple(base64.b64encode(datos[i:i + 999]) for i in range(0, len(datos), 999))
    assert b"".join(tramos).decode() == b64
    raro = 'comillas " y \\ y ñ y ' + chr(0) + " y " + chr(0x2028) + " y JAXEMPALME"
    def cuerpo(url, imagen):
        return {"model": "m", "messages": [
            {"role": "system", "content": raro},
            {"role": "user", "content": [{"type": "text", "text": "mirá"},
                                         {"type": "image_url", "image_url": {"url": url}}],
             "images": [imagen, imagen]}]}
    esperado = json.dumps(cuerpo(f"data:image/png;base64,{b64}", b64),
                          ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()

    async def leer(c):
        return [p async for p in c]

    de_un_uso = http_client.CuerpoJsonDeUnUso(cuerpo(
        http_client.LiteralJsonCrudo('data:image/png;base64,', tramos),
        http_client.LiteralJsonCrudo("", tramos)))
    partes = asyncio.run(leer(de_un_uso))
    assert b"".join(partes) == esperado
    assert de_un_uso.cabeceras["Content-Length"] == str(len(esperado))
    # Los tramos van tal cual: el mismo objeto, sin copiarse a un buffer grande.
    assert all(any(p is t for p in partes) for t in tramos)
    assert max(len(p) for p in partes) < len(b64)


def test_un_prefijo_con_caracteres_a_escapar_se_escapa():
    cuerpo = {"x": http_client.LiteralJsonCrudo('a"b\\', (b"QUJD",))}

    async def leer(c):
        return b"".join([p async for p in c])

    assert asyncio.run(leer(http_client.CuerpoJsonDeUnUso(cuerpo))) == json.dumps(
        {"x": 'a"b\\QUJD'}, ensure_ascii=False, separators=(",", ":")).encode()


def test_un_literal_crudo_con_json_normal_es_un_error_ruidoso():
    with pytest.raises(TypeError):
        json.dumps({"x": http_client.LiteralJsonCrudo("", (b"QUJD",))})


def test_una_marca_repetida_en_el_cuerpo_falla_cerrado(monkeypatch):
    # Si el texto del cuerpo trajera la marca (un uuid4 adivinado), el
    # empalme sería ambiguo: se niega en vez de mandar un JSON corrupto.
    class _Fijo:
        hex = "0" * 32

    monkeypatch.setattr(http_client.uuid, "uuid4", lambda: _Fijo())
    marca = "JAXEMPALME" + "0" * 32 + "_0_"
    cuerpo = {"texto": marca, "imagen": http_client.LiteralJsonCrudo("", (b"QUJD",))}
    with pytest.raises(ValueError):
        http_client.CuerpoJsonDeUnUso(cuerpo)
