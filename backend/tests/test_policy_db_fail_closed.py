"""
Ningun modulo se conecta a la DB con un default silencioso.

EL DEFECTO. `localhost:3306` no es "un default razonable": en hall9000 esa
instancia de MariaDB **no existe** -- la real escucha en `:3308` (ver la
memoria `jax-dual-mariadb-instances`). Un `os.getenv("JAX_DB_PORT", "3306")`
convierte "falta configuracion" en "conecta a una instancia muerta", que es un
fallo mucho mas caro de diagnosticar que un error al arrancar.

POR QUE SIGUE ABIERTO EN ESTE REPO. La auditoria del 2026-08-24 cerro este
patron en 19 archivos... todos del repo `jax`. jax-platform es un repo
separado y quedo afuera. Q1 de la ronda de seguridad (2026-09-01) lo noto para
UNA copia -- `facet_resolver._db_conn`, portada a mano desde jax/core -- pero
el barrido no siguio: quedaban CUATRO sitios, incluido `db/connection.py::get_pool()`,
que es el pool principal del backend y lo usan 22 modulos. Es la tercera vez
que el mismo defecto se cierra en un repo y sobrevive en el otro.

POR ESO ESTE ARCHIVO NO PRUEBA UN SITIO, PRUEBA LA REGLA. Cada guard tiene su
test de comportamiento, y ademas hay un scanner que recorre todo el arbol de
produccion: un sitio nuevo escrito manana queda cubierto sin que nadie lo
agregue a ninguna lista. Cerrar los cuatro sin eso solo garantiza que haya un
quinto.

En memoria de Jairo Urbina.
"""
from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parent.parent
_DIRS_EXCLUIDOS = {".venv", "venv", "__pycache__", "node_modules", ".git", "tests"}

_VARS_DE_ENDPOINT = {"JAX_DB_HOST", "JAX_DB_PORT"}


@pytest.fixture
def pool_limpio(monkeypatch):
    """Registro de pools vacio y AISLADO para este test.

    `get_pool()` cachea un pool por event loop, asi que solo lee el entorno
    cuando tiene que CREAR uno. Si un test anterior de la suite ya creo el pool
    de este loop, la llamada devuelve el cacheado sin mirar la configuracion y
    el guard nunca se ejercita -- los 4 tests de abajo pasaban corridos solos y
    fallaban dentro de la suite, que es la firma de ese problema.

    Se reemplaza el diccionario entero (monkeypatch lo restaura al terminar) en
    vez de vaciar el compartido: vaciarlo dejaria a los tests siguientes con un
    pool nuevo y el viejo colgado sin cerrar.
    """
    import weakref

    from db import connection

    monkeypatch.setattr(connection, "_pools", weakref.WeakKeyDictionary())


@pytest.fixture
def sin_endpoint(monkeypatch, pool_limpio):
    """Quita JAX_DB_HOST/JAX_DB_PORT del entorno del proceso.

    Hace falta explicitamente porque `tests/conftest.py` carga /etc/jax/.env
    al importarse: sin esto los tests correrian con la configuracion real y no
    probarian nada.
    """
    for var in _VARS_DE_ENDPOINT:
        monkeypatch.delenv(var, raising=False)


# ---------------------------------------------------------------------------
# 1. Comportamiento, sitio por sitio
# ---------------------------------------------------------------------------

async def test_get_pool_falla_cerrado(sin_endpoint):
    """El pool principal del backend -- 22 modulos dependen de el."""
    from db import connection

    with pytest.raises(RuntimeError) as e:
        await connection.get_pool()
    assert "JAX_DB_HOST" in str(e.value)


async def test_credential_resolver_falla_cerrado(sin_endpoint):
    import credential_resolver

    with pytest.raises(RuntimeError) as e:
        await credential_resolver._db_conn()
    assert "JAX_DB_HOST" in str(e.value)


async def test_la_memoria_del_chat_falla_cerrado(sin_endpoint, monkeypatch):
    """`_ensure_memory` envuelve la conexion en un `except Exception` que la
    deja en False: sin un guard ANTES de ese try, una configuracion ausente no
    produce error sino memoria silenciosamente desactivada. El guard va afuera
    del try a proposito."""
    from api import chat

    # `MemoryDB` sale de `from jax.memory.db import MemoryDB`, con un
    # `except: MemoryDB = None` alrededor -- y en un runner el paquete `jax` no
    # es importable, asi que _ensure_memory sale por `if MemoryDB is None:
    # return False` ANTES de llegar al guard. El test pasaba en hall9000 y
    # fallaba en CI con "DID NOT RAISE". Se parchea con un doble para ejercitar
    # NUESTRO guard, que es lo que este test afirma, y no la importabilidad del
    # repo vecino.
    class _MemoriaDoble:
        is_connected = False

        async def connect(self, **kwargs):  # pragma: no cover -- no debe llegar aca
            raise AssertionError("el guard tenia que haber cortado antes de conectar")

    monkeypatch.setattr(chat, "MemoryDB", _MemoriaDoble)
    monkeypatch.setattr(chat, "_memory", None)
    monkeypatch.setattr(chat, "_memory_ready", False)

    with pytest.raises(RuntimeError) as e:
        await chat._ensure_memory()
    assert "JAX_DB_HOST" in str(e.value)


