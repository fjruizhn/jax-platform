"""El .env de producción se lee con `sudo -n`, y si no se puede se dice."""
import subprocess

import pytest

from tests import entorno_de_produccion as E


class _Sudo:
    def __init__(self, rc, salida="", error=""):
        self.visto, self._r = None, (rc, salida, error)

    def run(self, argv, **kw):
        self.visto = argv
        rc, salida, error = self._r
        return subprocess.CompletedProcess(argv, rc, stdout=salida, stderr=error)


def _sin_permiso(_ruta):
    raise PermissionError(13, "Permission denied")


def test_lo_lee_con_sudo_no_interactivo():
    sudo = _Sudo(0, "A=1\n# comentario\nB=dos tres\n")
    assert E.cargar("/x/.env", abrir=_sin_permiso, corredor=sudo) == {"A": "1", "B": "dos tres"}
    assert sudo.visto == ["sudo", "-n", "cat", "/x/.env"]


def test_si_no_lo_puede_leer_lo_dice():
    with pytest.raises(RuntimeError, match="sudo -n"):
        E.cargar("/x/.env", abrir=_sin_permiso, corredor=_Sudo(1, error="sudo: a password is required"))


def test_sin_el_archivo_devuelve_vacio():
    def no_esta(_ruta):
        raise FileNotFoundError()

    assert E.cargar("/x/.env", abrir=no_esta, corredor=_Sudo(1, error="cat: No such file")) == {}


def test_el_sembrado_legado_de_llaves_no_revienta_sin_permiso(tmp_path, monkeypatch):
    """`/api/admin/keys` leía el .env en caliente: con el archivo del servicio devolvía 500."""
    from api.admin import keys as K

    cerrado = tmp_path / "env-ajeno"
    cerrado.write_text("OPENAI_API_KEY=sk-no-deberia-leerse\n")
    cerrado.chmod(0o000)
    monkeypatch.setattr(K, "ENV_PATH", str(cerrado))
    assert K._load_env() == {}
