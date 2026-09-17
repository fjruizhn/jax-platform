"""Límite global de profundidad de anidamiento JSON (2026-09-17).

Hallazgo de la auditoría del 2026-09-16: un cuerpo JSON con anidamiento
profundo hacía que la API devolviera 500 (`RecursionError` del decodificador
de la librería estándar escapando hacia uvicorn). Medido contra master
(8d9cb73) antes de tocar nada: POST /api/auth/login con 3000 niveles →
**500**. Con el middleware: **422** con el límite declarado.

El control falla contra el código viejo: sin `limite_json` no hay 422 posible.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import limite_json


def _cuerpo_anidado(niveles: int) -> str:
    return '{"a":' * niveles + "1" + "}" * niveles


def _app_con_limite(limite=None):
    app = FastAPI()

    @app.post("/eco")
    async def eco(dato: dict):
        return {"ok": True}

    app.add_middleware(limite_json.LimiteDeProfundidadJSON, limite=limite)
    return TestClient(app)


# --- El escáner de profundidad -------------------------------------------


def test_profundidad_cuenta_el_anidamiento_real():
    assert not limite_json.profundidad(b"1") == 0
    assert limite_json.profundidad(b'{"a":1}') == 1
    assert limite_json.profundidad(b'{"a":[{"b":1}]}') == 3
    assert limite_json.profundidad(_cuerpo_anidado(50).encode()) == 50


def test_las_llaves_dentro_de_una_cadena_no_cuentan():
    # El defecto clásico de un contador ingenuo: `"{{{{"` no anida nada.
    assert limite_json.profundidad(b'{"a":"{{{{[[[["}') == 1
    # Comilla escapada adentro de la cadena: la cadena no termina ahí.
    assert limite_json.profundidad(rb'{"a":"\"{{{{"}') == 1


def test_un_cuerpo_vacio_no_se_rechaza():
    assert limite_json.profundidad(b"") == 0


# --- El 422 de punta a punta, contra la app real --------------------------


def test_un_cuerpo_profundo_da_422_y_no_500(client):
    """Contra master este mismo pedido da 500 (medido). Ahora es un 422."""
    client._transport.raise_server_exceptions = False
    try:
        r = client.post(
            "/api/auth/login",
            content=_cuerpo_anidado(3000),
            headers={"Content-Type": "application/json"},
        )
    finally:
        client._transport.raise_server_exceptions = True
    assert r.status_code == 422, r.status_code
    detalle = r.json()["detail"]
    assert detalle["code"] == "json_demasiado_profundo"
    # El límite va DECLARADO en la respuesta: quien manda el cuerpo tiene que
    # poder saber contra qué número se lo rechazó.
    assert detalle["limite"] == limite_json.limite_configurado()


def test_un_cuerpo_normal_pasa_de_largo(client):
    """El camino feliz no cambia: el login sigue contestando 401, no 422."""
    r = client.post(
        "/api/auth/login",
        json={"email": "nadie@example.invalid", "password": "x" * 12},
    )
    assert r.status_code != 422, r.text
    assert r.json().get("detail") != {
        "code": "json_demasiado_profundo",
        "limite": limite_json.limite_configurado(),
    }


def test_el_limite_vale_para_toda_la_api_no_para_un_endpoint(client):
    """Global, no un parche: otro router, mismo 422."""
    client._transport.raise_server_exceptions = False
    try:
        r = client.post(
            "/api/auth/refresh",
            content=_cuerpo_anidado(3000),
            headers={"Content-Type": "application/json"},
        )
    finally:
        client._transport.raise_server_exceptions = True
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "json_demasiado_profundo"


def test_un_get_no_se_toca(client):
    # Sin cuerpo que medir: el middleware ni lee el canal.
    assert client.get("/api/health").status_code == 200


# --- Configurable por entorno, sin hardcoding -----------------------------


def test_el_limite_sale_del_entorno(monkeypatch):
    monkeypatch.setenv(limite_json.VARIABLE, "5")
    assert limite_json.limite_configurado() == 5


def test_sin_variable_usa_el_predeterminado_documentado(monkeypatch):
    monkeypatch.delenv(limite_json.VARIABLE, raising=False)
    assert limite_json.limite_configurado() == limite_json.PREDETERMINADO == 64


@pytest.mark.parametrize("valor", ["0", "-3", "muchos", "6.5", " "])
def test_un_limite_inservible_no_cae_a_un_default_silencioso(monkeypatch, valor):
    from config_entorno import EntornoInvalido

    monkeypatch.setenv(limite_json.VARIABLE, valor)
    if not valor.strip():
        # Vacío = no configurado, ese sí es el predeterminado.
        assert limite_json.limite_configurado() == limite_json.PREDETERMINADO
        return
    with pytest.raises(EntornoInvalido):
        limite_json.limite_configurado()


def test_el_limite_configurado_manda_en_el_middleware(monkeypatch):
    monkeypatch.setenv(limite_json.VARIABLE, "3")
    c = _app_con_limite()
    assert c.post("/eco", content=_cuerpo_anidado(3),
                  headers={"Content-Type": "application/json"}).status_code == 200
    r = c.post("/eco", content=_cuerpo_anidado(4),
               headers={"Content-Type": "application/json"})
    assert r.status_code == 422
    assert r.json()["detail"] == {"code": "json_demasiado_profundo", "limite": 3}


# --- Fail-closed ----------------------------------------------------------


def test_si_no_se_puede_medir_la_profundidad_se_rechaza(monkeypatch):
    """Principio IX: sin medición no hay aceptación."""
    def explota(_cuerpo):
        raise MemoryError("no se pudo medir")

    monkeypatch.setattr(limite_json, "profundidad", explota)
    c = _app_con_limite(limite=8)
    # Suficientes aperturas como para que el atajo barato no alcance y el
    # escáner (el que explota) tenga que correr de verdad.
    r = c.post("/eco", content=_cuerpo_anidado(20),
               headers={"Content-Type": "application/json"})
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "json_demasiado_profundo"


def test_el_atajo_barato_no_deja_pasar_nada_profundo():
    """La cota `count` es superior, nunca inferior: si descarta, descarta
    bien. Control directo sobre `excede`, en los dos lados del límite."""
    for niveles in range(1, 40):
        cuerpo = _cuerpo_anidado(niveles).encode()
        for limite in (1, 5, 12, 39, 64):
            assert limite_json.excede(cuerpo, limite) == (niveles > limite), (niveles, limite)


def test_el_atajo_no_se_confunde_con_llaves_dentro_de_cadenas():
    # 200 llaves dentro de una cadena: el `count` no alcanza para descartar,
    # así que corre el escáner completo -- y ese sí sabe que no anidan.
    cuerpo = ('{"a":"' + "{" * 200 + '"}').encode()
    assert limite_json.excede(cuerpo, 64) is False


# --- Ningún content-type es una puerta de atrás ---------------------------


def test_un_text_plain_con_json_adentro_tambien_se_mide():
    """`api/pipelines.py` hace `await request.json()` a mano: eso parsea el
    cuerpo sea cual sea el content-type. Un middleware que solo mirara
    `application/json` dejaría ese 500 vivo."""
    c = _app_con_limite(limite=8)
    for tipo in ("text/plain", "application/octet-stream", "application/hal+json"):
        r = c.post("/eco", content=_cuerpo_anidado(40),
                   headers={"Content-Type": tipo})
        assert r.status_code == 422, tipo
        assert r.json()["detail"]["code"] == "json_demasiado_profundo"


def test_sin_content_type_se_mide_igual():
    c = _app_con_limite(limite=8)
    r = c.request("POST", "/eco", content=_cuerpo_anidado(40))
    assert r.status_code == 422


# --- Lo que NO se toca ----------------------------------------------------


def test_un_multipart_no_se_buffere_ni_se_mide(monkeypatch):
    """Las subidas no se parsean como JSON: el middleware las deja pasar sin
    leer el cuerpo (una subida de 20 MB no se junta en memoria acá)."""
    def explota(_cuerpo):  # si llegara a medirse, el test lo ve
        raise AssertionError("el multipart no debería medirse")

    monkeypatch.setattr(limite_json, "profundidad", explota)
    app = FastAPI()

    @app.post("/sube")
    async def sube():
        return {"ok": True}

    app.add_middleware(limite_json.LimiteDeProfundidadJSON, limite=8)
    c = TestClient(app)
    r = c.post("/sube", files={"f": ("x.txt", b"{" * 5000)})
    assert r.status_code == 200


def test_un_cuerpo_json_partido_en_chunks_se_mide_entero():
    """El cuerpo llega en varios mensajes ASGI: la profundidad es la del
    cuerpo completo, no la de un pedazo."""
    c = _app_con_limite(limite=10)

    def chunks():
        texto = _cuerpo_anidado(40).encode()
        for i in range(0, len(texto), 7):
            yield texto[i:i + 7]

    r = c.post("/eco", content=chunks(),
               headers={"Content-Type": "application/json"})
    assert r.status_code == 422


def test_el_cuerpo_llega_intacto_al_endpoint():
    """El middleware consume el canal `receive` y lo vuelve a ofrecer: el
    endpoint tiene que ver el cuerpo completo, no uno truncado."""
    app = FastAPI()

    @app.post("/eco")
    async def eco(dato: dict):
        return dato

    app.add_middleware(limite_json.LimiteDeProfundidadJSON, limite=64)
    c = TestClient(app)
    carga = {"lista": list(range(500)), "texto": "ñ" * 1000}
    assert c.post("/eco", json=carga).json() == carga
