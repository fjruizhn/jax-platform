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


# ---------------------------------------------------------------------------
# Fix round 5 (2026-09-22, re-review): los dos tests de arriba prueban los
# HELPERS (`_columnas_copiables`/`_copiar_filas`) en aislamiento -- no
# prueban que `_clonar_esquema()` los LLAME de verdad en el call site real
# (la línea `await _copiar_filas(cur, BASE_PLANTILLA, nombre, tabla)`, antes
# `INSERT INTO ... SELECT * FROM ...`). Un regreso a `SELECT *` en ESA línea
# -- dejando los helpers definidos pero sin usar -- no lo hubiera detectado
# ninguno de los dos. Este test ejercita `_clonar_esquema()` de punta a
# punta, con una PLANTILLA PROPIA (nunca `jax_memory_test` real, para no
# tocarla) que tiene una tabla con columna GENERATED y filas -- igual que
# el escenario real que rompía (`jax_memory_test` con una fila en
# `jacobs_pipelines`, que sí tiene `visible`).
#
# Mutación verificada a mano (revertida después de confirmarla): volver el
# call site de `_clonar_esquema()` a
# `INSERT INTO \`{nombre}\`.\`{tabla}\` SELECT * FROM \`{BASE_PLANTILLA}\`.\`{tabla}\``
# -- con `_columnas_copiables`/`_copiar_filas` intactas pero sin usar --
# hace caer este test con `pymysql.err.OperationalError: (1906, "The value
# specified for generated column 'visible' in table '...' has been
# ignored")` -- texto EXACTO de MariaDB (verificado en vivo contra la base
# real; MySQL usa otra redacción para el mismo código).
# ---------------------------------------------------------------------------

import base_de_test as _base_de_test_modulo  # noqa: E402


def test_clonar_triggers_preserva_cuerpo_y_contexto_sin_reusar_esquema_origen():
    class Cursor:
        def __init__(self):
            self.statements = []
            self._rows = []
            self._one = None

        async def execute(self, statement, parameters=None):
            self.statements.append((statement, parameters))
            if "FROM information_schema.TRIGGERS" in statement:
                self._rows = [(
                    "no_update_memory_events", "memory_events", "BEFORE", "UPDATE",
                    "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='immutable'",
                    "STRICT_TRANS_TABLES", "utf8mb4", "utf8mb4_unicode_ci",
                )]
            elif statement.startswith("SELECT @@SESSION.sql_mode"):
                self._one = ("", "utf8mb4", "utf8mb4_general_ci")

        async def fetchall(self):
            return self._rows

        async def fetchone(self):
            return self._one

    cursor = Cursor()
    copied = asyncio.run(_base_de_test_modulo._copiar_triggers(
        cursor, "jax_memory_test", "jax_memory_test_session"))
    ddls = [statement for statement, _params in cursor.statements
            if statement.startswith("CREATE TRIGGER")]
    assert copied == 1
    assert ddls == [
        "CREATE TRIGGER `jax_memory_test_session`.`no_update_memory_events` "
        "BEFORE UPDATE ON `jax_memory_test_session`.`memory_events` FOR EACH ROW "
        "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='immutable'"
    ]
    assert "SET SESSION sql_mode=%s" in [s for s, _p in cursor.statements]
    assert any(s == "SET NAMES utf8mb4 COLLATE utf8mb4_general_ci"
        for s, _p in cursor.statements)


