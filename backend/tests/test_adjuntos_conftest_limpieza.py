"""La sesión de pytest borra al terminar el JAX_ADJUNTOS_DIR que creó (Final
fix wave #2, item 7). Antes, conftest.py lo creaba con mkdtemp y nunca lo
borraba: el revisor contó 116 directorios `jax-test-adjuntos-*` en /tmp, con
imágenes y textos de prueba adentro. Solo se borra el de ESTA sesión: los de
otras sesiones (otro worktree corriendo la suite a la vez, o restos viejos) no
son suyos."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent

_TEST_HIJO = '''
import os
from pathlib import Path

def test_escribe_en_el_directorio_de_la_sesion():
    d = Path(os.environ["JAX_ADJUNTOS_DIR"])
    Path(os.environ["RUTA_INFORME"]).write_text(str(d))
    (d / "5").mkdir(mode=0o700)
    (d / "5" / "algo.dato").write_bytes(b"x")
'''


def test_la_sesion_borra_su_directorio_de_adjuntos_y_solo_ese(tmp_path):
    ajeno = Path(tempfile.mkdtemp(prefix="jax-test-adjuntos-"))  # "otra sesión"
    (ajeno / "resto.dato").write_bytes(b"de otra sesion")
    try:
        (tmp_path / "test_hijo.py").write_text(_TEST_HIJO)
        informe = tmp_path / "informe.txt"
        entorno = {**os.environ, "RUTA_INFORME": str(informe)}
        entorno.pop("JAX_ADJUNTOS_DIR", None)
        r = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "tests.conftest",
             "--rootdir", str(tmp_path), str(tmp_path / "test_hijo.py")],
            cwd=BACKEND, env=entorno, capture_output=True, text=True, timeout=120)
        assert r.returncode == 0, r.stdout + r.stderr
        directorio = Path(informe.read_text())
        assert directorio.name.startswith("jax-test-adjuntos-")
        assert directorio != ajeno
        assert not directorio.exists(), f"la sesión dejó {directorio}"
        assert (ajeno / "resto.dato").read_bytes() == b"de otra sesion"
    finally:
        (ajeno / "resto.dato").unlink(missing_ok=True)
        ajeno.rmdir()
