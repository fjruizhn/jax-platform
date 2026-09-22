"""Tests puros (sin backend, sin DB, sin producción) para
`memoria_levantar_entorno.py::escribir_info_json`.

m7 (cierre jax-platform#146, ronda 6, SEGURIDAD): `os.open(path,
O_CREAT|O_TRUNC, 0o600)` NO cambia el modo de un `info.json` que ya existía
con otro modo -- el `mode` de `os.open()` sólo aplica cuando el archivo se
CREA (POSIX open(2)); sobre un archivo preexistente, se ignora por completo.
Reproducido acá con un archivo temporal del scratchpad, nunca con el
lanzador real contra una base de datos.

`python3 -m pytest loadtest/test_memoria_levantar_entorno.py -q` (no forma
parte de los tres pisos de CI de `backend/`, que sólo cuentan
`backend/tests/`).
"""
import json
import os
import stat
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from memoria_levantar_entorno import escribir_info_json  # noqa: E402


def _modo(path: Path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def test_info_json_nuevo_queda_en_600():
    with tempfile.TemporaryDirectory() as tmp:
        destino = Path(tmp) / "info.json"
        escribir_info_json(destino, {"superadmin_password": "x"})
        assert _modo(destino) == 0o600
        assert json.loads(destino.read_text()) == {"superadmin_password": "x"}


def test_info_json_preexistente_en_664_queda_forzado_a_600():
    # El caso real del hallazgo: una corrida anterior (u otro proceso) dejó
    # info.json con el umask de esa sesión (664), y la corrida de ahora
    # tiene que dejarlo en 600 igual, no heredar el modo viejo.
    with tempfile.TemporaryDirectory() as tmp:
        destino = Path(tmp) / "info.json"
        destino.write_text("{}")
        os.chmod(destino, 0o664)
        assert _modo(destino) == 0o664  # confirma el punto de partida

        escribir_info_json(destino, {"superadmin_password": "y"})

        assert _modo(destino) == 0o600
        assert json.loads(destino.read_text()) == {"superadmin_password": "y"}


def test_un_lector_que_ya_tenia_el_archivo_abierto_no_ve_el_contenido_nuevo():
    # MINOR (revision adversarial, ronda 7): `os.fchmod(fd, 0o600)` cambia
    # el MODO del inodo, pero no le quita el descriptor a nadie que YA lo
    # tuviera abierto -- si un lector abrio `info_path` ANTES de esta
    # llamada (mientras el archivo todavia tenia el modo viejo, p.ej. 664)
    # y la escritura reusa el MISMO inodo (como hacia `O_TRUNC` sin
    # `unlink`), ese lector sigue viendo -- ahora -- el contenido NUEVO (con
    # la contraseña), sin que el chmod lo afecte para nada: los permisos de
    # Unix se chequean al ABRIR, no en cada lectura.
    with tempfile.TemporaryDirectory() as tmp:
        destino = Path(tmp) / "info.json"
        destino.write_text('{"superadmin_password": "vieja-no-secreta"}')
        os.chmod(destino, 0o664)

        lector_viejo = open(destino, "r")
        inodo_viejo = os.fstat(lector_viejo.fileno()).st_ino

        escribir_info_json(destino, {"superadmin_password": "nueva-secreta"})

        inodo_nuevo = os.stat(destino).st_ino
        assert inodo_nuevo != inodo_viejo, (
            "escribir_info_json() reusa el inodo -- un lector que ya tenia "
            "el archivo abierto terminaria viendo el contenido nuevo")

        # El descriptor del lector viejo sigue apuntando al inodo VIEJO
        # (ahora huerfano, desconectado del nombre) -- no puede ver el
        # secreto nuevo aunque vuelva a leer desde el principio.
        lector_viejo.seek(0)
        contenido_que_ve_el_lector_viejo = lector_viejo.read()
        lector_viejo.close()
        assert "nueva-secreta" not in contenido_que_ve_el_lector_viejo
