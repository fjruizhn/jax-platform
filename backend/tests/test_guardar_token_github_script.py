"""ops/guardar-token-github.sh -- guarda JAX_GITHUB_TOKEN en un .env
(Task 12, 2026-09-28). Clon de test_guardar_token_claude_script.py: SIEMPRE
contra un archivo FALSO en tmp_path -- nunca contra /etc/jax/.env real
(barrera de la tarea). El script exige `sudo` (edita un archivo que sólo root
puede escribir de verdad), así que estos tests también lo corren con `sudo`,
pero apuntado -- via `JAX_ENV_PATH` -- a una copia descartable bajo tmp_path.

El token viaja SOLO por stdin (`input=` de subprocess), nunca como
argumento: el propio contrato del script ("nunca por argv") se ejercita acá
armando el comando sin el token en ninguna posición de `args`.

Lo que consume esta variable es jax/ejecutor/mision_servicio.py
(VARIABLE_TOKEN), corrido como subproceso de jax-platform con
`entorno = dict(os.environ)` (ejecutor/misiones.py::_runner) -- el token
tiene que estar en el entorno del PROPIO jax-platform (EnvironmentFile de
systemd, releído sólo al reiniciar), no en jax-las-manos.
"""
import os
import pwd
import stat
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "ops" / "guardar-token-github.sh"
# DC3 exige grano fino: sólo tokens fine-grained ("github_pat_..."), nunca un
# clásico ("ghp_..."). 60 caracteres de cuerpo -- cómodo por encima del
# mínimo (40) sin acercarse al máximo (255).
TOKEN_VALIDO = "github_pat_" + "A" * 60


def _sudo_disponible() -> bool:
    return subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode == 0


def _preparar_env_falso(tmp_path: Path, contenido: str, modo=0o640) -> Path:
    ruta = tmp_path / "fake.env"
    ruta.write_text(contenido)
    ruta.chmod(modo)
    return ruta


# Sentinela para el default de `confirmacion` en `_correr` -- distinto de
# `None` (que representa a propósito "no escribir ninguna segunda línea",
# para simular EOF antes de la confirmación).
_MISMA_QUE_TOKEN = object()


def _correr(ruta_env: Path, token: str | None, confirmacion=_MISMA_QUE_TOKEN, timeout=15):
    """El script pide el token DOS veces. Por default acá se manda la MISMA
    cadena las dos veces; los tests que ejercitan la confirmación de verdad
    pasan `confirmacion` explícito."""
    if token is None:
        entrada = None
    else:
        conf = token if confirmacion is _MISMA_QUE_TOKEN else confirmacion
        entrada = f"{token}\n" if conf is None else f"{token}\n{conf}\n"
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
    original = "FOO=bar\nJAX_GITHUB_TOKEN=viejo-token-a-reemplazar\nBAZ=qux\n"
    ruta = _preparar_env_falso(tmp_path, original)
    try:
        resultado = _correr(ruta, TOKEN_VALIDO)
        assert resultado.returncode == 0, resultado.stderr

        contenido = ruta.read_text()
        assert f"JAX_GITHUB_TOKEN={TOKEN_VALIDO}" in contenido
        assert "viejo-token-a-reemplazar" not in contenido
        assert "FOO=bar" in contenido
        assert "BAZ=qux" in contenido
        # una sola línea con la variable, no duplicada
        assert contenido.count("JAX_GITHUB_TOKEN=") == 1
    finally:
        _limpiar(ruta)


def test_agrega_la_linea_si_no_existia(tmp_path):
    original = "FOO=bar\nBAZ=qux\n"
    ruta = _preparar_env_falso(tmp_path, original)
    try:
        resultado = _correr(ruta, TOKEN_VALIDO)
        assert resultado.returncode == 0, resultado.stderr

        contenido = ruta.read_text()
        assert f"JAX_GITHUB_TOKEN={TOKEN_VALIDO}" in contenido
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


def test_token_con_prefijo_correcto_pero_alfabeto_invalido_no_cambia_nada(tmp_path):
    """El prefijo NO alcanza -- 'github_pat_' seguido de un caracter fuera de
    [A-Za-z0-9_] (acá un espacio) tiene que rechazarse igual que un token sin
    el prefijo."""
    original = "FOO=bar\n"
    ruta = _preparar_env_falso(tmp_path, original)
    try:
        resultado = _correr(ruta, "github_pat_tiene un espacio adentro")
        assert resultado.returncode != 0
        assert ruta.read_text() == original
    finally:
        _limpiar(ruta)