@pytest.mark.skipif(not os.environ.get("JAX_DB_HOST"), reason="necesita la MariaDB real")
def test_clonar_esquema_preserva_columnas_generadas_y_triggers(monkeypatch):
    plantilla = f"{BASE_COMPARTIDA}_plantilla_diag_{_uuid.uuid4().hex[:8]}"
    destino = f"{BASE_COMPARTIDA}_clondiag_{_uuid.uuid4().hex[:8]}"
    tabla = f"_diag_clon_{_uuid.uuid4().hex[:8]}"

    async def _cuerpo():
        from db.connection import get_pool

        pool = await get_pool()
        # Cierre (Ruling 23, punto (f)): la creación de `plantilla` vivía
        # ANTES de este `try` -- si el CREATE DATABASE hubiera tenido éxito
        # pero el CREATE TABLE o el INSERT que siguen hubieran fallado, el
        # `finally` de abajo nunca corría y `plantilla` quedaba huérfana en
        # la base real. Ahora TODO lo que puede fallar después de crear la
        # base -- create, insert, el clonado, el select de verificación --
        # está dentro del mismo `try`, así que el `finally` la borra pase lo
        # que pase.
        try:
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(f"CREATE DATABASE `{plantilla}`")
                    await cur.execute(
                        f"CREATE TABLE `{plantilla}`.`{tabla}` ("
                        "id INT PRIMARY KEY, status VARCHAR(20) NOT NULL, "
                        "visible TINYINT(1) GENERATED ALWAYS AS "
                        "(status NOT IN ('discarded','hidden')) VIRTUAL)"
                    )
                    await cur.execute(
                        f"CREATE TRIGGER `{plantilla}`.`{tabla}_guard` "
                        f"BEFORE DELETE ON `{plantilla}`.`{tabla}` FOR EACH ROW "
                        "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='immutable row'"
                    )
                    await cur.executemany(
                        f"INSERT INTO `{plantilla}`.`{tabla}` (id, status) VALUES (%s,%s)",
                        [(1, "completed"), (2, "discarded")],
                    )
                await conn.commit()

            monkeypatch.setattr(_base_de_test_modulo, "BASE_PLANTILLA", plantilla)
            copiadas = await _base_de_test_modulo._clonar_esquema(destino)
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        f"SELECT id, status, visible FROM `{destino}`.`{tabla}` ORDER BY id"
                    )
                    filas = await cur.fetchall()
                    await cur.execute(
                        "SELECT TRIGGER_NAME, EVENT_OBJECT_TABLE, ACTION_STATEMENT "
                        "FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA=%s",
                        (destino,),
                    )
                    triggers = await cur.fetchall()
        finally:
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(f"DROP DATABASE IF EXISTS `{plantilla}`")
                    await cur.execute(f"DROP DATABASE IF EXISTS `{destino}`")
                await conn.commit()
        return copiadas, filas, triggers

    copiadas, filas, triggers = asyncio.run(_cuerpo())
    assert copiadas == 1, "no copió la única tabla de la plantilla propia"
    assert list(filas) == [(1, "completed", 1), (2, "discarded", 0)], (
        "las filas no llegaron a la base clonada, o `visible` no se "
        "recalculó igual -- _clonar_esquema() no está usando "
        "_copiar_filas() en su call site real"
    )
    assert list(triggers) == [(
        f"{tabla}_guard", tabla,
        "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='immutable row'")], (
        "el clon debe conservar el trigger en la tabla destino"
    )


# ---------------------------------------------------------------------------
# Fuera de CI, la suite no se conecta a la instancia de produccion sin una
# variable explicita (port de jax PR #355, 2026-10-05).
#
# Antes: `asegurar_base_de_test()` abria, fuera de CI y sin pedir nada, conexiones
# de ESCRITURA (CREATE/DROP DATABASE) en 127.0.0.1:3308 -- la MariaDB que tambien
# tiene `jax_memory` -- y `_parametros_de_conexion()` apunta por defecto a 3306. En
# jax el mismo gancho tenia ademas una lectura de `jax_memory` (la semilla de
# gobernanza); este repo NO tiene esa conexion, asi que el contrato de lectura
# (`lectura_de_produccion=True`) se ejercita sobre la funcion y no sobre un punto
# de conexion (ver los dos controles marcados). El conector de aiomysql es de
# mentira: cualquier conexion abierta falla el test.
# ---------------------------------------------------------------------------

VARIABLE_PERMISO = "JAX_TEST_DB_PERMITIR_INSTANCIA_DE_PRODUCCION"


class _ConectorProhibido:
    """Reemplaza `aiomysql.connect`: registra el intento y falla."""

    def __init__(self):
        self.intentos: list[dict] = []

    async def __call__(self, **kwargs):
        self.intentos.append(kwargs)
        raise AssertionError(f"se abrio una conexion prohibida: db={kwargs.get('db')!r} port={kwargs.get('port')!r}")


@pytest.fixture
def conector(monkeypatch):
    import aiomysql
    c = _ConectorProhibido()
    monkeypatch.setattr(aiomysql, "connect", c)
    monkeypatch.setenv("JAX_DB_HOST", "127.0.0.1")
    monkeypatch.setenv("JAX_DB_USER", "jax_test")
    monkeypatch.setenv("JAX_DB_PASSWORD", "x")
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("JAX_CI_NO_DB", raising=False)
    monkeypatch.delenv(VARIABLE_PERMISO, raising=False)
    return c