@pytest.mark.parametrize("falta", sorted(_VARS_DE_ENDPOINT))
async def test_falta_cualquiera_de_las_dos_alcanza(monkeypatch, pool_limpio, falta):
    """No es "las dos o ninguna": con host seteado y puerto no, el default del
    puerto volveria a apuntar a la instancia muerta."""
    monkeypatch.setenv("JAX_DB_HOST", "127.0.0.1")
    monkeypatch.setenv("JAX_DB_PORT", "3308")
    monkeypatch.delenv(falta)

    from db import connection

    with pytest.raises(RuntimeError):
        await connection.get_pool()


async def test_el_mensaje_dice_que_hacer(sin_endpoint):
    """Un fail-closed que no dice como arreglarlo se resuelve volviendo a poner
    el default. El mensaje nombra la instancia muerta y el archivo a sourcear."""
    from db import connection

    with pytest.raises(RuntimeError) as e:
        await connection.get_pool()
    mensaje = str(e.value)
    assert "3306" in mensaje, "tiene que nombrar la instancia muerta"
    assert "/etc/jax/.env" in mensaje, "tiene que decir de donde sale la config"


# ---------------------------------------------------------------------------
# 2. La regla, sobre todo el arbol
# ---------------------------------------------------------------------------

def _archivos_de_produccion():
    for path in BACKEND_ROOT.rglob("*.py"):
        rel = path.relative_to(BACKEND_ROOT)
        if any(part in _DIRS_EXCLUIDOS for part in rel.parts):
            continue
        yield path


def _defaults_silenciosos(source: str, nombre: str) -> list[int]:
    """Lineas con `os.getenv("JAX_DB_HOST"|"JAX_DB_PORT", <default>)`.

    Se mira el AST y no el texto: `getenv` partido en dos lineas, con el
    default en una variable, o llamado como `getenv(...)` sin el `os.` delante,
    son todos el mismo defecto y un grep no los ve igual. Un getenv de UN solo
    argumento no es violacion -- ese devuelve None y obliga a decidir.
    """
    tree = ast.parse(source, filename=nombre)
    malos = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or len(node.args) < 2:
            continue
        fn = node.func
        nombre_fn = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
        if nombre_fn != "getenv":
            continue
        primero = node.args[0]
        if isinstance(primero, ast.Constant) and primero.value in _VARS_DE_ENDPOINT:
            malos.append(node.lineno)
    return malos


def test_ningun_modulo_de_produccion_usa_un_default_de_endpoint():
    violaciones = []
    escaneados = 0
    for path in _archivos_de_produccion():
        escaneados += 1
        for lineno in _defaults_silenciosos(path.read_text(encoding="utf-8"), str(path)):
            violaciones.append(f"{path.relative_to(BACKEND_ROOT)}:{lineno}")

    assert escaneados > 20, f"solo {escaneados} archivos escaneados -- el scanner no mira nada"
    assert not violaciones, (
        "default silencioso de JAX_DB_HOST/JAX_DB_PORT (localhost:3306 es la "
        "instancia MUERTA; la real es :3308). Fallar cerrado, no adivinar:\n"
        + "\n".join(violaciones)
    )


@pytest.mark.parametrize("mutacion,descripcion", [
    ('x = os.getenv("JAX_DB_PORT", "3306")', "default de puerto"),
    ('x = os.getenv("JAX_DB_HOST", "localhost")', "default de host"),
    ('from os import getenv\nx = getenv("JAX_DB_HOST", "localhost")', "getenv importado suelto"),
    ('x = os.getenv(\n    "JAX_DB_PORT",\n    "3306",\n)', "partido en varias lineas"),
    ('D = "3306"\nx = os.getenv("JAX_DB_PORT", D)', "el default en una variable"),
])
def test_el_scanner_detecta_la_mutacion(mutacion, descripcion):
    assert _defaults_silenciosos(mutacion, "<mutacion>"), f"no detecto: {descripcion}"


@pytest.mark.parametrize("legitimo,descripcion", [
    ('x = os.getenv("JAX_DB_PORT")', "un solo argumento -- devuelve None y obliga a decidir"),
    ('x = os.environ.get("JAX_DB_HOST")', "environ.get sin default"),
    ('x = os.getenv("JAX_DB_NAME", "jax_memory")', "otra variable, fuera de esta regla"),
])
def test_el_scanner_no_grita_por_lo_legitimo(legitimo, descripcion):
    assert not _defaults_silenciosos(legitimo, "<ok>"), f"falso positivo: {descripcion}"


def test_el_tablero_no_inventa_un_puerto():
    """`api/admin/dashboard.py` no conecta: MUESTRA el puerto en el panel de
    salud. Un default ahi no rompe nada -- miente, que en un tablero de estado
    es peor. Se fija aparte porque el scanner de arriba ya lo cubre, pero la
    razon por la que ese sitio importa no es la misma."""
    ruta = BACKEND_ROOT / "api" / "admin" / "dashboard.py"
    arbol = ast.parse(ruta.read_text(encoding="utf-8"), filename=str(ruta))
    # Sobre LITERALES del AST, no sobre el texto: el comentario que explica por
    # que no hay default nombra "3306", y un grep crudo lo contaria como
    # violacion (paso: este test fallo asi antes de arreglarlo).
    literales = {
        n.value for n in ast.walk(arbol)
        if isinstance(n, ast.Constant) and isinstance(n.value, (str, int))
    }
    assert "3306" not in literales and 3306 not in literales, (
        "el tablero volvio a inventar el puerto de la instancia muerta"
    )