def test_token_con_prefijo_correcto_pero_demasiado_corto_no_cambia_nada(tmp_path):
    original = "FOO=bar\n"
    ruta = _preparar_env_falso(tmp_path, original)
    try:
        resultado = _correr(ruta, "github_pat_corto")
        assert resultado.returncode != 0
        assert ruta.read_text() == original
    finally:
        _limpiar(ruta)


def _limite_del_script(nombre):
    """Lee LARGO_MINIMO/LARGO_MAXIMO de la fuente del propio script -- así
    estos tests no se desincronizan si el número vuelve a cambiar."""
    import re
    fuente = SCRIPT.read_text()
    return int(re.search(rf'{nombre}=(\d+)', fuente).group(1))


def test_token_valido_en_el_largo_minimo_exacto_se_acepta(tmp_path):
    """Boundary: el mínimo declarado por el script (40) tiene que aceptarse,
    no sólo rechazar por debajo de él."""
    original = "FOO=bar\n"
    ruta = _preparar_env_falso(tmp_path, original)
    try:
        minimo = _limite_del_script("LARGO_MINIMO")
        token = "github_pat_" + ("a" * max(0, minimo - len("github_pat_")))
        assert len(token) >= minimo
        resultado = _correr(ruta, token)
        assert resultado.returncode == 0, resultado.stderr
        assert f"JAX_GITHUB_TOKEN={token}" in ruta.read_text()
    finally:
        _limpiar(ruta)


def test_token_valido_en_el_largo_maximo_exacto_se_acepta(tmp_path):
    """Boundary simétrico: el máximo (255) también tiene que aceptarse, no
    sólo rechazar por encima de él."""
    original = "FOO=bar\n"
    ruta = _preparar_env_falso(tmp_path, original)
    try:
        maximo = _limite_del_script("LARGO_MAXIMO")
        token = "github_pat_" + ("a" * (maximo - len("github_pat_")))
        assert len(token) == maximo
        resultado = _correr(ruta, token)
        assert resultado.returncode == 0, resultado.stderr
        assert f"JAX_GITHUB_TOKEN={token}" in ruta.read_text()
    finally:
        _limpiar(ruta)


def test_token_mas_largo_que_el_maximo_no_cambia_nada(tmp_path):
    original = "FOO=bar\n"
    ruta = _preparar_env_falso(tmp_path, original)
    try:
        maximo = _limite_del_script("LARGO_MAXIMO")
        token = "github_pat_" + ("a" * (maximo - len("github_pat_") + 1))
        assert len(token) == maximo + 1
        resultado = _correr(ruta, token)
        assert resultado.returncode != 0
        assert ruta.read_text() == original
    finally:
        _limpiar(ruta)


def test_conserva_solo_los_dos_respaldos_mas_recientes(tmp_path):
    """Con 3 corridas exitosas, sólo quedan 2 archivos de respaldo -- los
    más viejos se borran, nunca nada que el script no haya creado él mismo
    (el glob es `fake.env.bak-*`, su propio patrón de nombre)."""
    original = "FOO=bar\n"
    ruta = _preparar_env_falso(tmp_path, original)
    try:
        for _ in range(3):
            resultado = _correr(ruta, TOKEN_VALIDO)
            assert resultado.returncode == 0, resultado.stderr
            # el sello de tiempo es por segundo -- sin esto, 3 corridas en el
            # mismo segundo crearían el MISMO nombre de respaldo y "sólo quedan
            # 2 distintos" daría 1 en vez de 2, un falso verde por casualidad.
            import time
            time.sleep(1.1)

        respaldos = sorted(tmp_path.glob("fake.env.bak-*"))
        assert len(respaldos) == 2, respaldos
    finally:
        _limpiar(ruta)


def test_no_borra_respaldos_de_otro_archivo(tmp_path):
    """El patrón de borrado es el propio (`<RUTA_ENV>.bak-*`) -- un archivo
    de respaldo de OTRO archivo en el mismo directorio no se toca."""
    original = "FOO=bar\n"
    ruta = _preparar_env_falso(tmp_path, original)
    ajeno = tmp_path / "otro-archivo.bak-19990101-000000"
    ajeno.write_text("no me borres")
    try:
        for _ in range(3):
            resultado = _correr(ruta, TOKEN_VALIDO)
            assert resultado.returncode == 0, resultado.stderr
            import time
            time.sleep(1.1)
        assert ajeno.is_file()
        assert ajeno.read_text() == "no me borres"
    finally:
        _limpiar(ruta)
        ajeno.unlink(missing_ok=True)


