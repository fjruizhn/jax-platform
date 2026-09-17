import json

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
    """

    def __init__(self, cuerpo: object):
        self._datos: bytes | None = json.dumps(
            cuerpo, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
        self.cabeceras = {"Content-Type": "application/json", "Content-Length": str(len(self._datos))}

    async def __aiter__(self):
        if self._datos is None:
            raise httpx.StreamConsumed()
        datos, self._datos = self._datos, None
        yield datos


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
