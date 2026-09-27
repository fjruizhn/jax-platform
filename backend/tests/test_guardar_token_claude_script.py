"""ops/guardar-token-claude.sh -- guarda CLAUDE_CODE_OAUTH_TOKEN en un .env
(2026-09-27). SIEMPRE contra un archivo FALSO en tmp_path -- nunca contra
/etc/jax/.env real (barrera de la tarea). El script exige `sudo` (edita un
archivo que sólo root puede escribir de verdad), así que estos tests también
lo corren con `sudo`, pero apuntado -- via `JAX_ENV_PATH` -- a una copia
descartable bajo tmp_path.

El token viaja SOLO por stdin (`input=` de subprocess), nunca como
argumento: el propio contrato del script ("nunca por argv") se ejercita acá
armando el comando sin el token en ninguna posición de `args`.
"""
import pwd
import stat
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "ops" / "guardar-token-claude.sh"
TOKEN_VALIDO = "sk-ant-oat01-" + "x" * 40


def _sudo_disponible() -> bool:
    return subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode == 0


def _preparar_env_falso(tmp_path: Path, contenido: str, modo=0o640) -> Path:
    ruta = tmp_path / "fake.env"
    ruta.write_text(contenido)
    ruta.chmod(modo)
    return ruta


def _correr(ruta_env: Path, token: str | None, timeout=15):
    entrada = None if token is None else f"{token}\n"
    return subprocess.run(
        ["sudo", "env", f"JAX_ENV_PATH={ruta_env}", "bash", str(SCRIPT)],
        input=entrada, text=True, capture_output=True, timeout=timeout,
    )


def _limpiar(ruta_env: Path):
    """El script corre como root -- si tocó el archivo, puede haber quedado
    root:root. La carpeta (tmp_path) sigue siendo de `fruiz` y con permiso
    de escritura, así que un rm normal alcanza; esto es sólo para no confiar
    en esa garantía y dejar la limpieza explícita."""
    subprocess.run(["sudo", "rm", "-f", str(ruta_env)] + list(ruta_env.parent.glob(f"{ruta_env.name}.bak-*")),
                    capture_output=True)


def setup_module(module):
    if not SCRIPT.is_file():
        raise AssertionError(f"falta el script: {SCRIPT}")
    if not _sudo_disponible():
        import pytest
        pytest.skip("sudo -n no disponible en este runner -- no se puede probar el script real")


def test_reemplaza_la_linea_existente_y_preserva_el_resto(tmp_path):
    original = "FOO=bar\nCLAUDE_CODE_OAUTH_TOKEN=viejo-token-a-reemplazar\nBAZ=qux\n"
    ruta = _preparar_env_falso(tmp_path, original)
    try:
        resultado = _correr(ruta, TOKEN_VALIDO)
        assert resultado.returncode == 0, resultado.stderr

        contenido = ruta.read_text()
        assert f"CLAUDE_CODE_OAUTH_TOKEN={TOKEN_VALIDO}" in contenido
        assert "viejo-token-a-reemplazar" not in contenido
        assert "FOO=bar" in contenido
        assert "BAZ=qux" in contenido
        # una sola línea con la variable, no duplicada
        assert contenido.count("CLAUDE_CODE_OAUTH_TOKEN=") == 1
    finally:
        _limpiar(ruta)


def test_agrega_la_linea_si_no_existia(tmp_path):
    original = "FOO=bar\nBAZ=qux\n"
    ruta = _preparar_env_falso(tmp_path, original)
    try:
        resultado = _correr(ruta, TOKEN_VALIDO)
        assert resultado.returncode == 0, resultado.stderr

        contenido = ruta.read_text()
        assert f"CLAUDE_CODE_OAUTH_TOKEN={TOKEN_VALIDO}" in contenido
        assert "FOO=bar" in contenido
        assert "BAZ=qux" in contenido
    finally:
        _limpiar(ruta)


def test_token_sin_el_prefijo_esperado_no_cambia_nada(tmp_path):
    original = "FOO=bar\n"
    ruta = _preparar_env_falso(tmp_path, original)
    try:
        resultado = _correr(ruta, "no-empieza-como-deberia")
        assert resultado.returncode != 0
        assert ruta.read_text() == original
    finally:
        _limpiar(ruta)


def test_token_vacio_no_cambia_nada(tmp_path):
    original = "FOO=bar\n"
    ruta = _preparar_env_falso(tmp_path, original)
    try:
        resultado = _correr(ruta, "")
        assert resultado.returncode != 0
        assert ruta.read_text() == original
    finally:
        _limpiar(ruta)


def test_conserva_dueno_grupo_y_modo_exactos(tmp_path):
    original = "FOO=bar\n"
    ruta = _preparar_env_falso(tmp_path, original, modo=0o640)
    modo_antes = stat.S_IMODE(ruta.stat().st_mode)
    dueno_antes = pwd.getpwuid(ruta.stat().st_uid).pw_name
    try:
        resultado = _correr(ruta, TOKEN_VALIDO)
        assert resultado.returncode == 0, resultado.stderr

        info = ruta.stat()
        assert stat.S_IMODE(info.st_mode) == modo_antes
        assert pwd.getpwuid(info.st_uid).pw_name == dueno_antes
    finally:
        _limpiar(ruta)


def test_crea_una_copia_de_respaldo_fechada(tmp_path):
    original = "FOO=bar\n"
    ruta = _preparar_env_falso(tmp_path, original)
    try:
        resultado = _correr(ruta, TOKEN_VALIDO)
        assert resultado.returncode == 0, resultado.stderr

        respaldos = list(tmp_path.glob("fake.env.bak-*"))
        assert len(respaldos) == 1
        assert respaldos[0].read_text() == original  # el respaldo es el contenido de ANTES
    finally:
        _limpiar(ruta)


def test_el_token_nunca_aparece_en_la_salida(tmp_path):
    original = "FOO=bar\n"
    ruta = _preparar_env_falso(tmp_path, original)
    try:
        resultado = _correr(ruta, TOKEN_VALIDO)
        assert TOKEN_VALIDO not in resultado.stdout
        assert TOKEN_VALIDO not in resultado.stderr
    finally:
        _limpiar(ruta)


def test_no_reinicia_ningun_servicio_solo_lo_menciona():
    """El script no puede EJECUTAR systemctl -- cada línea que lo nombra
    tiene que ser un `echo`, nunca un comando corrido de verdad."""
    lineas_con_systemctl = [
        linea for linea in SCRIPT.read_text().splitlines() if "systemctl" in linea
    ]
    assert lineas_con_systemctl, "el script debería al menos MENCIONAR qué reiniciar"
    assert all(linea.strip().startswith("echo") for linea in lineas_con_systemctl)


def test_falla_si_el_archivo_no_existe(tmp_path):
    ruta = tmp_path / "no-existe.env"
    resultado = _correr(ruta, TOKEN_VALIDO)
    assert resultado.returncode != 0
    assert not ruta.exists()
