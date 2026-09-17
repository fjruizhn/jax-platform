"""Revisión final del frente E (2026-09-16): jax-platform no arranca sin una
JAX_OLLAMA_URL válida.

Antes `_url_de_ollama()` (api/chat.py) solo exigía que no estuviera vacía y se
llamaba recién en el turno: el servicio arrancaba sano y el primer chat con
jax_local fallaba delante del usuario; el embedding de la memoria (jax.memory)
tragaba el EntornoInvalido en su manejo de errores y la búsqueda seguía sin
memoria. Ahora el lifespan valida la variable ANTES de abrir el pool, con la
misma regla que jax (config_entorno.url_requerida: http(s), con host, sin path,
query ni fragmento), y un valor que no sirve deja al servicio sin arrancar, visible
en el journal.

Puro: el pool se reemplaza por uno que falla si se llega a abrir.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest


def _lifespan_sin_db(monkeypatch):
    import main
    llego_a_la_db = AsyncMock(side_effect=AssertionError("el lifespan abrió el pool sin validar JAX_OLLAMA_URL"))
    monkeypatch.setattr(main, "get_pool", llego_a_la_db)

    async def arrancar():
        async with main.lifespan(main.app):
            pass
    return arrancar, llego_a_la_db


@pytest.mark.parametrize("valor", [None, "", "ollama.test:11434", "ftp://ollama.test",
                                   "http://ollama.test:11434/v1", "http://ollama.test:11434?x=1"])
def test_sin_una_JAX_OLLAMA_URL_valida_la_app_no_arranca(monkeypatch, valor):
    if valor is None:
        monkeypatch.delenv("JAX_OLLAMA_URL", raising=False)
    else:
        monkeypatch.setenv("JAX_OLLAMA_URL", valor)
    arrancar, llego_a_la_db = _lifespan_sin_db(monkeypatch)
    with pytest.raises(RuntimeError) as e:
        asyncio.run(arrancar())
    assert type(e.value).__name__ == "EntornoInvalido", repr(e.value)
    assert "JAX_OLLAMA_URL" in str(e.value)
    llego_a_la_db.assert_not_awaited()


@pytest.mark.parametrize("valor", ["ollama.test:11434", "http://", "http://ollama.test:11434/v1"])
def test_url_de_ollama_aplica_la_regla_de_jax(monkeypatch, valor):
    from api.chat import _url_de_ollama
    monkeypatch.setenv("JAX_OLLAMA_URL", valor)
    with pytest.raises(RuntimeError) as e:
        _url_de_ollama()
    assert "JAX_OLLAMA_URL" in str(e.value)


def test_url_de_ollama_valida_vuelve_sin_barra_final(monkeypatch):
    from api.chat import _url_de_ollama
    monkeypatch.setenv("JAX_OLLAMA_URL", " http://ollama.test:11434/ ")
    assert _url_de_ollama() == "http://ollama.test:11434"


def test_el_conftest_no_apunta_al_ollama_de_produccion():
    import os
    from urllib.parse import urlsplit
    assert urlsplit(os.environ["JAX_OLLAMA_URL"]).hostname.endswith(".invalid")
