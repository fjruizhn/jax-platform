"""Guard PASO 0 (2026-09-25): la suite nunca vuelve a cargar el `.env` de
producción, ni corre `sudo`, para armar su propio entorno.

El incidente que esto cierra: `tests/conftest.py` leía `/etc/jax/.env` (con
`sudo -n cat` de respaldo, en `tests/entorno_de_produccion.py`) en CADA
corrida de pytest, y volcaba TODAS sus claves al proceso -- la contraseña de
la base de producción, el JWT, la clave Fernet y el token de Telegram
incluidos, sin que ningún test los pidiera.

Este control es ESTRUCTURAL (AST), no un grep de texto libre sobre el
archivo entero: un grep de texto reventaría contra las MUCHAS menciones
legítimas que ya viven en `conftest.py` -- explican por qué cada variable se
FUERZA (no `setdefault`) para que un `/etc/jax/.env` fuente a mano en la
shell del operador no gane, lo cual sigue siendo cierto y sigue siendo buena
documentación -- y contra tests de otros archivos que nombran esa ruta a
propósito para verificar código de PRODUCCIÓN ajeno a este incidente
(`test_admin_keys_sin_escritura.py`, `test_policy_db_fail_closed.py`,
`test_tablero.py`). Lo que este control fija es el punto de entrada real del
incidente -- que `conftest.py`/`entorno_de_test.py` **ejecuten** una lectura
de `/etc/jax/.env` o invoquen `sudo` -- no que el texto lo nombre en un
comentario o una f-string de error."""
import ast
import importlib.util
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent

#: Los dos puntos de entrada de la configuración de sesión: lo único que
#: corre incondicionalmente para TODO test, sin que nadie lo pida.
ARCHIVOS_DE_ARRANQUE = ("conftest.py", "entorno_de_test.py")

#: Secretos de sesión: nunca pueden salir de un archivo de credenciales de
#: prueba. Si mañana alguien agrega uno nuevo (otro token de servicio, por
#: ejemplo), se agrega también acá.
SECRETOS_DE_SESION = ("JAX_JWT_SECRET", "FERNET_KEY", "TELEGRAM_BOT_TOKEN")

#: Llamadas que pueden leer un archivo o correr un subproceso.
_LLAMADAS_VIGILADAS = frozenset({"open", "run", "Popen", "call", "check_call", "check_output", "system", "getoutput"})


def _fuente(nombre: str) -> str:
    return (TESTS_DIR / nombre).read_text(encoding="utf-8")


def _nombre_de_la_llamada(nodo: ast.Call) -> str | None:
    if isinstance(nodo.func, ast.Name):
        return nodo.func.id
    if isinstance(nodo.func, ast.Attribute):
        return nodo.func.attr
    return None


def _constantes_de_texto(nodo: ast.AST) -> list[str]:
    return [n.value for n in ast.walk(nodo) if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def _llamadas_sospechosas(fuente: str, nombre_archivo: str) -> list[str]:
    """Cada `Call` a una de `_LLAMADAS_VIGILADAS` cuyos argumentos (a
    cualquier profundidad -- cubre `subprocess.run(["sudo", "-n", "cat", ...])`
    y también una lista armada aparte) contengan `"sudo"` como elemento, o
    una ruta que caiga bajo `/etc/jax/`. Sólo mira `ast.Call`: un comentario
    o una f-string de error (`f"... {ruta} ..."`) no arma una llamada real."""
    hallazgos = []
    arbol = ast.parse(fuente, filename=nombre_archivo)
    for nodo in ast.walk(arbol):
        if not isinstance(nodo, ast.Call):
            continue
        nombre = _nombre_de_la_llamada(nodo)
        if nombre not in _LLAMADAS_VIGILADAS:
            continue
        textos = [t for arg in nodo.args for t in _constantes_de_texto(arg)]
        textos += [t for kw in nodo.keywords for t in _constantes_de_texto(kw.value)]
        for texto in textos:
            if texto == "sudo" or texto.startswith("/etc/jax/"):
                hallazgos.append(f"{nombre_archivo}:{nodo.lineno}: {nombre}(...) con {texto!r}")
    return hallazgos


def test_entorno_de_produccion_ya_no_existe():
    """El módulo que leía `/etc/jax/.env` con `sudo -n cat` para toda la
    suite no puede volver a aparecer bajo el mismo nombre."""
    assert importlib.util.find_spec("tests.entorno_de_produccion") is None, (
        "tests/entorno_de_produccion.py volvió a aparecer (PASO 0, 2026-09-25 lo retiró): "
        "era el módulo que leía /etc/jax/.env con sudo -n cat para toda la suite."
    )


def test_conftest_no_importa_el_modulo_retirado():
    arbol = ast.parse(_fuente("conftest.py"), filename="conftest.py")
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.ImportFrom) and nodo.module and nodo.module.split(".")[-1] == "entorno_de_produccion":
            raise AssertionError("conftest.py vuelve a importar el módulo retirado entorno_de_produccion")
        if isinstance(nodo, ast.Import):
            for alias in nodo.names:
                assert alias.name.split(".")[-1] != "entorno_de_produccion"


