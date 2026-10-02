"""La versión de Axioma sale del archivo VERSION de la raíz del repo (2026-10-02)."""
from pathlib import Path

import pytest

import app_version

RAIZ = Path(__file__).resolve().parents[2]


def test_app_version_sale_del_archivo():
    import main

    esperado = (RAIZ / "VERSION").read_text(encoding="utf-8").strip()
    assert esperado
    assert main.app.version == esperado


def test_leer_version_rechaza_contenido_invalido(tmp_path):
    f = tmp_path / "VERSION"
    f.write_text("dos punto cinco\n")
    with pytest.raises(ValueError):
        app_version.leer_version(f)


def test_leer_version_sin_archivo_falla(tmp_path):
    with pytest.raises(FileNotFoundError):
        app_version.leer_version(tmp_path / "no-existe")


def test_leer_version_ignora_salto_de_linea(tmp_path):
    f = tmp_path / "VERSION"
    f.write_text("3.0\n")
    assert app_version.leer_version(f) == "3.0"
