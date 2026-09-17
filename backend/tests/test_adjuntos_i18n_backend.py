"""Cada código de adjuntos/errores.py tiene texto en es.js y en.js, dentro del
objeto erroresMesa (frente A, compartido con el resto de la Mesa; ver
api/errores.js::textoDeErrorDeMesa). Un código nuevo sin traducción se pone
rojo acá, no en la pantalla de un usuario. (Deferido de la Task 8 a la 9,
2026-09-16.)"""
import re
from pathlib import Path

import pytest

from adjuntos.errores import CODIGOS

_I18N = Path(__file__).resolve().parents[2] / "frontend" / "src" / "i18n"


def _bloque_errores_mesa(archivo: str) -> str:
    texto = (_I18N / archivo).read_text(encoding="utf-8")
    m = re.search(r"erroresMesa\s*:\s*\{(.*?)\n  \},", texto, re.S)
    assert m, f"{archivo} no tiene el bloque erroresMesa"
    return m.group(1)


@pytest.mark.parametrize("archivo", ["es.js", "en.js"])
def test_cada_codigo_de_adjunto_esta_traducido(archivo):
    bloque = _bloque_errores_mesa(archivo)
    faltan = [c for c in CODIGOS if not re.search(rf"^\s*{c}:", bloque, re.M)]
    assert faltan == [], f"{archivo} sin texto para {faltan}"
