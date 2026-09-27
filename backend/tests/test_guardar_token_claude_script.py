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
import os
import pwd
import stat
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "ops" / "guardar-token-claude.sh"
# MAJOR-5 (segunda auditoría adversarial, 2026-09-27): 100 caracteres --
# medido en un token OAuth REAL "sk-ant-oat01-…" de la cuenta de Fernando,
# que mide 108. Éste (113) queda cómodo por encima del mínimo sin acercarse
# al máximo holgado (400) que el script también exige.
TOKEN_VALIDO = "sk-ant-oat01-" + "x" * 100


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


# --------------------------------------------------------------------------
# A-8 (auditoría adversarial del commit d549335, 2026-09-27)
# --------------------------------------------------------------------------

def test_token_con_prefijo_correcto_pero_alfabeto_invalido_no_cambia_nada(tmp_path):
    """(a) El prefijo NO alcanza -- 'sk-ant-oat01-' seguido de un caracter
    fuera de [A-Za-z0-9_-] (acá un espacio) tiene que rechazarse igual que
    un token sin el prefijo."""
    original = "FOO=bar\n"
    ruta = _preparar_env_falso(tmp_path, original)
    try:
        resultado = _correr(ruta, "sk-ant-oat01-tiene un espacio adentro")
        assert resultado.returncode != 0
        assert ruta.read_text() == original
    finally:
        _limpiar(ruta)


def test_token_con_prefijo_correcto_pero_demasiado_corto_no_cambia_nada(tmp_path):
    original = "FOO=bar\n"
    ruta = _preparar_env_falso(tmp_path, original)
    try:
        resultado = _correr(ruta, "sk-ant-oat01-corto")
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
    """Boundary: el mínimo declarado por el script (100, MAJOR-5) tiene que
    aceptarse, no sólo rechazar por debajo de él."""
    original = "FOO=bar\n"
    ruta = _preparar_env_falso(tmp_path, original)
    try:
        minimo = _limite_del_script("LARGO_MINIMO")
        token = "sk-ant-oat01-" + ("a" * max(0, minimo - len("sk-ant-oat01-")))
        assert len(token) >= minimo
        resultado = _correr(ruta, token)
        assert resultado.returncode == 0, resultado.stderr
        assert f"CLAUDE_CODE_OAUTH_TOKEN={token}" in ruta.read_text()
    finally:
        _limpiar(ruta)


def test_token_valido_en_el_largo_maximo_exacto_se_acepta(tmp_path):
    """Boundary simétrico: el máximo (holgado, MAJOR-5) también tiene que
    aceptarse, no sólo rechazar por encima de él."""
    original = "FOO=bar\n"
    ruta = _preparar_env_falso(tmp_path, original)
    try:
        maximo = _limite_del_script("LARGO_MAXIMO")
        token = "sk-ant-oat01-" + ("a" * (maximo - len("sk-ant-oat01-")))
        assert len(token) == maximo
        resultado = _correr(ruta, token)
        assert resultado.returncode == 0, resultado.stderr
        assert f"CLAUDE_CODE_OAUTH_TOKEN={token}" in ruta.read_text()
    finally:
        _limpiar(ruta)


def test_token_mas_largo_que_el_maximo_no_cambia_nada(tmp_path):
    original = "FOO=bar\n"
    ruta = _preparar_env_falso(tmp_path, original)
    try:
        maximo = _limite_del_script("LARGO_MAXIMO")
        token = "sk-ant-oat01-" + ("a" * (maximo - len("sk-ant-oat01-") + 1))
        assert len(token) == maximo + 1
        resultado = _correr(ruta, token)
        assert resultado.returncode != 0
        assert ruta.read_text() == original
    finally:
        _limpiar(ruta)