@pytest.mark.parametrize("puerto", ["3306", "3308"])
def test_fuera_de_ci_asegurar_se_niega_en_los_puertos_de_produccion(conector, monkeypatch, puerto):
    monkeypatch.setenv("JAX_DB_PORT", puerto)
    with pytest.raises(BaseDeTestInvalida, match=VARIABLE_PERMISO):
        asegurar_base_de_test("jax_memory_test_sesion1")
    assert conector.intentos == []


def test_fuera_de_ci_sin_puerto_explicito_el_default_tambien_se_niega(conector, monkeypatch):
    """`_parametros_de_conexion()` usa 3306 si falta `JAX_DB_PORT`: un default que cae en
    un puerto de produccion no puede ser la puerta de entrada."""
    monkeypatch.delenv("JAX_DB_PORT", raising=False)
    with pytest.raises(BaseDeTestInvalida, match=VARIABLE_PERMISO):
        asegurar_base_de_test("jax_memory_test_sesion1")
    assert conector.intentos == []


def test_los_puntos_de_conexion_se_niegan_por_si_solos(conector, monkeypatch):
    """No basta con la guarda de `asegurar_base_de_test`: los tests llaman a
    estas funciones directamente."""
    from base_de_test import _clonar_esquema, _tabla_existe_en_base
    monkeypatch.setenv("JAX_DB_PORT", "3308")
    for funcion in (_clonar_esquema, _dropear_base_de_sesion):
        with pytest.raises(BaseDeTestInvalida, match=VARIABLE_PERMISO):
            asyncio.run(funcion("jax_memory_test_sesion1"))
    with pytest.raises(BaseDeTestInvalida, match=VARIABLE_PERMISO):
        asyncio.run(_tabla_existe_en_base("jax_memory_test_sesion1", "projects"))
    assert conector.intentos == []


def test_el_bootstrap_del_esquema_de_jax_se_niega_antes_de_lanzar_el_cliente(conector, monkeypatch):
    """Este repo tiene una conexion que jax no tiene: el cliente `mysql` por subprocess."""
    import base_de_test
    from base_de_test import _bootstrap_jax_schema_para_base_de_test

    def _subprocess_prohibido(*a, **k):
        raise AssertionError("lanzo el cliente mysql contra un puerto de produccion")

    monkeypatch.setattr(base_de_test.subprocess, "run", _subprocess_prohibido)
    monkeypatch.setenv("JAX_DB_PORT", "3308")
    with pytest.raises(BaseDeTestInvalida, match=VARIABLE_PERMISO):
        _bootstrap_jax_schema_para_base_de_test("jax_memory_test_sesion1")


def test_las_migraciones_b9_de_test_se_niegan_antes_de_pedir_el_pool(conector, monkeypatch, tmp_path):
    """Las migraciones B9 escriben por el pool de `db.connection`, no por `aiomysql.connect`
    de este modulo: la guarda tiene que estar antes de `get_pool()`. Autocontenido: una
    migracion pendiente y declarada en un manifiesto de mentira, para llegar hasta el pool
    sin depender del checkout de jax. La guarda va DESPUES de la validacion del manifiesto
    (que no conecta a nada): `test_b9_migraciones_restantes.py` exige ese error primero."""
    import base_de_test
    import db.connection
    import db.migrations
    from base_de_test import aplicar_migraciones_b9_restantes

    migracion = tmp_path / "007_prueba.sql"
    migracion.write_text("SELECT 1;\n", encoding="utf-8")
    monkeypatch.setattr(db.migrations, "_jax_b9_migration_root", lambda: tmp_path)
    monkeypatch.setattr(base_de_test, "_manifiesto_b9_de_produccion",
                        lambda: {migracion.name: {"sha256": base_de_test._sha256_de_archivo(migracion)}})

    async def _pool_prohibido():
        raise AssertionError("pidio el pool contra un puerto de produccion")

    monkeypatch.setattr(db.connection, "get_pool", _pool_prohibido)
    monkeypatch.setenv("JAX_DB_NAME", "jax_memory_test_sesion1")
    monkeypatch.setenv("JAX_DB_PORT", "3308")
    with pytest.raises(BaseDeTestInvalida, match=VARIABLE_PERMISO):
        asyncio.run(aplicar_migraciones_b9_restantes())


