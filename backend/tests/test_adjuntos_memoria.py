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
_B64 = base64.b64encode(_DATOS).decode()
IMG = (ImagenValidada("f.png", "image/png", _B64, len(_DATOS)),)
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
        texto = json.dumps(cuerpo)
        visto["imagen_intacta"] = _B64 in texto
        del cuerpo, texto
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
