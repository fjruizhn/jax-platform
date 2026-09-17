import json
import uuid

import httpx

from redaccion import instalar_filtro_en_loggers_http

# Task 6 S1 (2026-09-15): httpx loguea la URL completa de cada pedido. La key
# de Gemini ya no va en la URL (cabecera x-goog-api-key, T6-2); el filtro es
# defensa en profundidad para cualquier secreto que un llamador ponga en una
# query. Todo modulo que hace HTTP importa este, asi que el filtro queda
# instalado antes del primer pedido.
instalar_filtro_en_loggers_http()

_client: httpx.AsyncClient | None = None

# T6-2 (2026-09-15): Gemini acepta la key en la cabecera `x-goog-api-key`.
# Antes viajaba en `?key=` y la URL (con el secreto) terminaba en str(e) de
# httpx, en el log de httpx y en facet_health_event. Los 4 lugares que hablan
# con Gemini (chat, sync del catalogo, test de /keys, test de /credentials)
# arman la cabecera aca: un solo nombre, no cuatro literales.
GEMINI_API_KEY_HEADER = "x-goog-api-key"


def cabeceras_gemini(api_key: str) -> dict[str, str]:
    return {GEMINI_API_KEY_HEADER: api_key}


class LiteralJsonCrudo:
    """Un string JSON que se escribe en el cuerpo SIN pasar por json.dumps
    (frente D; RD3, 2026-09-17): `prefijo` (un str normal, se escapa como
    cualquier otro) seguido de `tramos`, pedazos de bytes ASCII que se copian
    tal cual. Lo usa la imagen de un adjunto: los tramos son la salida de
    base64.b64encode (almacen.leer_imagen_en_base64), que por construcción
    solo tiene [A-Za-z0-9+/=] y no necesita escape JSON. Por eso no se
    revisan: quien construye uno con otra cosa rompe el JSON del proveedor.

    Solo vale dentro de un CuerpoJsonDeUnUso; con `json=` httpx no sabe
    serializarlo (TypeError, ruidoso a propósito)."""

    __slots__ = ("prefijo", "tramos")

    def __init__(self, prefijo: str, tramos: tuple[bytes, ...]):
        self.prefijo = prefijo
        self.tramos = tramos


class CuerpoJsonDeUnUso:
    """Cuerpo JSON que httpx manda UNA vez y después suelta (frente D, R16, 2026-09-17).

    Con `json=` httpx guarda el cuerpo codificado en `Request._content` y en su
    ByteStream, y la Request queda colgando de un ciclo Response <->
    BoundAsyncStream que arma en cada pedido. Ese ciclo solo lo libera el GC
    cíclico, que se dispara por cantidad de objetos y no por bytes: medido en
    staging, 500 chats con una imagen de 10 MB dejaron 6,8 GB vivos (13,4 MB
    por pedido, sin meseta) que un gc.collect() bajaba a 136 MB.

    Acá el cuerpo se entrega como stream de un solo uso: el único que lo
    referencia es este objeto, que lo suelta al entregarlo. Mientras se espera
    la respuesta del proveedor (segundos con un LLM real) ya no hay copia
    viva. `Content-Length` explícito: sin él httpx lo mandaría en chunks.
    Una segunda lectura no manda un cuerpo vacío en silencio: StreamConsumed.

    RD3: el cuerpo son PARTES (lo chico codificado por dumps, y los tramos de
    cada LiteralJsonCrudo tal cual), entregadas una por una. Nunca se juntan:
    los tramos de la imagen no se copian a un buffer del tamaño del cuerpo.
    """

    def __init__(self, cuerpo: object):
        self._partes: list[bytes] | None = _codificar(cuerpo)
        largo = sum(len(p) for p in self._partes)
        self.cabeceras = {"Content-Type": "application/json", "Content-Length": str(largo)}

    async def __aiter__(self):
        if self._partes is None:
            raise httpx.StreamConsumed()
        partes, self._partes = self._partes, None
        partes.reverse()
        while partes:
            yield partes.pop()


def _marcar_literales(v, marca: str, crudos: list[LiteralJsonCrudo]):
    """Copia del cuerpo con cada LiteralJsonCrudo cambiado por su prefijo más
    marca+índice. Función de módulo y no closure recursiva: una closure que se
    llama a sí misma es un ciclo (función <-> celda) que retenía la lista con
    el base64 hasta la próxima pasada del GC (medido: 5,9 GB a c=25)."""
    if isinstance(v, LiteralJsonCrudo):
        crudos.append(v)
        return f"{v.prefijo}{marca}{len(crudos) - 1}_"
    if isinstance(v, dict):
        return {k: _marcar_literales(x, marca, crudos) for k, x in v.items()}
    if isinstance(v, list):
        return [_marcar_literales(x, marca, crudos) for x in v]
    return v


def _codificar(cuerpo: object) -> list[bytes]:
    """Las partes de json.dumps(cuerpo, ensure_ascii=False, separators=(",", ":"))
    en UTF-8, con cada LiteralJsonCrudo escrito como sus tramos.

    Bloqueo del event loop (R16, 2026-09-17): dumps de un str de 14 MB es una
    sola llamada en C que retiene el GIL ~20 ms. Acá dumps solo ve lo chico:
    cada literal crudo es una marca aleatoria, y en su lugar van los tramos.
    Una marca que no aparece exactamente una vez (imposible salvo que el
    cuerpo traiga un uuid4 adivinado) es un error: no hay "plano" al que
    volver, porque dumps no sabe escribir un literal crudo."""
    marca = f"JAXEMPALME{uuid.uuid4().hex}_"
    crudos: list[LiteralJsonCrudo] = []
    chico = json.dumps(_marcar_literales(cuerpo, marca, crudos), ensure_ascii=False,
                       separators=(",", ":"), allow_nan=False)
    if not crudos:
        return [chico.encode("utf-8")]
    partes: list[bytes] = []
    inicio = 0
    for i, crudo in enumerate(crudos):
        token = f"{marca}{i}_"
        posicion = chico.find(token, inicio)
        if posicion < 0 or chico.count(token) != 1:
            raise ValueError("cuerpo del proveedor: marca de empalme ausente o repetida")
        partes.append(chico[inicio:posicion].encode("utf-8"))
        partes.extend(t for t in crudo.tramos if t)
        inicio = posicion + len(token)
    partes.append(chico[inicio:].encode("utf-8"))
    return partes


async def get_http_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient()
    return _client


async def close_http_client() -> None:
    global _client
    if _client:
        await _client.aclose()
        _client = None