def test_la_lectura_de_produccion_exige_la_variable_aun_en_un_puerto_ajeno(conector, monkeypatch):
    """Contrato de la funcion (este repo no abre hoy esa conexion): la lectura de `jax_memory`
    no se permite sin permiso explicito, aunque el puerto sea el de un contenedor desechable."""
    from base_de_test import exigir_conexion_permitida
    monkeypatch.setenv("JAX_DB_PORT", "3399")
    with pytest.raises(BaseDeTestInvalida, match=VARIABLE_PERMISO):
        exigir_conexion_permitida(BASE_DE_PRODUCCION, lectura_de_produccion=True)


def test_con_la_variable_la_lectura_de_produccion_es_solo_de_jax_memory(conector, monkeypatch):
    """Con el permiso, `lectura_de_produccion=True` deja pasar `jax_memory` y nada mas."""
    from base_de_test import exigir_conexion_permitida
    monkeypatch.setenv("JAX_DB_PORT", "3399")
    monkeypatch.setenv(VARIABLE_PERMISO, "1")
    exigir_conexion_permitida(BASE_DE_PRODUCCION, lectura_de_produccion=True)
    for otra in ("otra_base", "jax_memory_test_sesion1"):
        with pytest.raises(BaseDeTestInvalida, match="solo de"):
            exigir_conexion_permitida(otra, lectura_de_produccion=True)


def test_ni_con_la_variable_se_escribe_en_una_base_sin_sufijo_de_test(conector, monkeypatch):
    from base_de_test import _clonar_esquema
    monkeypatch.setenv("JAX_DB_PORT", "3399")
    monkeypatch.setenv(VARIABLE_PERMISO, "1")
    for nombre in ("jax_memory", "otra_base", "jax_memory_testing"):
        with pytest.raises(BaseDeTestInvalida):
            asegurar_base_de_test(nombre)
        with pytest.raises(BaseDeTestInvalida):
            asyncio.run(_clonar_esquema(nombre))
    assert conector.intentos == []


def test_en_ci_los_puertos_de_produccion_siguen_permitidos(conector, monkeypatch):
    """Los jobs de CI corren su propio contenedor en 3306: la guarda no los toca."""
    from base_de_test import exigir_conexion_permitida
    monkeypatch.setenv("CI", "true")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    for puerto in ("3306", "3308"):
        monkeypatch.setenv("JAX_DB_PORT", puerto)
        exigir_conexion_permitida("jax_memory_test_sesion1")


@pytest.mark.parametrize("entorno", [
    {"CI": "true"},                                  # `CI` suelto: ya no alcanza
    {"CI": "1"},
    {"GITHUB_ACTIONS": "true"},                      # tampoco la otra sola
    {"CI": "true", "GITHUB_ACTIONS": "false"},
    {"CI": "true", "GITHUB_ACTIONS": ""},
])
@pytest.mark.parametrize("puerto", ["3306", "3308"])
def test_ci_solo_cuenta_con_ci_y_github_actions_true(conector, monkeypatch, entorno, puerto):
    """El runner de GitHub Actions (tambien el de hall9000) exporta `CI` y `GITHUB_ACTIONS=true`.
    Una de las dos sola, o `GITHUB_ACTIONS` distinto de `true`, sigue exigiendo el permiso explicito."""
    from base_de_test import exigir_conexion_permitida
    for clave, valor in entorno.items():
        monkeypatch.setenv(clave, valor)
    monkeypatch.setenv("JAX_DB_PORT", puerto)
    with pytest.raises(BaseDeTestInvalida, match=VARIABLE_PERMISO):
        exigir_conexion_permitida("jax_memory_test_sesion1")
    monkeypatch.setenv(VARIABLE_PERMISO, "1")        # y el permiso explicito sigue abriendo la puerta
    exigir_conexion_permitida("jax_memory_test_sesion1")


def test_un_puerto_ajeno_fuera_de_ci_se_permite_sin_variable(conector, monkeypatch):
    from base_de_test import exigir_conexion_permitida
    monkeypatch.setenv("JAX_DB_PORT", "3399")
    exigir_conexion_permitida("jax_memory_test_sesion1")
    exigir_conexion_permitida("jax_memory_test")


@pytest.mark.parametrize("puerto", ["", "abc", "-1", "70000"])
def test_un_puerto_ilegible_falla_cerrado(conector, monkeypatch, puerto):
    from base_de_test import exigir_conexion_permitida
    monkeypatch.setenv("JAX_DB_PORT", puerto)
    with pytest.raises(BaseDeTestInvalida, match="JAX_DB_PORT"):
        exigir_conexion_permitida("jax_memory_test_sesion1")


