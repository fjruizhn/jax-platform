"""Límites de adjuntos (frente D, 2026-09-16): salen de /etc/jax/.env y, si
faltan o no son enteros > 0, el servicio NO arranca. Sin default silencioso:
un límite ausente leído como "sin límite" es un fail-open."""
import asyncio

import pytest

from adjuntos import limites as mod

_VALORES = {
    "JAX_ADJUNTO_MAX_BYTES": "10485760",
    "JAX_ADJUNTO_MAX_CHARS": "8000",
    "JAX_ADJUNTO_MAX_PAGINAS": "20",
    "JAX_ADJUNTO_MAX_POR_MENSAJE": "1",
}


def _fijar(monkeypatch, **cambios):
    for variable, valor in {**_VALORES, **cambios}.items():
        if valor is None:
            monkeypatch.delenv(variable, raising=False)
        else:
            monkeypatch.setenv(variable, valor)


def test_lee_los_cuatro_limites(monkeypatch):
    _fijar(monkeypatch)
    assert mod.cargar_limites() == mod.LimitesDeAdjuntos(
        max_bytes=10485760, max_chars=8000, max_paginas=20, max_por_mensaje=1)


def test_variable_ausente_no_arranca_y_la_nombra(monkeypatch):
    _fijar(monkeypatch, JAX_ADJUNTO_MAX_CHARS=None)
    with pytest.raises(mod.LimitesDeAdjuntosInvalidos) as e:
        mod.cargar_limites()
    assert "JAX_ADJUNTO_MAX_CHARS" in str(e.value)


@pytest.mark.parametrize("valor", ["0", "-5", "diez"])
def test_valor_no_positivo_o_no_entero_no_arranca(monkeypatch, valor):
    _fijar(monkeypatch, JAX_ADJUNTO_MAX_BYTES=valor)
    with pytest.raises(mod.LimitesDeAdjuntosInvalidos) as e:
        mod.cargar_limites()
    assert "JAX_ADJUNTO_MAX_BYTES" in str(e.value)


def test_lifespan_valida_los_limites_antes_de_abrir_la_base(monkeypatch):
    import main

    llamadas = []

    def sin_limites():
        llamadas.append("limites")
        raise mod.LimitesDeAdjuntosInvalidos("JAX_ADJUNTO_MAX_BYTES=None")

    async def pool_espia():
        llamadas.append("pool")

    monkeypatch.setattr(mod, "cargar_limites", sin_limites)
    monkeypatch.setattr(main, "get_pool", pool_espia)

    async def arrancar():
        async with main.lifespan(main.app):
            pass

    with pytest.raises(mod.LimitesDeAdjuntosInvalidos):
        asyncio.run(arrancar())
    assert llamadas == ["limites"]