def test_no_borra_respaldo_ajeno_con_sufijo_no_numerico_del_mismo_archivo(tmp_path):
    """El glob de limpieza tiene que ser el patrón EXACTO que el propio
    script genera (fecha-hora numérica: 8 dígitos, guion, 6 dígitos) -- no
    `*` a secas. Un respaldo ajeno con el MISMO archivo base pero un sufijo
    puesto a mano (`antes-rotar`, no una fecha) tiene que sobrevivir."""
    original = "FOO=bar\n"
    ruta = _preparar_env_falso(tmp_path, original)
    ajeno = tmp_path / "fake.env.bak-antes-rotar"
    ajeno.write_text("no me borres -- no tengo el patrón de fecha")
    try:
        for _ in range(3):
            resultado = _correr(ruta, TOKEN_VALIDO)
            assert resultado.returncode == 0, resultado.stderr
            import time
            time.sleep(1.1)
        assert ajeno.is_file()
        assert ajeno.read_text() == "no me borres -- no tengo el patrón de fecha"
    finally:
        _limpiar(ruta)
        ajeno.unlink(missing_ok=True)


def test_no_borra_respaldo_ajeno_con_sufijo_que_ordena_antes_que_las_fechas(tmp_path):
    """La prueba que SÍ distingue el glob viejo del nuevo: un sufijo ajeno
    puramente numérico pero de otra forma (`bak-1`, un solo dígito) ordena
    ANTES que cualquier fecha real (`bak-2026...`, comparación de string:
    '1' < '2') -- con el glob viejo (`*` a secas) esto SÍ caía entre los
    "más viejos" y se borraba. El glob estricto (8 dígitos-guion-6 dígitos)
    nunca lo toma como candidato, sin importar dónde ordene."""
    original = "FOO=bar\n"
    ruta = _preparar_env_falso(tmp_path, original)
    ajeno = tmp_path / "fake.env.bak-1"
    ajeno.write_text("no me borres -- ordeno antes que cualquier fecha real")
    try:
        for _ in range(3):
            resultado = _correr(ruta, TOKEN_VALIDO)
            assert resultado.returncode == 0, resultado.stderr
            import time
            time.sleep(1.1)
        assert ajeno.is_file()
        assert ajeno.read_text() == "no me borres -- ordeno antes que cualquier fecha real"
    finally:
        _limpiar(ruta)
        ajeno.unlink(missing_ok=True)


def test_conserva_exactamente_el_respaldo_recien_creado_mas_uno_viejo(tmp_path):
    """El respaldo recién creado se excluye EXPLÍCITAMENTE del cálculo de
    "qué borrar" -- no depende sólo de que el orden alfabético lo deje
    último. Con 3 corridas, sobreviven el más nuevo y el segundo más nuevo;
    el primero se borra."""
    original = "FOO=bar\n"
    ruta = _preparar_env_falso(tmp_path, original)
    try:
        import time
        for _ in range(3):
            resultado = _correr(ruta, TOKEN_VALIDO)
            assert resultado.returncode == 0, resultado.stderr
            time.sleep(1.1)

        respaldos = sorted(tmp_path.glob("fake.env.bak-*"))
        assert len(respaldos) == 2
        # el más reciente de los 2 sigue siendo legible y con el contenido
        # de ANTES de la última corrida (el respaldo se toma antes de escribir)
        assert respaldos[-1].is_file()
    finally:
        _limpiar(ruta)


def test_mensaje_final_menciona_jax_platform(tmp_path):
    """jax-platform corre el runner del Ejecutor (jax/ejecutor/mision_servicio.py)
    con `entorno = dict(os.environ)` -- el token tiene que estar en el
    entorno del PROPIO jax-platform (EnvironmentFile de systemd, releído
    sólo al reiniciar)."""
    original = "FOO=bar\n"
    ruta = _preparar_env_falso(tmp_path, original)
    try:
        resultado = _correr(ruta, TOKEN_VALIDO)
        assert resultado.returncode == 0, resultado.stderr
        assert "jax-platform" in resultado.stderr
    finally:
        _limpiar(ruta)