def test_la_guarda_de_asegurar_corta_antes_de_tocar_el_entorno_y_antes_de_clonar(conector, monkeypatch):
    """La guarda de `asegurar_base_de_test` corre ANTES de escribir `JAX_DB_NAME` y antes de
    clonar nada: `_clonar_esquema` de mentira falla si se la llama, y `JAX_DB_NAME` conserva
    el valor que tenia. Con la guarda movida despues de la asignacion, el nombre de la base
    de sesion quedaria en el entorno de un proceso que ni siquiera pudo conectarse."""
    import base_de_test
    llamadas = []

    async def _clonar_prohibido(nombre):
        llamadas.append(nombre)
        raise AssertionError("clono un esquema pese a la guarda")

    monkeypatch.setattr(base_de_test, "_clonar_esquema", _clonar_prohibido)
    monkeypatch.setenv("JAX_DB_NAME", "valor-previo-sin-tocar")
    monkeypatch.setenv("JAX_DB_PORT", "3308")
    with pytest.raises(BaseDeTestInvalida, match=VARIABLE_PERMISO):
        asegurar_base_de_test("jax_memory_test_sesion1")
    assert llamadas == []
    assert os.environ["JAX_DB_NAME"] == "valor-previo-sin-tocar"
    monkeypatch.delenv("JAX_DB_NAME")
    with pytest.raises(BaseDeTestInvalida, match=VARIABLE_PERMISO):
        asegurar_base_de_test("jax_memory_test_sesion1")
    assert "JAX_DB_NAME" not in os.environ


def test_el_cliente_mysql_del_bootstrap_ignora_los_archivos_de_opciones_y_va_por_tcp(conector, monkeypatch, tmp_path):
    """`mysql` lee `~/.my.cnf` y `/etc/mysql/*`: un `host`, `socket` o `port` ahi lo desviaria
    de `JAX_DB_HOST`/`JAX_DB_PORT` sin que el guard lo vea. `--no-defaults` va PRIMERO (es
    la unica posicion en que el cliente lo acepta) y `--protocol=TCP` fuerza el host/puerto."""
    import base_de_test
    from base_de_test import _bootstrap_jax_schema_para_base_de_test

    (tmp_path / "jax_memory_schema.sql").write_text(
        "CREATE DATABASE IF NOT EXISTS jax_memory CHARACTER SET utf8mb4;\n"
        "USE jax_memory;\n"
        "CREATE TABLE `projects` (id INT PRIMARY KEY);\n", encoding="utf-8")
    capturado = {}

    def _run_falso(argv, **kwargs):
        capturado["argv"] = argv
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(base_de_test.subprocess, "run", _run_falso)
    monkeypatch.setenv("JAX_REPO_PATH", str(tmp_path))
    monkeypatch.setenv("JAX_DB_PORT", "3399")
    _bootstrap_jax_schema_para_base_de_test("jax_memory_test_sesion1")
    argv = capturado["argv"]
    assert argv[:3] == ["mysql", "--no-defaults", "--protocol=TCP"], argv
    assert argv[argv.index("--port") + 1] == "3399"


def test_un_nombre_que_pasa_el_verificador_pero_no_es_base_de_test_se_niega(conector, monkeypatch):
    """Auditoria Jax#355, MINOR 1. `jax_memory_test_A-B` tiene el prefijo (pasa
    `_verificar_que_no_es_produccion`) pero no es un sufijo valido (`es_base_de_test` da False).
    Sin la rama `elif not es_base_de_test(base)` de `exigir_conexion_permitida`, nada lo frenaria
    antes de abrir la conexion: este control cae si se la quita."""
    from base_de_test import _clonar_esquema, _verificar_que_no_es_produccion
    nombre = "jax_memory_test_A-B"
    assert _verificar_que_no_es_produccion(nombre) == nombre
    assert not es_base_de_test(nombre)
    monkeypatch.setenv("JAX_DB_PORT", "3399")
    for permiso in (False, True):
        if permiso:
            monkeypatch.setenv(VARIABLE_PERMISO, "1")
        with pytest.raises(BaseDeTestInvalida, match="no es una base de tests"):
            asegurar_base_de_test(nombre)
        with pytest.raises(BaseDeTestInvalida, match="no es una base de tests"):
            asyncio.run(_clonar_esquema(nombre))
    asyncio.run(_dropear_base_de_sesion(nombre))   # el borrado ya la ignoraba (es_base_de_test): sigue sin conectar
    assert conector.intentos == []