def test_ningun_archivo_de_arranque_lee_produccion_ni_corre_sudo():
    """Ni `conftest.py` ni `entorno_de_test.py` ejecutan una lectura de algo
    bajo `/etc/jax/` ni invocan `sudo`, en ningún llamado real (no en
    prosa)."""
    hallazgos = []
    for nombre in ARCHIVOS_DE_ARRANQUE:
        hallazgos += _llamadas_sospechosas(_fuente(nombre), nombre)
    assert hallazgos == [], hallazgos


def test_lista_blanca_de_entorno_de_test_no_incluye_secretos_de_sesion():
    """`CLAVES_PERMITIDAS` de `entorno_de_test.py` es sólo credenciales de
    conexión a la base de PRUEBA. Ningún secreto de sesión (JWT, Fernet,
    tokens de servicio) puede colarse ahí: esos siempre se generan en la
    sesión, nunca se leen de un archivo."""
    from tests import entorno_de_test as E

    for prohibida in SECRETOS_DE_SESION:
        assert prohibida not in E.CLAVES_PERMITIDAS, (
            f"{prohibida} está en la lista blanca de tests/entorno_de_test.py: "
            "los secretos de sesión son siempre efímeros, nunca vienen de un archivo."
        )


def test_entorno_de_test_solo_lee_las_claves_de_la_lista_blanca(tmp_path):
    from tests import entorno_de_test as E

    archivo = tmp_path / "test-db.env"
    archivo.write_text(
        "JAX_DB_HOST=127.0.0.1\n"
        "JAX_JWT_SECRET=no-deberia-leerse\n"
        "FERNET_KEY=tampoco-deberia-leerse\n"
        "# comentario\n"
    )
    leido = E.cargar(str(archivo))
    assert leido == {"JAX_DB_HOST": "127.0.0.1"}


def test_entorno_de_test_sin_el_archivo_devuelve_vacio(tmp_path):
    from tests import entorno_de_test as E

    assert E.cargar(str(tmp_path / "no-existe.env")) == {}


def test_el_detector_de_llamadas_sospechosas_ve_sudo_y_produccion():
    """Control del control: contra una fuente de mentira que SÍ invoca sudo
    (subprocess.run) y SÍ abre algo bajo /etc/jax/, el detector tiene que
    encontrar las dos -- si esto no falla, `_llamadas_sospechosas` no sirve
    para nada y las otras aserciones de este archivo son verdes por
    accidente."""
    fuente_de_mentira = (
        "import subprocess\n"
        "subprocess.run(['sudo', '-n', 'cat', '/etc/jax/.env'])\n"
        "open('/etc/jax/.env')\n"
    )
    hallazgos = _llamadas_sospechosas(fuente_de_mentira, "mentira.py")
    assert any("sudo" in h for h in hallazgos)
    assert any("/etc/jax/.env" in h for h in hallazgos)
