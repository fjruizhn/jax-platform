"""Tope de imágenes codificándose a la vez (R16, 2026-09-17; RD3 el mismo día).

Antes cubría el barrido del alfabeto del base64 que mandaba el cliente. Desde
RD3 cubre la lectura de la imagen desde disco y su codificación a base64 en
un hilo: binascii retiene el GIL durante cada tramo, y medido en hall9000
(tic de 1 ms en el loop, 25 imágenes de 10 MB en tramos de 256 KB) el tic se
atrasa p95 60 ms con las 25 a la vez contra 0,35 ms de a una. El tope sale de
JAX_ADJUNTO_IMAGENES_EN_PROCESO, sin default (fail-closed)."""
import asyncio

import pytest

from adjuntos import almacen
from adjuntos import contrato as c
from adjuntos.limites import LimitesDeAdjuntos, LimitesDeAdjuntosInvalidos
from auth.models import AuthUser
from tests.adjuntos_muestras import PNG

_LIM = LimitesDeAdjuntos(max_bytes=10_485_760, max_chars=10, max_paginas=20, max_por_mensaje=1)
DUENIO = AuthUser(user_id="5", tenant_id="1", role="operator")


@pytest.fixture
def imagen(tmp_path, monkeypatch):
    d = tmp_path / "adjuntos"
    monkeypatch.setenv("JAX_ADJUNTOS_DIR", str(d))
    almacen.preparar_directorio()
    temporal = d / almacen.nombre_temporal()
    temporal.write_bytes(PNG + bytes(500_000))
    meta = almacen.guardar_imagen(d, temporal, user=DUENIO, mime="image/png", nombre="f.png",
                                  bytes_=len(PNG) + 500_000, ttl_horas=1)
    return [meta]


@pytest.mark.parametrize("tope", [1, 2])
def test_la_codificacion_de_imagenes_respeta_el_tope(monkeypatch, imagen, tope):
    monkeypatch.setenv("JAX_ADJUNTO_IMAGENES_EN_PROCESO", str(tope))
    activos = 0
    maximo = 0
    real = almacen.leer_imagen_en_base64

    async def espia(*a, **k):
        nonlocal activos, maximo
        activos += 1
        maximo = max(maximo, activos)
        try:
            await asyncio.sleep(0.01)
            return await real(*a, **k)
        finally:
            activos -= 1

    monkeypatch.setattr(almacen, "leer_imagen_en_base64", espia)

    async def correr():
        return await asyncio.gather(*(c.leer_adjuntos(imagen, DUENIO, _LIM) for _ in range(4)))

    resultados = asyncio.run(correr())
    assert maximo == tope
    assert all(r.imagenes[0].bytes == len(PNG) + 500_000 for r in resultados)


def test_sin_tope_configurado_leer_una_imagen_falla_cerrado(monkeypatch, imagen):
    monkeypatch.delenv("JAX_ADJUNTO_IMAGENES_EN_PROCESO", raising=False)
    with pytest.raises(LimitesDeAdjuntosInvalidos):
        asyncio.run(c.leer_adjuntos(imagen, DUENIO, _LIM))