def test_token_partido_en_dos_lineas_aborta_sin_tocar_el_env(tmp_path):
    """Un token pegado partido en dos líneas por el terminal -- `read -rs`
    sólo toma la primera, y la segunda queda esperando en la entrada. Con el
    protocolo de dos lecturas, la mitad sobrante se termina leyendo como si
    fuera la CONFIRMACIÓN, y no coincide con el token real -- así que aborta
    sin tocar el .env, por "no coinciden".

    Se ejercita con un pty de verdad (un pipe no tiene la semántica de "cola
    de entrada compartida" que sí tiene una terminal)."""
    import pty

    original = "FOO=bar\n"
    ruta = _preparar_env_falso(tmp_path, original)
    master_fd = None
    slave_fd = None
    try:
        master_fd, slave_fd = pty.openpty()
        proc = subprocess.Popen(
            ["sudo", "env", f"JAX_ENV_PATH={ruta}", "bash", str(SCRIPT)],
            stdin=slave_fd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        os.write(master_fd, f"{TOKEN_VALIDO}\nresto-que-no-deberia-quedar-sin-leer\n".encode())
        proc.wait(timeout=15)
        salida_err = proc.stderr.read().decode(errors="replace")
        proc.stdout.close()
        proc.stderr.close()

        assert proc.returncode != 0
        assert ruta.read_text() == original  # NO se tocó
        assert "no coinciden" in salida_err.lower()  # el mensaje explica qué pasó
    finally:
        if master_fd is not None:
            os.close(master_fd)
        if slave_fd is not None:
            os.close(slave_fd)
        _limpiar(ruta)


def test_vacia_lo_que_sobra_en_stdin_tras_leer_el_token(tmp_path):
    """El resto se DRENA de la entrada aunque el resultado sea abortar (test
    de arriba) -- si no se drenara, esa segunda línea seguiría esperando en
    la cola del terminal para que el shell que lanzó
    `sudo bash guardar-token-github.sh` la lea como si fuera SU propio
    siguiente comando."""
    import pty
    import select
    import termios

    original = "FOO=bar\n"
    ruta = _preparar_env_falso(tmp_path, original)
    master_fd = None
    slave_fd = None
    try:
        master_fd, slave_fd = pty.openpty()
        attrs = termios.tcgetattr(slave_fd)
        attrs[3] = attrs[3] & ~termios.ECHO
        termios.tcsetattr(slave_fd, termios.TCSANOW, attrs)

        proc = subprocess.Popen(
            ["sudo", "env", f"JAX_ENV_PATH={ruta}", "bash", str(SCRIPT)],
            stdin=slave_fd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        os.write(master_fd, f"{TOKEN_VALIDO}\nresto-que-no-deberia-quedar-sin-leer\n".encode())
        proc.wait(timeout=15)
        proc.stdout.close()
        proc.stderr.close()

        assert proc.returncode != 0  # abortó -- pero drenó igual

        listo, _, _ = select.select([slave_fd], [], [], 0.5)
        sobrante = os.read(slave_fd, 4096) if listo else b""
        assert b"resto-que-no-deberia-quedar-sin-leer" not in sobrante
    finally:
        if master_fd is not None:
            os.close(master_fd)
        if slave_fd is not None:
            os.close(slave_fd)
        _limpiar(ruta)


def test_confirmacion_igual_al_token_guarda_normalmente(tmp_path):
    """Coinciden -> OK. Es el caso "feliz" de todos los tests de arriba
    (que usan el default de `_correr`, mismo valor las dos veces) -- este
    lo deja explícito."""
    original = "FOO=bar\n"
    ruta = _preparar_env_falso(tmp_path, original)
    try:
        resultado = _correr(ruta, TOKEN_VALIDO, confirmacion=TOKEN_VALIDO)
        assert resultado.returncode == 0, resultado.stderr
        assert f"JAX_GITHUB_TOKEN={TOKEN_VALIDO}" in ruta.read_text()
    finally:
        _limpiar(ruta)


def test_confirmacion_distinta_no_coincide_y_no_cambia_nada(tmp_path):
    """No coinciden -> aborta con .env intacto."""
    original = "FOO=bar\n"
    ruta = _preparar_env_falso(tmp_path, original)
    otro_token_valido = "github_pat_" + "B" * 60
    try:
        resultado = _correr(ruta, TOKEN_VALIDO, confirmacion=otro_token_valido)
        assert resultado.returncode != 0
        assert ruta.read_text() == original
        assert "no coinciden" in resultado.stderr.lower()
    finally:
        _limpiar(ruta)


def test_confirmacion_vacia_por_eof_no_coincide_y_no_cambia_nada(tmp_path):
    """Sin nada más en la entrada (EOF antes de la segunda lectura),
    `read -rs` devuelve vacío -- eso tampoco coincide con un token real, así
    que aborta igual que cualquier otro mismatch, con el .env intacto."""
    original = "FOO=bar\n"
    ruta = _preparar_env_falso(tmp_path, original)
    try:
        resultado = _correr(ruta, TOKEN_VALIDO, confirmacion=None)
        assert resultado.returncode != 0
        assert ruta.read_text() == original
    finally:
        _limpiar(ruta)


def test_el_token_nunca_aparece_en_la_salida_ni_siquiera_al_no_coincidir(tmp_path):
    """El primer token (el que sí es válido) tampoco debería filtrarse a la
    salida cuando el que falla es el segundo pegado."""
    original = "FOO=bar\n"
    ruta = _preparar_env_falso(tmp_path, original)
    otro_token_valido = "github_pat_" + "C" * 60
    try:
        resultado = _correr(ruta, TOKEN_VALIDO, confirmacion=otro_token_valido)
        assert TOKEN_VALIDO not in resultado.stdout
        assert TOKEN_VALIDO not in resultado.stderr
        assert otro_token_valido not in resultado.stdout
        assert otro_token_valido not in resultado.stderr
    finally:
        _limpiar(ruta)


def test_confirmacion_partida_en_mas_de_dos_lineas_aborta_sin_tocar_el_env(tmp_path):
    """Pegado partido -> aborta. A diferencia del test de la mitad del
    PRIMER pegado (más arriba, que ahora aborta por "no coinciden"), acá el
    sobrante aparece DESPUÉS de la confirmación -- eso sigue siendo detectado
    por el chequeo explícito de `hay_stdin_sobrante`, con su propio mensaje
    ("partido en más de dos líneas")."""
    import pty

    original = "FOO=bar\n"
    ruta = _preparar_env_falso(tmp_path, original)
    master_fd = None
    slave_fd = None
    try:
        master_fd, slave_fd = pty.openpty()
        proc = subprocess.Popen(
            ["sudo", "env", f"JAX_ENV_PATH={ruta}", "bash", str(SCRIPT)],
            stdin=slave_fd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        os.write(
            master_fd,
            f"{TOKEN_VALIDO}\n{TOKEN_VALIDO}\nresto-que-no-deberia-quedar-sin-leer\n".encode(),
        )
        proc.wait(timeout=15)
        salida_err = proc.stderr.read().decode(errors="replace")
        proc.stdout.close()
        proc.stderr.close()

        assert proc.returncode != 0
        assert ruta.read_text() == original
        assert "partid" in salida_err.lower()
    finally:
        if master_fd is not None:
            os.close(master_fd)
        if slave_fd is not None:
            os.close(slave_fd)
        _limpiar(ruta)


def test_una_linea_vacia_de_mas_no_cuenta_como_partido(tmp_path):
    """Un Enter de más al pegar dos veces (o cualquier línea vacía
    sobrante) no es un pegado partido -- sólo contenido no vacío cuenta
    como "sobró algo". Se ejercita con un pty de verdad, mismo motivo que
    los tests de arriba."""
    import pty

    original = "FOO=bar\n"
    ruta = _preparar_env_falso(tmp_path, original)
    master_fd = None
    slave_fd = None
    try:
        master_fd, slave_fd = pty.openpty()
        proc = subprocess.Popen(
            ["sudo", "env", f"JAX_ENV_PATH={ruta}", "bash", str(SCRIPT)],
            stdin=slave_fd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        os.write(master_fd, f"{TOKEN_VALIDO}\n{TOKEN_VALIDO}\n\n".encode())
        proc.wait(timeout=15)
        salida_err = proc.stderr.read().decode(errors="replace")
        proc.stdout.close()
        proc.stderr.close()

        assert proc.returncode == 0, salida_err
        assert f"JAX_GITHUB_TOKEN={TOKEN_VALIDO}" in ruta.read_text()
    finally:
        if master_fd is not None:
            os.close(master_fd)
        if slave_fd is not None:
            os.close(slave_fd)
        _limpiar(ruta)


# --------------------------------------------------------------------------
# DC3: grano fino, nunca un token clásico.
# --------------------------------------------------------------------------

def test_token_clasico_ghp_se_rechaza(tmp_path):
    """DC3 exige grano fino ('github_pat_...'). Un token clásico
    ('ghp_...'), aunque tenga forma y largo válidos, tiene que rechazarse
    igual que cualquier otro prefijo equivocado -- el .env queda intacto."""
    original = "FOO=bar\n"
    ruta = _preparar_env_falso(tmp_path, original)
    try:
        token_clasico = "ghp_" + "A" * 60
        resultado = _correr(ruta, token_clasico)
        assert resultado.returncode != 0
        assert ruta.read_text() == original
    finally:
        _limpiar(ruta)
