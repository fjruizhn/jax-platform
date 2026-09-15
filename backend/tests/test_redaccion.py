"""Task 6 S1 (2026-09-15): `redactar_secretos` -- funcion pura, sin I/O.

Historia: la key de Gemini viajaba en `?key=` y httpx.HTTPStatusError mete la
URL entera en str(e): sin redactar, quedaba en facet_health_event.detail, en el
log y en la respuesta del sync de modelos. Desde T6-2 (2026-09-15) la key va en
la cabecera `x-goog-api-key`; estos tests quedan como defensa en profundidad
para cualquier secreto que igual llegue a un texto o a una URL. Todas las keys
de este archivo son FALSAS.
"""
import logging

import httpx

import http_client  # noqa: F401 -- instala el filtro en los loggers de httpx
from redaccion import FiltroDeSecretos, redactar_secretos, texto_de_error

KEY = "AIzaFAKE-task6-0123456789abcdef"


def test_el_filtro_redacta_el_log_de_httpx(caplog):
    """httpx loguea `HTTP Request: POST <url>` en INFO en cada pedido. Con
    Gemini la URL traia `?key=` hasta T6-2 (hoy la key va en la cabecera
    `x-goog-api-key`); el filtro sigue como defensa en profundidad para
    cualquier secreto en una query. Va en el logger, no en un handler."""
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


# --- Fix round 2 (2026-09-15, re-review de 3bed155..e6f2b75) ---------------------
@pytest.mark.parametrize("texto, esperado", [
    ("Authorization: Token abc", "Authorization: Token ***"),
    ("Authorization: Basic dXNlcjpwYXNz fin", "Authorization: Basic *** fin"),
    ('{"authorization": "Bearer abc"}', '{"authorization": "Bearer ***"}'),
    ("GET /x?authorization=abc&n=1", "GET /x?authorization=***&n=1"),
    ("credential=abc fin", "credential=*** fin"),
    ("private_key_id: abc", "private_key_id: ***"),
    # Esquema suelto (sin contexto Authorization): solo si lo que sigue tiene
    # forma de credencial (>= 16 caracteres de token y al menos un digito).
    ("reintento con Bearer eyJhbGciOiJIUzI1NiJ9.payload", "reintento con Bearer ***"),
])
def test_ronda2_formas_de_secreto_que_se_redactan(texto, esperado):
    assert redactar_secretos(texto) == esperado


@pytest.mark.parametrize("texto", [
    "basic idea of it",
    "the bearer of bad news",
    "Duplicate entry 'x' for key 'PRIMARY'",
    "for key: PRIMARY",
])
def test_ronda2_prosa_que_no_se_toca(texto):
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


# --- Fix wave final (2026-09-15): paridad con jax/core/redaccion.py ------------
# Hueco compartido con jax (review de 05c028b, punto 2; portado de
# jax tests/test_redaccion.py): un valor ENTRE COMILLAS despues del esquema
# quedaba entero en claro -- la clase sin comillas no lo tomaba, el esquema
# pasaba a ser el "valor" y el secreto seguia visible.
@pytest.mark.parametrize("texto, esperado", [
    ("Authorization: Bearer 'quoted-FAKE-123' fin", "Authorization: Bearer '***' fin"),
    ('Authorization: Bearer "quoted-FAKE-123" fin', 'Authorization: Bearer "***" fin'),
    ("Authorization: Token 'con espacios FAKE 9'", "Authorization: Token '***'"),
])
def test_valor_entre_comillas_despues_del_esquema(texto, esperado):
    out = redactar_secretos(texto)
    assert "quoted-FAKE" not in out and "FAKE 9" not in out
    assert out == esperado


# --- Fix wave final, ronda 2 (2026-09-15, re-review de 0c72f4e) ----------------
# El grupo 4 (comilla opcional antes del valor) se tragaba la comilla de
# apertura: las alternativas entre comillas nunca aplicaban y un valor entre
# comillas SIN esquema, o con espacios adentro, dejaba el resto en claro
# (`authorization: "secret value"` -> `"*** value"`).
@pytest.mark.parametrize("texto, esperado", [
    ('authorization: "secret value"', 'authorization: "***"'),
    ("authorization: 'secret value'", "authorization: '***'"),
    ('"authorization": "Bearer abc def"', '"authorization": "Bearer ***"'),
    ("'authorization': 'Bearer abc def'", "'authorization': 'Bearer ***'"),
])
def test_valor_entre_comillas_con_espacios_se_tapa_entero(texto, esperado):
    out = redactar_secretos(texto)
    assert "value" not in out and "def" not in out
    assert out == esperado


