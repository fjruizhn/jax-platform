import sys
from types import SimpleNamespace

import pytest_guard
from pytest_guard import corriendo_bajo_pytest


def test_detecta_contexto_de_pruebas_por_variable_de_entorno(monkeypatch):
    monkeypatch.setattr(pytest_guard, "sys", SimpleNamespace(modules={}))
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "tests/test_pytest_guard.py::test")

    assert corriendo_bajo_pytest()


def test_detecta_contexto_de_pruebas_por_modulo_cargado(monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setitem(sys.modules, "pytest", object())

    assert corriendo_bajo_pytest()


def test_fuera_de_pruebas_devuelve_falso(monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delitem(sys.modules, "pytest", raising=False)

    assert not corriendo_bajo_pytest()
