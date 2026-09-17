"""El lector del freno, del lado de la plataforma: sólo "no existe" es SUELTO.

`pausa_presente` (espejo byte a byte de jax/core/interruptor.py) mira con
os.stat. `Path.exists()` no sirve: ante un error de permiso devuelve False en
Python 3.14 (el freno se leería SUELTO con el archivo puesto) y lanza en 3.12.
Estos tests fijan la regla sin depender de la versión: la precondición se
afirma con os.stat, y los errores de E/S se fuerzan sustituyendo os.stat.

Vistos en rojo con `pausa_presente` reemplazado por `Path(ruta).exists()`.

En memoria de Jairo Urbina.
"""
import errno
import logging
import os
from pathlib import Path

import pytest

import interruptor

VARIABLE = "JAX_KILL_SWITCH_PATH"
ES_ROOT = os.geteuid() == 0


@pytest.fixture
def nueva(monkeypatch, tmp_path):
    ruta = tmp_path / "interruptor" / "PAUSE"
    ruta.parent.mkdir()
    monkeypatch.setenv(VARIABLE, str(ruta))
    return ruta


@pytest.fixture
def vieja(monkeypatch, tmp_path):
    ruta = tmp_path / "vieja" / "PAUSE"
    ruta.parent.mkdir()
    monkeypatch.setattr(interruptor, "RUTA_HEREDADA", ruta)
    return ruta


@pytest.mark.skipif(ES_ROOT, reason="root atraviesa cualquier permiso")
def test_directorio_ilegible_cuenta_como_puesto(nueva, vieja):
    nueva.write_text("")
    nueva.parent.chmod(0)
    try:
        with pytest.raises(PermissionError):
            os.stat(nueva)
        assert interruptor.pausa_presente(nueva) is True
        assert interruptor.interruptor_activo() is True
    finally:
        nueva.parent.chmod(0o700)


@pytest.mark.skipif(ES_ROOT, reason="root atraviesa cualquier permiso")
def test_heredada_en_directorio_ilegible_cuenta_como_puesto(nueva, vieja):
    vieja.write_text("")
    vieja.parent.chmod(0)
    try:
        with pytest.raises(PermissionError):
            os.stat(vieja)
        assert interruptor.interruptor_activo() is True
    finally:
        vieja.parent.chmod(0o700)


ERRORES = [
    PermissionError(errno.EACCES, "sin permiso"),
    OSError(errno.EIO, "error de E/S"),
    OSError(errno.ELOOP, "demasiados enlaces"),
]


@pytest.mark.parametrize("error", ERRORES)
def test_cualquier_error_de_stat_que_no_sea_no_existe_cuenta_como_puesto(nueva, vieja, monkeypatch, error):
    def stat_roto(ruta, *args, **kwargs):
        raise error

    monkeypatch.setattr(interruptor.os, "stat", stat_roto)
    assert interruptor.pausa_presente(nueva) is True
    assert interruptor.interruptor_activo() is True


@pytest.mark.parametrize("error", ERRORES[:2])
def test_heredada_con_stat_roto_cuenta_como_puesto(nueva, vieja, monkeypatch, caplog, error):
    stat_real = os.stat

    def stat_selectivo(ruta, *args, **kwargs):
        if Path(ruta) == vieja:
            raise error
        return stat_real(ruta, *args, **kwargs)

    monkeypatch.setattr(interruptor.os, "stat", stat_selectivo)
    monkeypatch.setattr(interruptor, "_heredada_avisada", False)
    caplog.set_level(logging.WARNING)
    assert interruptor.pausa_presente(nueva) is False
    assert interruptor.interruptor_activo() is True
    assert any(r.levelno == logging.WARNING for r in caplog.records)


def test_no_existe_es_lo_unico_suelto(nueva, vieja):
    assert interruptor.pausa_presente(nueva) is False
    assert interruptor.interruptor_activo() is False
