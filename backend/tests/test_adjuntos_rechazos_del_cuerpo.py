"""/api/chat rechaza y acepta el cuerpo EXACTAMENTE como el parseo normal de FastAPI (R16, 2026-09-17).

Ruling del principal sobre 3ba4630: fuera el parser propio que saltaba el
literal del base64. Este test fija el comportamiento de c449587 (parseo
normal: json.loads de Starlette + pydantic con extra='forbid') con cuerpos
GRANDES, que son los que un segundo parser tocaría: JSON roto, claves
duplicadas (json.loads se queda con la ÚLTIMA), claves desconocidas y un
base64 que no es string. Un parser distinto en cualquiera de esos casos
cambia el código o la forma del error y rompe acá.

Verificado: pasa igual contra una copia de c449587 (git archive).
"""
import base64
import json

import pytest

import api.chat as chat_mod
import http_client
from tests.adjuntos_muestras import PNG
from tests.identidades import cabeceras
from tests.test_adjuntos_chat_endpoint import _Grabador, _resuelta

pytestmark = pytest.mark.usefixtures("chat_sin_memoria")

_BUENO = base64.b64encode(PNG + bytes(300_000)).decode()   # > 64 KB: lo que tocaba el parser propio
_MALO_GRANDE = "A" * 400_001 + "!"                           # largo múltiplo de 4, alfabeto roto


def _img(b64) -> bytes:
    return json.dumps({"tipo": "imagen", "nombre": "f.png", "mime": "image/png", "base64": b64},
                      separators=(",", ":")).encode()


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


_CASOS = {
    # JSON roto
    "byte_de_control_crudo_en_el_base64": (
        _cuerpo(_img(_BUENO + "Z").replace(b'Z"', bytes([1]) + b'"')),
        (422, (("json_invalid", ("body", 422_000)),))),
    "json_cortado": (_cuerpo(_img(_BUENO))[:-2], (422, (("json_invalid", ("body", None)),))),
    "comilla_sin_cerrar": (_cuerpo(_img(_BUENO)[:-2] + b"}"), (422, (("json_invalid", ("body", None)),))),
    # Claves duplicadas: gana la última
    "base64_duplicado_gana_el_ultimo_invalido": (
        _cuerpo(_img(_BUENO)[:-2] + b'","base64":"no es base64!!"}'), (422, "adjunto_invalido")),
    "base64_duplicado_gana_el_ultimo_valido": (
        _cuerpo(b'{"tipo":"imagen","nombre":"f.png","mime":"image/png","base64":"' + _MALO_GRANDE.encode()
                + b'","base64":"' + _BUENO.encode() + b'"}'), (200, None)),
    "adjuntos_duplicado_gana_el_ultimo": (
        _cuerpo(_img(_BUENO)) [:-2] + b'],"adjuntos":[' + _img(_MALO_GRANDE) + b"]}", (422, "adjunto_invalido")),
    # Claves desconocidas (extra='forbid')
    "clave_desconocida_en_el_adjunto": (
        _cuerpo(_img(_BUENO)[:-1] + b',"image_base64":"x"}'),
        (422, (("extra_forbidden", ("body", "adjuntos", 0, "imagen", "image_base64")),))),
    "clave_desconocida_arriba": (
        _cuerpo(_img(_BUENO), b',"file_context":"x"'), (422, (("extra_forbidden", ("body", "file_context")),))),
    # Anidado profundo en una clave desconocida. Medido en c449587 (parseo
    # normal): hasta ~950 niveles, 422 extra_forbidden; desde ~980, 500
    # (RecursionError de FastAPI al serializar el `input` del error; json.loads
    # sí acepta 5000). El parser propio de 3ba4630 daba 400 desde ~980. El
    # 500 es un defecto PREVIO, reportado al principal: acá se fija "igual que
    # el parseo normal", no se lo bendice como contrato.
    "anidado_900_en_clave_desconocida": (
        _cuerpo(_img(_BUENO), b',"x":' + b"[" * 900 + b"]" * 900),
        (422, (("extra_forbidden", ("body", "x")),))),
    "anidado_5000_en_clave_desconocida": (
        _cuerpo(_img(_BUENO), b',"x":' + b"[" * 5000 + b"]" * 5000), (500, "error_interno_previo")),
    # base64 que no es string
    "base64_numero": (_cuerpo(_img(12345)), (422, (("string_type", ("body", "adjuntos", 0, "imagen", "base64")),))),
    "base64_lista": (_cuerpo(_img([_BUENO])), (422, (("string_type", ("body", "adjuntos", 0, "imagen", "base64")),))),
    # Escapes JSON válidos dentro del base64: \/ es "/" para json.loads
    "base64_con_barra_escapada": (
        _cuerpo(_img(_BUENO)[:1000] + _img(_BUENO)[1000:].replace(b"AAAA", b"AA\\/A", 1)), None),
}


@pytest.mark.parametrize("caso", sorted(_CASOS))
def test_el_cuerpo_se_rechaza_como_el_parseo_normal(client, monkeypatch, caso):
    from fastapi.testclient import TestClient
    from main import app

    body, esperado = _CASOS[caso]
    grabador = _Grabador()
    monkeypatch.setattr(http_client, "_client", grabador)

    async def resolver(_key):
        return _resuelta("jax_local", "ollama", frozenset({"text", "image"}))

    monkeypatch.setattr(chat_mod, "resolve_facet", resolver)
    hdrs = {**cabeceras(client, "test-rechazos-cuerpo"), "Content-Type": "application/json"}
    # Sin relanzar excepciones del servidor: el status que ve el cliente real.
    r = TestClient(app, raise_server_exceptions=False).post("/api/chat", content=body, headers=hdrs)
    forma = _forma(r)
    if esperado is None:
        # Referencia calculada con json.loads, no con el endpoint.
        valor = json.loads(body)["adjuntos"][0]["base64"]
        assert forma == (200, None), r.text
        (_, enviado), = grabador.pedidos
        assert enviado["messages"][-1]["images"] == [valor]
        return
    if forma[0] == 422 and isinstance(forma[1], tuple) and forma[1][0][0] == "json_invalid":
        # La posición del error depende del largo exacto: se compara el tipo.
        assert (forma[0], forma[1][0][0]) == (esperado[0], esperado[1][0][0]), r.text
        return
    assert forma == esperado, r.text
    if forma == (200, None):
        (_, enviado), = grabador.pedidos
        assert enviado["messages"][-1]["images"] == [json.loads(body)["adjuntos"][-1]["base64"]]
