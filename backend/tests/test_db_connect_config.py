"""Test puro de `db_connect_config.db_connect_timeout_seconds()`.

Hallazgo de revisión de la Tarea 1b (tanda A, PR-A, 2026-09-14):
`backend/db_connect_config.py` es un ESPEJO verbatim de
`jax/core/db_connect_config.py` @2dedf0b (ver el propio docstring del
módulo), pero jax no tiene un test puro dedicado a este helper -- solo el
tripwire AST que vigila que el kwarg esté wireado en los sitios de llamada
(`test_aiomysql_connect_timeout_tripwire.py`). Ese tripwire NO ejercita el
contrato del helper en sí (default, valor válido por env, inválido ->
RuntimeError): un `connect_timeout=99999` roto igual pasaría el tripwire.
Este archivo cierra ese hueco del lado de jax-platform.

No toca la DB -- estos tests son puros (solo leen/mutan `os.environ` vía
`monkeypatch`), corren con y sin JAX_CI_NO_DB.
"""
from __future__ import annotations

import pytest

from db_connect_config import db_connect_timeout_seconds


def test_default_es_10_segundos(monkeypatch):
    monkeypatch.delenv("JAX_DB_CONNECT_TIMEOUT_SECONDS", raising=False)
    assert db_connect_timeout_seconds() == 10


def test_valor_valido_por_env_se_respeta(monkeypatch):
    monkeypatch.setenv("JAX_DB_CONNECT_TIMEOUT_SECONDS", "25")
    assert db_connect_timeout_seconds() == 25


def test_valor_no_numerico_lanza_runtime_error(monkeypatch):
    monkeypatch.setenv("JAX_DB_CONNECT_TIMEOUT_SECONDS", "diez")
    with pytest.raises(RuntimeError, match="JAX_DB_CONNECT_TIMEOUT_SECONDS"):
        db_connect_timeout_seconds()


def test_valor_cero_lanza_runtime_error(monkeypatch):
    """P10: cero no es "sin límite" implícito -- tiene que pedirse a
    propósito en otro lado (este helper nunca lo entrega)."""
    monkeypatch.setenv("JAX_DB_CONNECT_TIMEOUT_SECONDS", "0")
    with pytest.raises(RuntimeError, match="JAX_DB_CONNECT_TIMEOUT_SECONDS"):
        db_connect_timeout_seconds()


def test_valor_negativo_lanza_runtime_error(monkeypatch):
    monkeypatch.setenv("JAX_DB_CONNECT_TIMEOUT_SECONDS", "-5")
    with pytest.raises(RuntimeError, match="JAX_DB_CONNECT_TIMEOUT_SECONDS"):
        db_connect_timeout_seconds()