def test_conserva_solo_los_dos_respaldos_mas_recientes(tmp_path):
    """(b) Con 3 corridas exitosas, sólo quedan 2 archivos de respaldo --
    los más viejos se borran, nunca nada que el script no haya creado él
    mismo (el glob es `fake.env.bak-*`, su propio patrón de nombre)."""
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
    """MINOR-6 (segunda auditoría adversarial, 2026-09-27): el glob de
    limpieza tiene que ser el patrón EXACTO que el propio script genera
    (fecha-hora numérica: 8 dígitos, guion, 6 dígitos) -- no `*` a secas.
    Un respaldo ajeno con el MISMO archivo base pero un sufijo puesto a mano
    (`antes-rotar`, no una fecha) tiene que sobrevivir."""
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
    """MINOR-6, la prueba que SÍ distingue el glob viejo del nuevo: un
    sufijo ajeno puramente numérico pero de otra forma (`bak-1`, un solo
    dígito) ordena ANTES que cualquier fecha real (`bak-2026...`,
    comparación de string: '1' < '2') -- con el glob viejo (`*` a secas)
    esto SÍ caía entre los "más viejos" y se borraba. El glob estricto
    (8 dígitos-guion-6 dígitos) nunca lo toma como candidato, sin importar
    dónde ordene."""
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
    """MINOR-6: el respaldo recién creado se excluye EXPLÍCITAMENTE del
    cálculo de "qué borrar" -- no depende sólo de que el orden alfabético lo
    deje último. Con 3 corridas, sobreviven el más nuevo y el segundo más
    nuevo; el primero se borra."""
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


def test_mensaje_final_menciona_los_dos_servicios_a_reiniciar(tmp_path):
    """(c) jax-platform Y jax-las-manos -- las dos partes que leen la
    credencial de anthropic (el sync del catálogo corre en el backend de
    jax-platform, pero el dispatch de chat real de la faceta también vive
    del lado de las-manos)."""
    original = "FOO=bar\n"
    ruta = _preparar_env_falso(tmp_path, original)
    try:
        resultado = _correr(ruta, TOKEN_VALIDO)
        assert resultado.returncode == 0, resultado.stderr
        assert "jax-platform" in resultado.stderr
        assert "jax-las-manos" in resultado.stderr
    finally:
        _limpiar(ruta)


def test_token_partido_en_dos_lineas_aborta_sin_tocar_el_env(tmp_path):
    """MAJOR-5 (segunda auditoría adversarial, 2026-09-27): un token pegado
    partido en dos líneas por el terminal -- `read -rs` sólo toma la
    primera, y la segunda queda esperando en la entrada. ANTES este script
    sólo drenaba ese resto y seguía adelante con la primera línea (a medias,
    probablemente truncada) como si fuera el token completo. Ahora CUALQUIER
    resto no vacío aborta sin tocar el .env: "el token llegó partido en
    varias líneas, pegalo de nuevo en una sola" -- ver
    test_vacia_lo_que_sobra_en_stdin_tras_leer_el_token (más abajo) para la
    prueba de que el resto se drena IGUAL, aunque el resultado ahora sea
    abortar y no continuar.

    Se ejercita con un pty de verdad (no un pipe simple: un pipe no tiene la
    semántica de "cola de entrada compartida" que sí tiene una terminal, así
    que con un pipe la segunda línea no llegaría a mezclarse con la
    primera)."""
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
        assert "partid" in salida_err.lower()  # el mensaje explica qué pasó
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
    `sudo bash guardar-token-claude.sh` la lea como si fuera SU propio
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
        # Sin eco: lo que se ESCRIBE en el master se reflejaría de inmediato
        # en una lectura del master, sin importar si el proceso del otro
        # lado lo llegó a leer o no -- confundiría "aparece en el buffer"
        # con "quedó sin consumir". Se lee del lado SLAVE (el mismo que
        # leería el próximo proceso que use esta terminal), con el eco
        # apagado para no ensuciar esa lectura tampoco.
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

        assert proc.returncode != 0  # abortó (MAJOR-5) -- pero drenó igual

        # Si el script vació stdin, no queda nada por leer del lado slave --
        # si el drenado NO ocurrió, la segunda línea seguiría entera en la
        # cola del terminal y este read la traería.
        listo, _, _ = select.select([slave_fd], [], [], 0.5)
        sobrante = os.read(slave_fd, 4096) if listo else b""
        assert b"resto-que-no-deberia-quedar-sin-leer" not in sobrante
    finally:
        if master_fd is not None:
            os.close(master_fd)
        if slave_fd is not None:
            os.close(slave_fd)
        _limpiar(ruta)
