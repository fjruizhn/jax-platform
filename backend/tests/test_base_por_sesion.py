"""La base de tests es propia de cada sesión (port de `jax`, 2026-09-20).

`backend/tests/conftest.py` fijaba `JAX_DB_NAME = "jax_memory_test"` a
secas, sin ningún aislamiento -- ni siquiera el opt-in que `jax` ya tenía
desde el 2026-09-17. Medido el 2026-09-20: dos worktrees corriendo la
suite a la vez, ninguno con `JAX_TEST_DB_SUFIJO` puesto, se pisaban contra
la misma base física (que además comparten los dos repos).

El control central (`test_el_sufijo_manda_en_la_base_que_usan_los_tests`)
corre en un SUBPROCESO a propósito: lo que se prueba es la resolución en
tiempo de IMPORT -- `tests/conftest.py` fijando `JAX_DB_NAME` antes de que
nada se conecte. En el proceso de pytest eso ya pasó y no se puede volver a
ejercitar.

En memoria de Jairo Urbina.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest

from base_de_test import (
    BASE_COMPARTIDA,
    BASE_DE_PRODUCCION,
    BaseDeTestInvalida,
    asegurar_base_de_test,
    _borrar_al_salir,
    _dropear_base_de_sesion,
    _sufijo_automatico_de_sesion,
    es_base_de_test,
    exigir_base_de_test,
    nombre_base_de_test,
)

BACKEND = Path(__file__).resolve().parents[1]


def _correr(codigo: str, **entorno: str) -> subprocess.CompletedProcess:
    """Corre `codigo` en un intérprete nuevo, con `backend/` en el path y
    SIN `JAX_DB_HOST` (así nada intenta crear ni tocar una base)."""
    env = dict(os.environ)
    env.pop("JAX_DB_HOST", None)
    env.pop("JAX_DB_NAME", None)
    env.pop("JAX_TEST_DB_SUFIJO", None)
    env.pop("CI", None)
    env.setdefault("JAX_JWT_SECRET", "ci-dummy-not-a-real-secret")
    env["PYTHONPATH"] = str(BACKEND)
    env.update(entorno)
    return subprocess.run([sys.executable, "-c", codigo], cwd=BACKEND, env=env,
                          capture_output=True, text=True, timeout=180)


# ---------------------------------------------------------------- control 1
# Con el sufijo puesto, la base que van a usar los tests es la del sufijo.

CODIGO_QUE_RESUELVE_LA_BASE = """
import os
import tests.conftest as _conftest  # el mismo camino que corre pytest (rootdir backend/)
print(os.environ["JAX_DB_NAME"])
"""


def test_el_sufijo_manda_en_la_base_que_usan_los_tests():
    r = _correr(CODIGO_QUE_RESUELVE_LA_BASE, JAX_TEST_DB_SUFIJO="zz_control_sufijo")
    assert r.returncode == 0, r.stderr
    resuelta = r.stdout.strip().splitlines()[-1]
    assert resuelta == f"{BASE_COMPARTIDA}_zz_control_sufijo", (
        f"con JAX_TEST_DB_SUFIJO puesto la suite resolvió {resuelta!r}: "
        f"sigue pisando la base compartida"
    )
    assert resuelta != BASE_COMPARTIDA


def test_sin_sufijo_fuera_de_ci_la_base_es_propia_y_no_la_compartida():
    """El choque medido el 2026-09-20: dos worktrees sin `JAX_TEST_DB_SUFIJO`
    puesto a mano compartían `jax_memory_test` y se pisaban de verdad. Fuera
    de CI, el aislamiento es el comportamiento por defecto -- nadie tiene que
    acordarse de exportar nada."""
    r = _correr(CODIGO_QUE_RESUELVE_LA_BASE)
    assert r.returncode == 0, r.stderr
    resuelta = r.stdout.strip().splitlines()[-1]
    assert resuelta != BASE_COMPARTIDA, (
        f"sin JAX_TEST_DB_SUFIJO y fuera de CI, la suite resolvió {resuelta!r}: "
        f"sigue siendo la base compartida, que es el defecto que colisionó "
        f"el 2026-09-20."
    )
    assert resuelta.startswith(f"{BASE_COMPARTIDA}_")


def test_dos_procesos_sin_sufijo_se_aislan_entre_si():
    """El control central del choque del 2026-09-20: DOS procesos sin
    sufijo, cada uno resuelve una base DISTINTA."""
    r1 = _correr(CODIGO_QUE_RESUELVE_LA_BASE)
    r2 = _correr(CODIGO_QUE_RESUELVE_LA_BASE)
    assert r1.returncode == 0, r1.stderr
    assert r2.returncode == 0, r2.stderr
    base1 = r1.stdout.strip().splitlines()[-1]
    base2 = r2.stdout.strip().splitlines()[-1]
    assert base1 != base2, (
        f"dos procesos sin sufijo resolvieron la MISMA base ({base1!r}): "
        f"eso es la colisión, no el aislamiento."
    )


def test_sin_sufijo_en_ci_sigue_siendo_la_compartida():
    """Compatibilidad hacia atrás DELIBERADA para CI: el único job de
    `.github/workflows/policy.yml` que toca la base (`backend-tests-db`)
    corre contra su propio contenedor MariaDB efímero -- no hay sesiones
    concurrentes que se puedan pisar ahí. `CI` es la variable que exportan
    GitHub Actions, GitLab CI y CircleCI por convención."""
    r = _correr(CODIGO_QUE_RESUELVE_LA_BASE, CI="true")
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip().splitlines()[-1] == BASE_COMPARTIDA


# ---------------------------------------------------------------- control 2
# Un sufijo inválido es un error explícito, NO una caída a la compartida.

SUFIJOS_INVALIDOS = [
    "",                    # vacío: quien exportó la variable quiso base propia
    "Ronda",               # mayúsculas
    "con-guion",           # guion medio
    "con espacio",
    "punto.coma",
    "drop; drop database jax_memory",
    "acentuada_ñ",
    "x" * 80,              # pasa los 64 del identificador
]


@pytest.mark.parametrize("sufijo", SUFIJOS_INVALIDOS)
def test_sufijo_invalido_es_error_y_no_fallback(sufijo):
    with pytest.raises(BaseDeTestInvalida):
        nombre_base_de_test(sufijo)


@pytest.mark.parametrize("sufijo", ["", "con-guion", "drop; drop database jax_memory"])
def test_sufijo_invalido_corta_al_arrancar(sufijo):
    """Y corta de verdad al importar el conftest: no es una excepción que
    alguien atrapa más tarde."""
    r = _correr(CODIGO_QUE_RESUELVE_LA_BASE, JAX_TEST_DB_SUFIJO=sufijo)
    assert r.returncode != 0, (
        f"con JAX_TEST_DB_SUFIJO={sufijo!r} la suite arrancó igual "
        f"(salida: {r.stdout.strip()!r}) -- eso es el fallback silencioso"
    )
    assert "JAX_TEST_DB_SUFIJO" in r.stderr
    assert BASE_COMPARTIDA not in r.stdout


# ---------------------------------------------------------------- control 3
# Nunca, por ningún camino, la base de producción.

def test_nunca_resuelve_a_produccion():
    assert nombre_base_de_test("loquesea") != BASE_DE_PRODUCCION
    # El sufijo que armaría el nombre de producción no existe: no hay sufijo
    # `s` tal que `jax_memory_test_<s>` sea `jax_memory`.
    assert not es_base_de_test(BASE_DE_PRODUCCION)
    assert not es_base_de_test("jax_memory_prod")
    assert not es_base_de_test("jax_memoryx_test")


def test_una_base_de_produccion_ya_exportada_es_error(monkeypatch):
    """El caso real: `set -a; . <(sudo -n cat /etc/jax/.env); set +a` deja
    JAX_DB_NAME=jax_memory. Un test que escribe filas no puede correr así."""
    monkeypatch.setenv("JAX_DB_NAME", BASE_DE_PRODUCCION)
    with pytest.raises(BaseDeTestInvalida):
        exigir_base_de_test()
    assert os.environ["JAX_DB_NAME"] == BASE_DE_PRODUCCION  # no lo pisó en silencio


def test_el_verificador_de_produccion_no_se_puede_esquivar():
    from base_de_test import _verificar_que_no_es_produccion
    with pytest.raises(BaseDeTestInvalida):
        _verificar_que_no_es_produccion(BASE_DE_PRODUCCION)
    with pytest.raises(BaseDeTestInvalida):
        _verificar_que_no_es_produccion("otra_base_cualquiera")
    assert _verificar_que_no_es_produccion(BASE_COMPARTIDA) == BASE_COMPARTIDA


# ---------------------------------------------------------------------------
# El borrado automático al salir (mismo mecanismo que jax): el default de
# arriba crea una base nueva en CADA corrida local sin sufijo -- sin esto se
# acumulan bases huérfanas en la MISMA MariaDB que usa `jax`.
# ---------------------------------------------------------------------------

def test_el_borrado_al_salir_se_niega_a_tocar_produccion_y_la_compartida(monkeypatch):
    """El candado: intenta borrar `jax_memory` (producción), la compartida
    pelada, y un nombre cualquiera que no lleve el prefijo de test. Ninguno
    de los tres llega siquiera a abrir una conexión -- `aiomysql.connect`
    explota el test si algo lo intenta."""
    import aiomysql

    def _connect_prohibido(*_a, **_k):
        raise AssertionError("intentó conectar para borrar algo que no es una base de test")

    monkeypatch.setattr(aiomysql, "connect", _connect_prohibido)

    for nombre in (BASE_DE_PRODUCCION, BASE_COMPARTIDA, "otra_cosa_cualquiera"):
        asyncio.run(_dropear_base_de_sesion(nombre))  # no debe lanzar ni conectar


def test_el_borrado_al_salir_no_hace_nada_sin_jax_db_host(monkeypatch):
    monkeypatch.delenv("JAX_DB_HOST", raising=False)

    def _run_prohibido(*_a, **_k):
        raise AssertionError("no debería intentar correr nada sin JAX_DB_HOST")

    monkeypatch.setattr(asyncio, "run", _run_prohibido)
    _borrar_al_salir(f"{BASE_COMPARTIDA}_lo_que_sea")  # no debe lanzar


def test_el_borrado_al_salir_no_hace_nada_bajo_ci_sin_db(monkeypatch):
    """El caso real medido en hall9000: `JAX_DB_HOST` SÍ está puesto (lo deja
    `/etc/jax/.env`, que `tests/conftest.py` carga con `setdefault` en cada
    corrida local) aunque la sesión corra en modo `JAX_CI_NO_DB=1`. Ese modo
    dice "no hay MariaDB a mano para este runner" -- que haya una variable
    con pinta de host válida no lo cambia.

    Un `AssertionError` levantado desde `asyncio.run` NO sirve acá para
    detectar la llamada: `_borrar_al_salir` envuelve ese `asyncio.run` en un
    `except Exception: pass` a propósito (fail-soft de limpieza), así que se
    tragaría el `AssertionError` igual que cualquier otro error real -- un
    control así da verde contra el código viejo sin haber probado nada. Por
    eso acá se GRABA la llamada en vez de reventarla."""
    monkeypatch.setenv("JAX_CI_NO_DB", "1")
    monkeypatch.setenv("JAX_DB_HOST", "127.0.0.1")

    llamadas = []
    monkeypatch.setattr(asyncio, "run", lambda *a, **k: llamadas.append((a, k)))
    _borrar_al_salir(f"{BASE_COMPARTIDA}_lo_que_sea")  # no debe lanzar
    assert llamadas == [], (
        "asyncio.run() se llamó bajo JAX_CI_NO_DB=1: intentó tocar la base"
    )


def test_asegurar_base_de_test_no_toca_nada_bajo_ci_sin_db(monkeypatch):
    """La causa raíz de los 3 rojos de este archivo bajo
    `JAX_CI_NO_DB=1 CI=true JAX_DB_PORT=3306`: `asegurar_base_de_test()`
    sólo miraba `JAX_DB_HOST` para decidir si hay MariaDB a mano, y
    `JAX_DB_HOST` SIGUE puesto en modo CI-sin-DB en cualquier máquina con
    `/etc/jax/.env` (hall9000 entre ellas) -- así que intentaba clonar el
    esquema de verdad y explotaba contra un puerto muerto/inexistente."""
    monkeypatch.setenv("JAX_CI_NO_DB", "1")
    monkeypatch.setenv("JAX_DB_HOST", "127.0.0.1")

    def _run_prohibido(*_a, **_k):
        raise AssertionError("no debería intentar correr nada bajo JAX_CI_NO_DB=1")

    monkeypatch.setattr(asyncio, "run", _run_prohibido)
    nombre = f"{BASE_COMPARTIDA}_zz_ci_sin_db"
    assert asegurar_base_de_test(nombre) == nombre  # no debe lanzar ni conectar


def test_el_sufijo_automatico_se_registra_para_borrarse_al_salir(monkeypatch):
    """Un sufijo AUTO-generado queda registrado en `atexit` para borrarse.
    Uno EXPLÍCITO (pasado a mano) NO se registra -- alguien pudo querer
    reusarlo entre corridas."""
    registrados = []
    monkeypatch.setattr(
        "base_de_test.atexit.register",
        lambda fn, *args: registrados.append((fn, args)),
    )
    sufijo = _sufijo_automatico_de_sesion()
    assert len(registrados) == 1
    fn, args = registrados[0]
    assert fn is _borrar_al_salir
    assert args == (f"{BASE_COMPARTIDA}_{sufijo}",)


# ---------------------------------------------------------------------------
# Guarda contra volver a hardcodear el nombre de la base fuera del módulo
# dueño (el defecto original de este port: conftest.py:13 decidía el nombre
# a secas).
# ---------------------------------------------------------------------------

_DUENIOS_DE_LA_DECISION = {"base_de_test.py", "test_base_por_sesion.py", "conftest.py"}


def _archivos_del_backend():
    yield from BACKEND.glob("tests/test_*.py")


def test_ningun_test_hardcodea_jax_db_name_fuera_del_modulo_dueno():
    """Validado por mutación: reponiendo `os.environ["JAX_DB_NAME"] =
    "jax_memory_test"` en cualquier archivo de test, este control se pone
    rojo y nombra el archivo y la línea. `test_migraciones_sin_jwt.py` arma
    un `dict` literal para un SUBPROCESO que nunca pasa por pytest (no
    importa `tests.conftest`, así que no decide nada del lado del proceso
    de la suite) y queda excluido por eso, no por privilegio."""
    patron_prohibido = 'os.environ["JAX_DB_NAME"] ='
    excluido = {"test_migraciones_sin_jwt.py"} | _DUENIOS_DE_LA_DECISION
    hallazgos = []
    for f in _archivos_del_backend():
        if f.name in excluido:
            continue
        for n, linea in enumerate(f.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            desnuda = linea.split("#", 1)[0]
            if patron_prohibido in desnuda:
                hallazgos.append(f"{f.relative_to(BACKEND)}:{n}: {linea.strip()[:90]}")
    assert hallazgos == [], (
        "un test decide JAX_DB_NAME por su cuenta en vez de usar "
        "exigir_base_de_test()/fijar_base_de_test() de base_de_test.py:\n"
        + "\n".join(hallazgos)
    )


# ---------------------------------------------------------------------------
# Fix round 4 de Task 4 (descartar-pipelines, 2026-09-22, Ruling 18/19
# punto 4): clonar una tabla con una columna GENERATED y filas rompía con
# 1906 -- `jacobs_pipelines.visible` (columna que agrega `jax`, Ruling
# 18/19) es la primera columna generada que pasa por `_clonar_esquema()`,
# y la plantilla compartida (`jax_memory_test`) SÍ puede tener filas en esa
# tabla (jax-platform y jax comparten la misma base física). Mismo bug,
# mismo arreglo, que jax aplicó a su propio `base_de_test.py` el mismo día
# -- ver el docstring del módulo (espejo mínimo, sin paquete compartido).
# `_columnas_copiables()`/`_copiar_filas()` arreglan esto con una lista
# EXPLÍCITA de columnas (sin las GENERATED), no `SELECT *`. Se prueba
# contra la base de la SESIÓN (no `jax_memory_test`, la plantilla real,
# para no tocarla) con una tabla propia, desechable: origen (con datos) y
# destino (vacía, misma forma) en una SEGUNDA base creada y borrada por el
# propio test.
# ---------------------------------------------------------------------------

import uuid as _uuid  # noqa: E402

from base_de_test import _columnas_copiables, _copiar_filas  # noqa: E402


@pytest.mark.skipif(not os.environ.get("JAX_DB_HOST"), reason="necesita la MariaDB real")
def test_copiar_filas_excluye_columnas_generadas_y_no_revienta_con_1906():
    async def _cuerpo():
        from db.connection import get_pool

        origen = nombre_base_de_test()  # la base de ESTA sesión, ya existe
        destino = f"{origen}_clon{_uuid.uuid4().hex[:8]}"
        tabla = f"_diag_gen_{_uuid.uuid4().hex[:8]}"
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(f"CREATE DATABASE `{destino}`")
                try:
                    ddl = (
                        f"CREATE TABLE `{{esquema}}`.`{tabla}` ("
                        "id INT PRIMARY KEY, status VARCHAR(20) NOT NULL, "
                        "visible TINYINT(1) GENERATED ALWAYS AS "
                        "(status NOT IN ('discarded','hidden')) VIRTUAL)"
                    )
                    await cur.execute(ddl.format(esquema=origen))
                    await cur.execute(ddl.format(esquema=destino))
                    await cur.executemany(
                        f"INSERT INTO `{origen}`.`{tabla}` (id, status) VALUES (%s,%s)",
                        [(1, "completed"), (2, "discarded"), (3, "running")],
                    )
                    # El paso que rompía: `SELECT *`/`INSERT ... SELECT *`
                    # incluiría `visible` -- 1906. `_copiar_filas` no.
                    await _copiar_filas(cur, origen, destino, tabla)
                    await cur.execute(
                        f"SELECT id, status, visible FROM `{destino}`.`{tabla}` ORDER BY id"
                    )
                    filas = await cur.fetchall()
                finally:
                    await cur.execute(f"DROP TABLE IF EXISTS `{origen}`.`{tabla}`")
                    await cur.execute(f"DROP DATABASE IF EXISTS `{destino}`")
        return filas

    filas = asyncio.run(_cuerpo())
    assert list(filas) == [(1, "completed", 1), (2, "discarded", 0), (3, "running", 1)], (
        "las filas copiadas no coinciden -- o no llegaron, o `visible` no se "
        "recalculó igual en la tabla destino"
    )


@pytest.mark.skipif(not os.environ.get("JAX_DB_HOST"), reason="necesita la MariaDB real")
def test_columnas_copiables_excluye_solo_las_generadas():
    async def _cuerpo():
        from db.connection import get_pool

        origen = nombre_base_de_test()
        tabla = f"_diag_cols_{_uuid.uuid4().hex[:8]}"
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"CREATE TABLE `{origen}`.`{tabla}` ("
                    "id INT PRIMARY KEY, status VARCHAR(20) NOT NULL, "
                    "visible TINYINT(1) GENERATED ALWAYS AS "
                    "(status NOT IN ('discarded','hidden')) VIRTUAL)"
                )
                try:
                    return await _columnas_copiables(cur, origen, tabla)
                finally:
                    await cur.execute(f"DROP TABLE IF EXISTS `{origen}`.`{tabla}`")

    columnas = asyncio.run(_cuerpo())
    assert columnas == ["id", "status"]
