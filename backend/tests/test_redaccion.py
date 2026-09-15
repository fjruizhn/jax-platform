"""Task 6 S1 (2026-09-15): `redactar_secretos` -- funcion pura, sin I/O.

La key de Gemini viaja en `?key=` y httpx.HTTPStatusError mete la URL entera
en str(e): sin redactar, quedaba en facet_health_event.detail, en el log y en
la respuesta del sync de modelos. Todas las keys de este archivo son FALSAS.
"""
import logging

import httpx

import http_client  # noqa: F401 -- instala el filtro en los loggers de httpx
from redaccion import FiltroDeSecretos, redactar_secretos, texto_de_error

KEY = "AIzaFAKE-task6-0123456789abcdef"


def test_el_filtro_redacta_el_log_de_httpx(caplog):
    """httpx loguea `HTTP Request: POST <url>` en INFO en cada pedido: con
    Gemini, la URL trae `?key=`. El filtro va en el logger, no en un handler."""
    assert any(isinstance(f, FiltroDeSecretos) for f in logging.getLogger("httpx").filters)
    caplog.set_level(logging.INFO, logger="httpx")
    url = httpx.URL(f"https://g.example/v1beta/models/m:generateContent?key={KEY}")
    logging.getLogger("httpx").info('HTTP Request: %s %s "%s"', "POST", url, "HTTP/1.1 400")
    assert "HTTP Request: POST" in caplog.text
    assert KEY not in caplog.text
    assert "key=***" in caplog.text


# --- Fix round 1 (2026-09-15, review de 3bed155): formas que la regex de S1
# no cubria, y nombres que NO son secretos.
import pytest  # noqa: E402


@pytest.mark.parametrize("texto, esperado", [
    ('api_key="sk-FAKE-comillas-dobles" x', 'api_key="***" x'),
    ("api_key='sk-FAKE-comillas-simples' x", "api_key='***' x"),
    ('{"api_key": "sk-FAKE-json", "n": 1}', '{"api_key": "***", "n": 1}'),
    ("token = tok-FAKE-espacios fin", "token = *** fin"),
    ("Authorization: Bearer x", "Authorization: Bearer ***"),
    ("Authorization: Bearer tok-FAKE.abc_123/xyz= fin", "Authorization: Bearer *** fin"),
    ("password=hunter2-FAKE&u=1", "password=***&u=1"),
    ("secret=s3cr3t-FAKE fin", "secret=*** fin"),
    ("x-goog-api-key: AIzaFAKE-cabecera-0123456789", "x-goog-api-key: ***"),
])
def test_formas_de_secreto_que_se_redactan(texto, esperado):
    assert redactar_secretos(texto) == esperado


@pytest.mark.parametrize("texto", [
    "monkey=5 y turkey=3",
    "turkey=pavo",
    "Duplicate entry 'x' for key 'PRIMARY'",
])
def test_nombres_que_no_son_secretos_no_se_tocan(texto):
    assert redactar_secretos(texto) == texto


def test_sort_key_y_cache_key_se_redactan_perdida_aceptada():
    """Perdida aceptada (review de 3bed155): un nombre compuesto que termina
    en `_key` se trata como secreto. Mejor tapar de mas que filtrar."""
    assert redactar_secretos("sort_key=nombre&cache_key=abc") == "sort_key=***&cache_key=***"


def test_query_key_se_redacta_y_el_resto_de_la_url_queda():
    texto = f"for url 'https://g.example/v1beta/models?key={KEY}&x=1'"
    out = redactar_secretos(texto)
    assert KEY not in out
    assert "?key=***&x=1" in out


def test_api_key_igual_se_redacta():
    out = redactar_secretos("POST /x?api_key=sk-FAKE-abc123&y=2")
    assert "sk-FAKE-abc123" not in out
    assert "api_key=***&y=2" in out


def test_token_igual_se_redacta():
    out = redactar_secretos("callback?token=tok-FAKE-zzz999 fin")
    assert "tok-FAKE-zzz999" not in out
    assert "token=*** fin" in out


def test_una_key_AIza_suelta_en_el_texto_se_redacta():
    out = redactar_secretos(f"la credencial {KEY} fue rechazada")
    assert KEY not in out
    assert out == "la credencial *** fue rechazada"


def test_los_secretos_conocidos_se_redactan_aunque_no_tengan_forma():
    out = redactar_secretos("Bearer sk-FAKE-sin-forma rechazado",
                            secretos=["sk-FAKE-sin-forma", "", None])
    assert "sk-FAKE-sin-forma" not in out
    assert out == "Bearer *** rechazado"


def test_None_y_vacio_pasan_tal_cual():
    assert redactar_secretos(None) is None
    assert redactar_secretos("") == ""


def test_un_texto_sin_secretos_no_cambia():
    texto = "HTTPStatusError: 502 Bad Gateway en https://h.example/v1/chat?modelo=x&n=1"
    assert redactar_secretos(texto) == texto


def test_texto_de_error_de_un_HTTPStatusError_real_no_trae_la_key():
    """El caso exacto del hallazgo: httpx 0.28 incluye la URL con la query."""
    req = httpx.Request("POST", f"https://generativelanguage.googleapis.com/v1beta/models/m:generateContent?key={KEY}")
    resp = httpx.Response(400, request=req)
    try:
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        assert KEY in str(e)          # el control: sin redactar, la key esta
        out = texto_de_error(e)
    assert KEY not in out
    assert out.startswith("HTTPStatusError: ")
    assert "key=***" in out
