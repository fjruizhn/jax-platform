"""Tope de imágenes pesadas en proceso a la vez (R16, 2026-09-17).

Medido en staging, chat_imagen_max a c=25 con /api/health a 5 VUs en
paralelo: sin tope, el p95 de health bajo carga fue 17,2 ms (0,4 ms solo);
con tope 1 sobre el parseo y la validación del base64, 2,5 ms. El tope sale
de JAX_ADJUNTO_IMAGENES_EN_PROCESO, sin default (fail-closed)."""
import asyncio
import base64
import json

import pytest

from adjuntos import contrato as c
from adjuntos import json_grande
from adjuntos.limites import LimitesDeAdjuntos
from tests.adjuntos_muestras import PNG

_B64 = base64.b64encode(PNG + bytes(1_500_000)).decode()
_LIM = LimitesDeAdjuntos(max_bytes=10_485_760, max_chars=10, max_paginas=20, max_por_mensaje=1)


def _maximo_simultaneo(monkeypatch, modulo, nombre, corrutinas):
    activos = 0
    maximo = 0
    original = getattr(modulo, nombre)

    async def espia(*a, **k):
        nonlocal activos, maximo
        activos += 1
        maximo = max(maximo, activos)
        try:
            return await original(*a, **k)
        finally:
            activos -= 1

    monkeypatch.setattr(modulo, nombre, espia)

    async def correr():
        return await asyncio.gather(*(f() for f in corrutinas))

    resultados = asyncio.run(correr())
    return maximo, resultados


@pytest.mark.parametrize("tope", [1, 2])
def test_la_validacion_de_imagenes_respeta_el_tope(monkeypatch, tope):
    monkeypatch.setenv("JAX_ADJUNTO_IMAGENES_EN_PROCESO", str(tope))
    img = c.AdjuntoImagen(tipo="imagen", nombre="f.png", mime="image/png", base64=_B64)
    maximo, resultados = _maximo_simultaneo(
        monkeypatch, c, "_alfabeto_estricto_cooperativo",
        [lambda: c.validar_adjuntos([img], _LIM) for _ in range(4)])
    assert maximo == tope
    assert all(r.imagenes[0].bytes == len(PNG) + 1_500_000 for r in resultados)


@pytest.mark.parametrize("tope", [1, 2])
def test_el_parseo_de_cuerpos_grandes_respeta_el_tope(monkeypatch, tope):
    monkeypatch.setenv("JAX_ADJUNTO_IMAGENES_EN_PROCESO", str(tope))
    body = json.dumps({"adjuntos": [{"base64": _B64}]}, separators=(",", ":")).encode()
    maximo, resultados = _maximo_simultaneo(
        monkeypatch, json_grande, "_sin_caracteres_de_control",
        [lambda: json_grande.cargar(body) for _ in range(4)])
    assert maximo == tope
    assert all(r == json.loads(body) for r in resultados)


def test_sin_tope_configurado_el_parseo_grande_falla_cerrado(monkeypatch):
    from adjuntos.limites import LimitesDeAdjuntosInvalidos
    monkeypatch.delenv("JAX_ADJUNTO_IMAGENES_EN_PROCESO", raising=False)
    body = json.dumps({"adjuntos": [{"base64": _B64}]}, separators=(",", ":")).encode()
    with pytest.raises(LimitesDeAdjuntosInvalidos):
        asyncio.run(json_grande.cargar(body))
