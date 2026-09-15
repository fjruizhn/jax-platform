import httpx

from redaccion import instalar_filtro_en_loggers_http

# Task 6 S1 (2026-09-15): httpx loguea la URL completa de cada pedido (con
# `?key=` para Gemini). Todo modulo que hace HTTP importa este, asi que el
# filtro queda instalado antes del primer pedido.
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
