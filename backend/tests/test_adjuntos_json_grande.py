"""El JSON de /api/chat con una imagen no se parsea entero en el event loop (R16, 2026-09-17).

Medido en staging con chat_imagen_max a c=25: json.loads del cuerpo (14 MB)
son 8,3 ms por pedido, UNA llamada en C que retiene el GIL (tampoco la
destraba asyncio.to_thread). Con /api/health a 5 VUs en paralelo, su p95
pasaba de 0,3 ms a 70-153 ms. El literal del base64 se separa con búsquedas
de bytes y se parsea solo lo chico; el resultado tiene que ser SIEMPRE el
mismo que json.loads, errores incluidos.
"""
import asyncio
import base64
import json
import random

import pytest

import api.chat as chat_mod
import http_client
from adjuntos import json_grande
from tests.adjuntos_muestras import PNG
from tests.identidades import cabeceras
from tests.test_adjuntos_chat_endpoint import _Grabador, _resuelta

pytestmark = pytest.mark.usefixtures("chat_sin_memoria")


def _cuerpo_chat(b64: str, **extra) -> bytes:
    return json.dumps({"message": "describí", "facet": "jax_local", **extra,
                       "adjuntos": [{"tipo": "imagen", "nombre": "f.png", "mime": "image/png", "base64": b64}]},
                      separators=(",", ":")).encode()


def _igual_a_json_loads(body: bytes):
    try:
        esperado = ("ok", json.loads(body))
    except ValueError as e:
        esperado = ("error", type(e))
    try:
        obtenido = ("ok", asyncio.run(json_grande.cargar(body)))
    except ValueError as e:
        obtenido = ("error", type(e))
    assert obtenido == esperado, body[:200]


def test_da_lo_mismo_que_json_loads(monkeypatch):
    monkeypatch.setattr(json_grande, "_UMBRAL", 64)
    rnd = random.Random(20260917)
    b64 = base64.b64encode(PNG + bytes(200)).decode()
    base = _cuerpo_chat(b64)
    cuerpos = [
        base, base.replace(b'"base64":"', b'"base64": "'), base.replace(b'"base64":"', b'"base64":"\\/'),
        base.replace(b'"describ', b'"\\"base64\\":\\"describ'), _cuerpo_chat(b64 + "\x01"),
        _cuerpo_chat(b64 + "é"), _cuerpo_chat(b64[:-2] + '\\"'), base[:-3], base + b"x",
        base.replace(b'"message"', b'"base64":"' + b"A" * 100 + b'","message"'),
        json.dumps({"base64": "A" * 100}).encode("utf-16"), b'{"base64":"' + b"A" * 100 + b'"}',
        b'["base64":"' + b"A" * 100 + b'"]', b'{"a":"b"base64":"' + b"A" * 100 + b'"}',
        b'{"x":"base64":"' + b"A" * 100 + b'"}', _cuerpo_chat("A" * 100, JAXLIT="x"),
        b'{"base64":"' + b"\xff" * 100 + b'"}', b'{"base64":"' + b"A" * 100,
        b'{"base64":"' + b"A" * 100 + b'","base64":"' + b"B" * 100 + b'"}',
        b'{"base64":"' + b"A" * 100 + bytes([1]) + b'"}', b'{"base64":"' + b"A" * 100 + b'","b":1.5e3,"c":[true,null]}',
        base.replace(b'"f.png"', b'"JAXLITERAL"'),
    ]
    for _ in range(300):
        b = bytearray(base)
        for _ in range(rnd.choice([1, 2, 3])):
            b[rnd.randrange(len(b))] = rnd.choice(b'"\\:,{}[] \x00\x1fAz/=')
        cuerpos.append(bytes(b))
    for body in cuerpos:
        _igual_a_json_loads(body)


def test_chat_con_imagen_grande_no_pasa_el_base64_por_json_loads(client, monkeypatch):
    grabador = _Grabador()
    monkeypatch.setattr(http_client, "_client", grabador)

    async def resolver(_key):
        return _resuelta("jax_local", "ollama", frozenset({"text", "image"}))

    monkeypatch.setattr(chat_mod, "resolve_facet", resolver)
    largos: list[int] = []
    original = json.loads

    def espia(s, *a, **k):
        # Solo el cuerpo del pedido (el grabador también parsea lo que va al proveedor).
        if isinstance(s, (bytes, bytearray)) and s.startswith(b'{"message"'):
            largos.append(len(s))
        return original(s, *a, **k)

    b64 = base64.b64encode(PNG + bytes(300_000)).decode()
    body = _cuerpo_chat(b64, origin="test")
    hdrs = {**cabeceras(client, "test-json-grande"), "Content-Type": "application/json"}
    monkeypatch.setattr(json, "loads", espia)
    r = client.post("/api/chat", content=body, headers=hdrs)
    monkeypatch.setattr(json, "loads", original)
    assert r.status_code == 200, r.text
    (_, enviado), = grabador.pedidos
    assert enviado["messages"][-1]["images"] == [b64]
    assert largos and max(largos) < json_grande._UMBRAL


def test_cuerpo_grande_roto_sigue_siendo_el_422_de_fastapi(client):
    hdrs = {**cabeceras(client, "test-json-grande"), "Content-Type": "application/json"}
    # Un byte de control CRUDO dentro del literal: JSON inválido (dumps lo escaparía).
    roto = _cuerpo_chat("A" * 100_000 + "Z").replace(b'Z"', bytes([1]) + b'"')
    r = client.post("/api/chat", content=roto, headers=hdrs)
    assert r.status_code == 422
    assert r.json()["detail"][0]["type"] == "json_invalid"


def test_cargar_no_usa_hilos_y_cede_el_loop(monkeypatch):
    async def sin_hilos(*a, **k):
        raise AssertionError("cargar no usa asyncio.to_thread")

    monkeypatch.setattr(asyncio, "to_thread", sin_hilos)
    b64 = base64.b64encode(PNG + bytes(3_000_000)).decode()
    body = _cuerpo_chat(b64)

    async def correr():
        ticks = 0
        fin = asyncio.Event()

        async def tic():
            nonlocal ticks
            while not fin.is_set():
                ticks += 1
                await asyncio.sleep(0)

        t = asyncio.create_task(tic())
        await asyncio.sleep(0)
        datos = await json_grande.cargar(body)
        fin.set()
        await t
        return datos, ticks

    datos, ticks = asyncio.run(correr())
    assert datos == json.loads(body)
    assert ticks >= len(b64) // json_grande._TRAMO
