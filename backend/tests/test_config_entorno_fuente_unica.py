"""Una sola lectura validada del entorno (2026-09-17, unificación A/E).

Tras el rebase de E sobre A convivían `config_de_entorno.py` (frente A,
`ruta_requerida`) y `config_entorno.py` (frente E, espejo verbatim de
jax/core/config_entorno.py, `ruta_absoluta_requerida`): dos reglas para la
misma ruta absoluta obligatoria. Queda `config_entorno` (fuente única espejada
con jax). Este archivo:

1. Política: ni el módulo viejo ni un import suyo en producción o en tests.
2. Equivalencia: cada caso que cubría `ruta_requerida` se exige de
   `ruta_absoluta_requerida`, con la regla más estricta de las dos. Inventario
   medido: las dos quitan espacios, rechazan vacío/solo espacios, rechazan
   relativa (incluida `~/x`, sin expandir) y NO exigen que la ruta exista;
   las dos lanzan RuntimeError (E con la subclase EntornoInvalido) con el
   nombre de la variable en el mensaje.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from config_entorno import EntornoInvalido, ruta_absoluta_requerida

BACKEND = Path(__file__).resolve().parent.parent
_VIEJO = "config_de_entorno"


def _importa_el_modulo_viejo(fuente: str) -> list[int]:
    lineas = []
    for nodo in ast.walk(ast.parse(fuente)):
        if isinstance(nodo, ast.Import):
            nombres = [a.name for a in nodo.names]
        elif isinstance(nodo, ast.ImportFrom):
            nombres = [nodo.module or ""] + [a.name for a in nodo.names]
        elif isinstance(nodo, ast.Call) and isinstance(nodo.func, (ast.Name, ast.Attribute)):
            nombre_func = nodo.func.id if isinstance(nodo.func, ast.Name) else nodo.func.attr
            if nombre_func not in ("import_module", "__import__"):
                continue
            nombres = [a.value for a in nodo.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
        else:
            continue
        if any(n == _VIEJO or n.split(".")[-1] == _VIEJO for n in nombres):
            lineas.append(nodo.lineno)
    return lineas


@pytest.mark.parametrize("fuente", [
    f"from {_VIEJO} import ruta_requerida",
    f"import {_VIEJO}",
    f"import {_VIEJO} as c",
    f"from backend.{_VIEJO} import ruta_requerida",
    f"from . import {_VIEJO}",
    f"import importlib\nimportlib.import_module('{_VIEJO}')",
])
def test_el_escaneo_ve_un_import_del_modulo_viejo(fuente):
    assert _importa_el_modulo_viejo(fuente)


@pytest.mark.parametrize("fuente", [
    "from config_entorno import ruta_absoluta_requerida",
    "import config_entorno",
    "x = 'config_de_entorno'",
])
def test_el_escaneo_no_marca_lo_que_no_importa_el_modulo_viejo(fuente):
    assert _importa_el_modulo_viejo(fuente) == []


def test_no_existe_el_modulo_viejo():
    assert not (BACKEND / f"{_VIEJO}.py").exists()
    assert not (BACKEND / _VIEJO).exists()


def test_ningun_archivo_del_backend_importa_el_modulo_viejo():
    hallazgos = []
    for ruta in BACKEND.rglob("*.py"):
        rel = ruta.relative_to(BACKEND).as_posix()
        if rel.startswith(".venv/"):
            continue
        hallazgos += [f"{rel}:{n}" for n in _importa_el_modulo_viejo(ruta.read_text(encoding="utf-8"))]
    assert hallazgos == []


# --- Equivalencia: los casos de ruta_requerida contra ruta_absoluta_requerida ---

def test_sin_variable_es_error_con_el_nombre(monkeypatch):
    monkeypatch.delenv("JAX_PRUEBA_RUTA", raising=False)
    with pytest.raises(EntornoInvalido, match="JAX_PRUEBA_RUTA"):
        ruta_absoluta_requerida("JAX_PRUEBA_RUTA")


@pytest.mark.parametrize("valor", ["", "   ", "\t\n"])
def test_vacia_o_solo_espacios_es_error_con_el_nombre(monkeypatch, valor):
    monkeypatch.setenv("JAX_PRUEBA_RUTA", valor)
    with pytest.raises(EntornoInvalido, match="JAX_PRUEBA_RUTA"):
        ruta_absoluta_requerida("JAX_PRUEBA_RUTA")


@pytest.mark.parametrize("valor", ["jax/repo", "./jax", "~/jax", "~"])
def test_relativa_es_error_y_no_expande_la_virgulilla(monkeypatch, valor):
    monkeypatch.setenv("JAX_PRUEBA_RUTA", valor)
    with pytest.raises(EntornoInvalido, match="absoluta"):
        ruta_absoluta_requerida("JAX_PRUEBA_RUTA")


def test_el_error_sigue_siendo_runtimeerror():
    # Lo que ruta_requerida lanzaba; un `except RuntimeError` de arriba lo sigue viendo.
    assert issubclass(EntornoInvalido, RuntimeError)


def test_absoluta_se_devuelve_como_path(monkeypatch, tmp_path):
    monkeypatch.setenv("JAX_PRUEBA_RUTA", str(tmp_path))
    assert ruta_absoluta_requerida("JAX_PRUEBA_RUTA") == tmp_path


def test_absoluta_con_espacios_alrededor_se_recorta(monkeypatch, tmp_path):
    monkeypatch.setenv("JAX_PRUEBA_RUTA", f"  {tmp_path}  ")
    assert ruta_absoluta_requerida("JAX_PRUEBA_RUTA") == tmp_path


def test_absoluta_inexistente_se_devuelve_sin_exigir_que_exista(monkeypatch, tmp_path):
    # Ni A ni E validaban existencia: JAX_MISSIONS_DIR puede no existir aún al importar.
    inexistente = tmp_path / "no-existe"
    monkeypatch.setenv("JAX_PRUEBA_RUTA", str(inexistente))
    assert ruta_absoluta_requerida("JAX_PRUEBA_RUTA") == inexistente
