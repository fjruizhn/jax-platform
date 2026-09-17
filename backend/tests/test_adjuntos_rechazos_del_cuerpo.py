"""/api/chat rechaza y acepta el cuerpo EXACTAMENTE como el parseo normal de FastAPI (R16, 2026-09-17; RD3).

Ruling del principal sobre 3ba4630: fuera el parser propio. Este test fija
el comportamiento del parseo normal (json.loads de Starlette + pydantic con
extra='forbid') en los casos que un segundo parser tocaría: JSON roto,
claves duplicadas (json.loads se queda con la ÚLTIMA), claves desconocidas,
un id que no es string y anidado profundo. Desde RD3 el adjunto es
`{"id": ...}`; el contrato en línea (base64) es una clave desconocida más.
"""
import json

import pytest

import api.chat as chat_mod
import http_client
from adjuntos import almacen
from auth.models import AuthUser
from tests.identidades import cabeceras, uid
from tests.test_adjuntos_chat_endpoint import _Grabador, _resuelta

pytestmark = pytest.mark.usefixtures("chat_sin_memoria")

_ETIQUETA = "test-rechazos-cuerpo"
_OTRO_ID = "B" * almacen.LARGO_ID   # bien formado, no existe: 404


def _ref(id_) -> bytes:
    return json.dumps({"id": id_}, separators=(",", ":")).encode()


def _cuerpo(adjuntos: bytes, extra: bytes = b"") -> bytes:
    return b'{"message":"describ\xc3\xad","facet":"jax_local","origin":"test"' + extra + b',"adjuntos":[' + adjuntos + b"]}"


def _forma(r) -> tuple:
    """Status + forma del error: (type, loc) de cada error de validación de
    FastAPI, o el código estable de los rechazos propios."""
    if r.status_code == 200:
        return (200, None)
    if r.status_code == 500:
        return (500, "error_interno_previo")
    detalle = r.json()["detail"]
    if isinstance(detalle, list):
        return (r.status_code, tuple((e["type"], tuple(e["loc"])) for e in detalle))
    return (r.status_code, detalle["code"])


def _casos(bueno: str) -> dict:
    return {
        # JSON roto
        "byte_de_control_crudo_en_el_id": (
            _cuerpo(_ref(bueno).replace(bueno[-1].encode() + b'"', bytes([1]) + b'"')),
            (422, (("json_invalid", ("body", 0)),))),
        "json_cortado": (_cuerpo(_ref(bueno))[:-2], (422, (("json_invalid", ("body", None)),))),
        "comilla_sin_cerrar": (_cuerpo(_ref(bueno)[:-2] + b"}"), (422, (("json_invalid", ("body", None)),))),
        # Claves duplicadas: gana la última
        "id_duplicado_gana_el_ultimo_que_no_existe": (
            _cuerpo(_ref(bueno)[:-1] + b',"id":"' + _OTRO_ID.encode() + b'"}'), (404, "adjunto_no_encontrado")),
        "id_duplicado_gana_el_ultimo_que_existe": (
            _cuerpo(_ref(_OTRO_ID)[:-1] + b',"id":"' + bueno.encode() + b'"}'), (200, None)),
        "adjuntos_duplicado_gana_el_ultimo": (
            _cuerpo(_ref(bueno))[:-2] + b'],"adjuntos":[' + _ref(_OTRO_ID) + b"]}", (404, "adjunto_no_encontrado")),
        # Claves desconocidas (extra='forbid'), incluido el contrato en línea viejo
        "base64_en_el_adjunto": (
            _cuerpo(_ref(bueno)[:-1] + b',"base64":"QUJD"}'),
            (422, (("extra_forbidden", ("body", "adjuntos", 0, "base64")),))),
        "clave_desconocida_arriba": (
            _cuerpo(_ref(bueno), b',"file_context":"x"'), (422, (("extra_forbidden", ("body", "file_context")),))),
        # Anidado profundo en una clave desconocida. ESTE ES EL PR APARTE que el
        # frente D anunciaba (ruling 2026-09-17): antes, 900 niveles daban 422
        # extra_forbidden y 5.000 daban 500 (RecursionError de FastAPI al
        # serializar el `input` del error). Ahora el limite global de
        # profundidad los corta ANTES de parsear, con el mismo codigo y el
        # limite declarado, en los dos casos. El 500 ya no es alcanzable.
        "anidado_900_en_clave_desconocida": (
            _cuerpo(_ref(bueno), b',"x":' + b"[" * 900 + b"]" * 900),
            (422, "json_demasiado_profundo")),
        "anidado_5000_en_clave_desconocida": (
            _cuerpo(_ref(bueno), b',"x":' + b"[" * 5000 + b"]" * 5000),
            (422, "json_demasiado_profundo")),
        # id que no es string
        "id_numero": (_cuerpo(_ref(12345)), (422, (("string_type", ("body", "adjuntos", 0, "id")),))),
        "id_lista": (_cuerpo(_ref([bueno])), (422, (("string_type", ("body", "adjuntos", 0, "id")),))),
        # Escape JSON válido dentro del id: una letra escrita como \uXXXX es esa letra para json.loads
        "id_con_escape_unicode": (
            _cuerpo(_ref(bueno).replace(b':"' + bueno[:1].encode(), b':"\\u%04x' % ord(bueno[0]), 1)), (200, None)),
    }


_NOMBRES = sorted(_casos("A" * almacen.LARGO_ID))


@pytest.mark.parametrize("caso", _NOMBRES)
def test_el_cuerpo_se_rechaza_como_el_parseo_normal(client, monkeypatch, caso):
    import copy

    from fastapi.testclient import TestClient
    from main import app

    user = AuthUser(user_id=uid(client, _ETIQUETA), tenant_id="1", role="operator")
    bueno = almacen.guardar_texto(almacen.cargar_directorio(), "ventas 42", user=user, origen="texto",
                                  nombre="n.txt", bytes_=9, recortado=False, ttl_horas=1)["id"]
    body, esperado = _casos(bueno)[caso]
    grabador = _Grabador()
    monkeypatch.setattr(http_client, "_client", grabador)
    config = copy.deepcopy(chat_mod._load_config())
    config["personalities"]["jax_local"]["api_url"] = "http://ollama.invalid/api/chat"
    monkeypatch.setattr(chat_mod, "_load_config", lambda: config)

    async def resolver(_key):
        return _resuelta("jax_local", "ollama", frozenset({"text"}))

    monkeypatch.setattr(chat_mod, "resolve_facet", resolver)
    hdrs = {**cabeceras(client, _ETIQUETA), "Content-Type": "application/json"}
    # Sin relanzar excepciones del servidor: el status que ve el cliente real.
    r = TestClient(app, raise_server_exceptions=False).post("/api/chat", content=body, headers=hdrs)
    forma = _forma(r)
    if forma[0] == 422 and isinstance(forma[1], tuple) and forma[1][0][0] == "json_invalid":
        # La posición del error depende del largo exacto: se compara el tipo.
        assert (forma[0], forma[1][0][0]) == (esperado[0], esperado[1][0][0]), r.text
        return
    assert forma == esperado, r.text
    if forma == (200, None):
        (_, enviado), = grabador.al_proveedor
        assert "ventas 42" in enviado["messages"][-1]["content"]
    else:
        assert grabador.pedidos == []
