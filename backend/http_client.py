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

    def __init__(self, cuerpo: object, literales_seguros: tuple[str, ...] = ()):
        self._datos: bytes | None = _codificar(cuerpo, literales_seguros)
        self.cabeceras = {"Content-Type": "application/json", "Content-Length": str(len(self._datos))}

    async def __aiter__(self):
        if self._datos is None:
            raise httpx.StreamConsumed()
        datos, self._datos = self._datos, None
        yield datos


_MINIMO_PARA_EMPALMAR = 64 * 1024
_MAXIMO_PREFIJO = 256


def _marcar_literales(v, seguros: list[str], marca: str, empalmes: list[str]):
    """Copia del cuerpo con cada literal seguro cambiado por marca+índice.
    Función de módulo y no closure recursiva: una closure que se llama a sí
    misma es un ciclo (función <-> celda) que retenía `empalmes`, con el
    base64 adentro, hasta la próxima pasada del GC (medido: 5,9 GB a c=25)."""
    if isinstance(v, dict):
        return {k: _marcar_literales(x, seguros, marca, empalmes) for k, x in v.items()}
    if isinstance(v, list):
        return [_marcar_literales(x, seguros, marca, empalmes) for x in v]
    if isinstance(v, str) and len(v) >= _MINIMO_PARA_EMPALMAR:
        for literal in seguros:
            sobra = len(v) - len(literal)
            if v is literal or (0 <= sobra <= _MAXIMO_PREFIJO and v.endswith(literal)):
                empalmes.append(literal)
                return v[:sobra] + f"{marca}{len(empalmes) - 1}_"
    return v


def _codificar(cuerpo: object, literales_seguros: tuple[str, ...]) -> bytes:
    """json.dumps(cuerpo, ensure_ascii=False, separators=(",", ":")) en UTF-8,
    sin pasar por dumps los literales grandes que no necesitan escape.

    Bloqueo del event loop (R16, 2026-09-17): dumps de un str de 14 MB es una
    sola llamada en C que retiene el GIL ~20 ms, y medido, ni en
    asyncio.to_thread deja correr al loop. `literales_seguros` son strings que
    el llamador YA verificó que no llevan nada que JSON escape (el base64 de
    una imagen, validado por alfabeto en adjuntos/contrato.py). Cada valor del
    cuerpo que ES uno de ellos, o que termina en uno con un prefijo corto (la
    data URI de OpenAI), se reemplaza por una marca aleatoria, se codifica lo
    chico y la marca se cambia por los bytes del literal (una copia ASCII).
    Si una marca no aparece exactamente una vez, se codifica todo como antes:
    el resultado es siempre el mismo que dumps."""
    def plano() -> bytes:
        return json.dumps(cuerpo, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")

    seguros = [l for l in literales_seguros if len(l) >= _MINIMO_PARA_EMPALMAR]
    if not seguros:
        return plano()
    marca = f"JAXEMPALME{uuid.uuid4().hex}_"
    empalmes: list[str] = []
    chico = json.dumps(_marcar_literales(cuerpo, seguros, marca, empalmes), ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    partes: list[bytes] = []
    resto = chico
    for i, literal in enumerate(empalmes):
        token = f"{marca}{i}_"
        if chico.count(token) != 1:
            return plano()
        antes, resto = resto.split(token, 1)
        partes.append(antes.encode("utf-8"))
        partes.append(literal.encode("ascii"))
    partes.append(resto.encode("utf-8"))
    return b"".join(partes)


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