# --- Fix wave final, ronda 3 (2026-09-15) ---------------------------------------
# Una comilla ESCAPADA dentro del valor (`\"`, `\'`) cerraba el valor antes de
# tiempo y lo que seguia salia en claro:
#   '{"authorization": "Bearer abc\"123 SECRETTAIL"}' -> '"Bearer ***"123 SECRETTAIL"'.
# Y una comilla SIN CERRAR no entraba en ninguna alternativa: el valor entero
# salia en claro. Ahora las formas entre comillas aceptan escapes y, si no
# cierran, tapan hasta el final del texto.
@pytest.mark.parametrize("texto, esperado", [
    ('{"authorization": "Bearer abc\\"123 SECRETTAIL"}', '{"authorization": "Bearer ***"}'),
    ("{'authorization': 'Bearer abc\\'123 SECRETTAIL'}", "{'authorization': 'Bearer ***'}"),
    ('authorization: "abc\\"123 SECRETTAIL" fin', 'authorization: "***" fin'),
    ("authorization: 'abc\\'123 SECRETTAIL' fin", "authorization: '***' fin"),
    ('Authorization: Bearer "abc\\"123 SECRETTAIL" fin', 'Authorization: Bearer "***" fin'),
    ("Authorization: Bearer 'abc\\'123 SECRETTAIL' fin", "Authorization: Bearer '***' fin"),
    ('api_key="abc\\"123 SECRETTAIL" x', 'api_key="***" x'),
    ("api_key='abc\\'123 SECRETTAIL' x", "api_key='***' x"),
])
def test_comilla_escapada_dentro_del_valor_no_corta_la_redaccion(texto, esperado):
    out = redactar_secretos(texto)
    assert "SECRETTAIL" not in out and "123" not in out
    assert out == esperado


@pytest.mark.parametrize("texto, esperado", [
    # Valor que TERMINA en una barra escapada: `\\` es la barra, la comilla
    # que sigue cierra. Control de la forma del escape (una regex que tomara
    # `\"` sin mirar la barra anterior se comeria el resto del texto).
    ('authorization: "abc\\\\" y "visible"', 'authorization: "***" y "visible"'),
    ("api_key='abc\\\\' y 'visible'", "api_key='***' y 'visible'"),
])
def test_valor_que_termina_en_barra_escapada(texto, esperado):
    assert redactar_secretos(texto) == esperado


@pytest.mark.parametrize("texto, esperado", [
    ('authorization: "abc123 SECRETTAIL', 'authorization: "***"'),
    ('"authorization": "Bearer abc SECRETTAIL', '"authorization": "Bearer ***"'),
    ("api_key='abc SECRETTAIL", "api_key='***'"),
])
def test_comilla_sin_cerrar_tapa_hasta_el_final(texto, esperado):
    out = redactar_secretos(texto)
    assert "SECRETTAIL" not in out
    assert out == esperado


_CIEN_KB = 100_000


@pytest.mark.parametrize("texto", [
    'authorization: "' + "a" * _CIEN_KB,                       # sin cerrar
    'authorization: "' + "\\" * _CIEN_KB,                      # barras sin par
    'authorization: "' + '\\"' * (_CIEN_KB // 2),              # solo escapes
    "api_key='" + "\\'" * (_CIEN_KB // 2),
    'authorization: "x ' * (_CIEN_KB // 17),                   # muchas aperturas
    'api_key="' * (_CIEN_KB // 9),
    "a_" * (_CIEN_KB // 2),                                    # prefijos de nombre
    "Bearer " + "a1" * (_CIEN_KB // 2),
], ids=["sin-cerrar", "barras", "escapes", "escapes-simples", "aperturas",
        "aperturas-param", "prefijos", "esquema-suelto"])
def test_100_kb_adversarial_en_menos_de_50_ms(texto):
    import time
    redactar_secretos(texto)                                   # calienta la cache de re
    t0 = time.perf_counter()
    redactar_secretos(texto)
    ms = (time.perf_counter() - t0) * 1000
    assert ms < 50, f"{ms:.1f} ms sobre {len(texto)} caracteres"
