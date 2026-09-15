import httpx

from redaccion import instalar_filtro_en_loggers_http

# Task 6 S1 (2026-09-15): httpx loguea la URL completa de cada pedido (con
# `?key=` para Gemini). Todo modulo que hace HTTP importa este, asi que el
# filtro queda instalado antes del primer pedido.
instalar_filtro_en_loggers_http()

_client: httpx.AsyncClient | None = None


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
