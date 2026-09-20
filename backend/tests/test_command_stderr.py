# backend/tests/test_command_stderr.py
"""El stderr del comando no se tira a la basura.

`api/command.py` lanzaba `JAX_BIN --task ...` con stdout Y stderr en DEVNULL y
leia el resultado de un archivo. Si el binario moria antes de escribirlo, el
turno salia con `comando_sin_resultado` y NO habia una sola linea diciendo por
que -- ni en el log ni en ningun lado.

Cuarto caso del mismo patron el 2026-09-20 (vigia jax#231, runner del Ejecutor,
y este). Aca `communicate()` SI sirve, a diferencia del vigia y del runner:
nadie mas lee esos flujos, y `communicate()` los drena solo, asi que no hay
riesgo de llenar el pipe.

El stderr va al LOG, no a la respuesta: la respuesta la ve el usuario y una traza
puede traer rutas o secretos. Va redactado igual, por si acaso.
"""
import asyncio
import logging
import os
import sys

import pytest


@pytest.fixture
def _bin_que_falla(tmp_path, monkeypatch):
    """Un JAX_BIN falso que escribe en stderr y NO deja resultado."""
    guion = tmp_path / "jax_falso.py"
    guion.write_text("import sys\n"
                     "sys.stderr.write('ModuleNotFoundError: no existe jax.core\\n')\n"
                     "sys.exit(1)\n")
    lanzador = tmp_path / "jax_falso.sh"
    lanzador.write_text(f"#!/bin/sh\nexec {sys.executable} {guion} \"$@\"\n")
    lanzador.chmod(0o755)
    from api import command
    monkeypatch.setattr(command, "JAX_BIN", lanzador)
    return lanzador


def test_el_stderr_del_comando_queda_en_el_log(_bin_que_falla, tmp_path, caplog):
    from api import command
    mision = tmp_path / "m.json"; mision.write_text("{}")
    with caplog.at_level(logging.ERROR):
        asyncio.run(command._correr_binario(mision, tmp_path / "r.json"))
    assert any("ModuleNotFoundError" in r.getMessage() for r in caplog.records), \
        [r.getMessage() for r in caplog.records]


def test_el_stderr_NO_llega_a_la_respuesta_del_usuario(_bin_que_falla, tmp_path):
    """Una traza puede traer rutas o secretos: va al log, no a lo que ve el usuario."""
    from api import command
    mision = tmp_path / "m.json"; mision.write_text("{}")
    err = asyncio.run(command._correr_binario(mision, tmp_path / "r.json"))
    assert isinstance(err, str)
