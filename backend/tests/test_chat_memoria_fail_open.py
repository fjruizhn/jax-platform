"""Task 3 (2026-09-15), clase (b) del triage de `except` amplios en api/chat.py.

- l.157: `int(JAX_DB_PORT)` vivia DENTRO del `try` de `_memory.connect`, cuyo
  `except Exception` se lo tragaba sin log: un puerto mal formado apagaba la
  memoria en silencio en cada turno -- lo mismo que el guard de config
  ausente (justo arriba) viene a volver ruidoso.
- l.107: `from jax.memory.db import MemoryDB` con `except Exception` mudo: un
  error de sintaxis o de import dentro del repo jax dejaba `MemoryDB = None`
  sin ningun rastro (chat sin memoria y sin shadow validation).

Puros: no piden `client` ni DB.
"""
import asyncio
import builtins
import logging

import pytest

import api.chat as chat_mod


class _MemoryDBFalsa:
    conexiones = []

    def __init__(self):
        self.is_connected = False

    async def connect(self, **kwargs):
        _MemoryDBFalsa.conexiones.append(kwargs)
        return True


@pytest.fixture
def memoria_limpia(monkeypatch):
    _MemoryDBFalsa.conexiones = []
    monkeypatch.setattr(chat_mod, "MemoryDB", _MemoryDBFalsa)
    monkeypatch.setattr(chat_mod, "_memory", None)
    monkeypatch.setattr(chat_mod, "_memory_ready", False)
    monkeypatch.setenv("JAX_DB_HOST", "127.0.0.1")


def test_puerto_mal_formado_apaga_la_memoria_con_log_ruidoso(monkeypatch, caplog, memoria_limpia):
    monkeypatch.setenv("JAX_DB_PORT", "abc")
    with caplog.at_level(logging.ERROR, logger="api.chat"):
        listo = asyncio.run(chat_mod._ensure_memory())
    assert listo is False
    assert _MemoryDBFalsa.conexiones == [], "no se intenta conectar con un puerto invalido"
    errores = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert errores, "un JAX_DB_PORT mal formado apagaba la memoria sin ningun log"
    assert "JAX_DB_PORT" in errores[0].getMessage()


def test_puerto_valido_sigue_conectando(monkeypatch, memoria_limpia):
    monkeypatch.setenv("JAX_DB_PORT", "3308")
    assert asyncio.run(chat_mod._ensure_memory()) is True
    assert _MemoryDBFalsa.conexiones[0]["port"] == 3308


def _import_que_falla(exc):
    real = builtins.__import__

    def falso(name, *args, **kwargs):
        if name == "jax.memory.db":
            raise exc
        return real(name, *args, **kwargs)
    return falso


def test_memorydb_no_importable_se_loguea_y_deja_el_chat_sin_memoria(monkeypatch, caplog):
    monkeypatch.setattr(builtins, "__import__", _import_que_falla(ImportError("sin jax")))
    with caplog.at_level(logging.ERROR, logger="api.chat"):
        clase = chat_mod._importar_memorydb()
    assert clase is None
    errores = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert errores and errores[0].exc_info, "el ImportError tiene que quedar logueado con traceback"


def test_un_error_que_no_es_de_import_no_se_traga(monkeypatch):
    # Un SyntaxError dentro de jax.memory.db es un bug, no "memoria ausente":
    # se propaga y el servicio no arranca en silencio sin memoria.
    monkeypatch.setattr(builtins, "__import__", _import_que_falla(SyntaxError("bug en jax")))
    with pytest.raises(SyntaxError):
        chat_mod._importar_memorydb()
