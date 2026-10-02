"""Tests puros (sin red ni DB) de la limpieza de `proyectos_e1.py` (E1, T10, ronda 1, I-1):
la limpieza borra SOLO lo sembrado por la corrida cuyo sufijo se le da.

`python3 -m pytest loadtest/test_proyectos_e1.py -q`
"""
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import proyectos_e1 as pe  # noqa: E402

MIO, AJENO = "a1b2c3d4", "0f9e8d7c"


def _like(patron: str, texto: str) -> bool:
    return re.fullmatch(re.escape(patron).replace("%", ".*"), texto) is not None


def _nombres(sufijo: str) -> tuple[list[str], list[str]]:
    proyectos = [f"{pe.PREFIJO_PROYECTO}{sufijo}-{i:04d}" for i in (0, 999)]
    correos = [f"{pe.PREFIJO_PROYECTO}{k}-{sufijo}@{pe.DOMINIO}" for k in ("miembro", "dueno", "u00", "u39")]
    return proyectos, correos


def test_los_patrones_cubren_todo_lo_de_la_corrida():
    pp, pc = pe.patrones_de_limpieza(MIO)
    proyectos, correos = _nombres(MIO)
    assert all(_like(pp, n) for n in proyectos)
    assert all(_like(pc, c) for c in correos)


def test_los_patrones_no_tocan_la_siembra_de_otra_corrida():
    pp, pc = pe.patrones_de_limpieza(MIO)
    proyectos, correos = _nombres(AJENO)
    assert not any(_like(pp, n) for n in proyectos)
    assert not any(_like(pc, c) for c in correos)


@pytest.mark.parametrize("malo", [None, "", "carga-e1", "%", "a1b2c3d", "A1B2C3D4", "a1b2c3d4%", "a1b2c3d4-x"])
def test_sin_sufijo_valido_no_hay_patron(malo):
    with pytest.raises(ValueError):
        pe.patrones_de_limpieza(malo)


def test_limpiar_sin_sufijo_no_se_conecta_ni_borra(monkeypatch):
    def prohibido():
        raise AssertionError("limpiar sin sufijo no debe ni conectarse")
    monkeypatch.setattr(pe, "_conectar", prohibido)
    for malo in (None, ""):
        with pytest.raises(ValueError):
            pe.limpiar(malo)


def test_sembrar_sin_sufijo_valido_no_escribe(monkeypatch):
    monkeypatch.setattr(pe, "_conectar", lambda: (_ for _ in ()).throw(AssertionError("no debe conectarse")))
    with pytest.raises(ValueError):
        pe.sembrar("")
