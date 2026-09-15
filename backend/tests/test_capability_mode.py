"""
capability.mode (tanda A v3, spec 2026-09-14 §0 y §3.1).

`mode` dice si una capability cambia el estado del sistema ('mutating') o
solo produce texto/parches sin aplicarlos ('read_only'). Decisión de
Fernando (2026-09-14): 'mutating' SOLO file_write. Lo lee
MotorCatalog.from_db() (jax) y lo verifica el resolver de
CAPABILITY_AVAILABLE contra lo que afirma una faceta.

Tipo: VARCHAR(16) NOT NULL + CONSTRAINT chk_capability_mode CHECK (mode IN
('read_only','mutating')), SIN DEFAULT a propósito. v2 usaba
`ENUM('read_only','mutating') NOT NULL` sin default, midiendo que "sin
default, un INSERT sin modo falla" -- FALSO para ENUM en MariaDB 12.3.3:
con STRICT_TRANS_TABLES, un ENUM NOT NULL sin default guarda en silencio
el primer valor ('read_only') cuando se omite la columna -- exactamente el
fail-open que el diseño quería evitar. Con VARCHAR(16)+CHECK, medido en
`jax_memory_test`: omitir `mode` -> 1364 (ER_NO_DEFAULT_FOR_FIELD); un
valor fuera del conjunto -> 4025 (CONSTRAINT chk_capability_mode failed);
NULL -> 1048. Ver spec §0 v3.

Los dos primeros son PUROS. El resto usa `client` (base migrada) y, cuando
rompe el esquema a propósito, lo deja como estaba con run_migrations().

Nunca `pytest.raises(...)` DENTRO de una corrutina corrida por
`client.portal.call`: `Failed` es `BaseException` y mata el `BlockingPortal`
de sesión para el resto de la corrida (medido en el reporte anterior de
esta tarea: 635 -> 352 passed / 184 failed / 108 errors). Acá la corrutina
captura el error (código o excepción) y lo DEVUELVE; la aserción va afuera,
en el hilo sincrónico. `backend/tests/test_arnes_portal.py` prueba además
que el fixture `client` (conftest.py) captura y relanza cualquier
`BaseException` que se escape igual, como red de seguridad.
"""
from __future__ import annotations

import pymysql

from db import migrations

_INFO_MODE = (
    "SELECT IS_NULLABLE, COLUMN_DEFAULT, COLUMN_TYPE FROM information_schema.COLUMNS "
    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'capability' AND COLUMN_NAME = 'mode'"
)

_INFO_CHECK = (
    "SELECT CHECK_CLAUSE FROM information_schema.CHECK_CONSTRAINTS "
    "WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'capability' "
    "AND CONSTRAINT_NAME = 'chk_capability_mode'"
)


def _sembradas() -> set[str]:
    return ({fila[0] for fila in migrations._CAPABILITY_SEED}
            | {fila[0] for fila in migrations._FILE_CAPABILITY_SEED})


def test_tripwire_cada_capability_sembrada_declara_su_modo():
    """TRIPWIRE. Una capability nueva en la semilla sin su modo en
    _CAPABILITY_MODE rompe acá, antes de que su INSERT falle en producción."""
    assert set(migrations._CAPABILITY_MODE) == _sembradas()
    assert set(migrations._CAPABILITY_MODE.values()) <= {"read_only", "mutating"}


def test_solo_file_write_es_mutating():
    """Decisión de Fernando, 2026-09-14. Cambiarla es cambiar este test a
    propósito, con fecha y quién decidió."""
    assert {k for k, v in migrations._CAPABILITY_MODE.items() if v == "mutating"} == {"file_write"}


async def _sql(sentencia, args=None):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(sentencia, args)
            return await cur.fetchall()


def test_la_columna_mode_es_not_null_y_sin_default_con_check(client):
    """Forma v3: varchar(16)/NOT NULL/sin default, más el CHECK presente
    con su cláusula (spec §0 v3, §3.1)."""
    async def correr():
        return await _sql(_INFO_MODE), await _sql(_INFO_CHECK)

    info, check = client.portal.call(correr)
    assert info == (("NO", None, "varchar(16)"),)
    assert len(check) == 1
    clausula = check[0][0].lower()
    assert "read_only" in clausula and "mutating" in clausula


def test_los_valores_sembrados_son_los_de_capability_mode(client):
    filas = dict(client.portal.call(_sql, "SELECT `key`, mode FROM capability"))
    assert {k: filas.get(k) for k in migrations._CAPABILITY_MODE} == migrations._CAPABILITY_MODE
    assert {k for k, v in filas.items() if v == "mutating"} == {"file_write"}


def test_control_un_insert_sin_mode_falla(client):
    """CONTROL del sin-default: si alguien le pone DEFAULT a la columna,
    este INSERT entra y el test se pone rojo."""
    async def correr():
        try:
            await _sql(
                "INSERT INTO capability (`key`, risk_level, max_execution_minutes, allowed_callers) "
                "VALUES ('zz_sin_modo', 'low', 5, '[]')"
            )
        except pymysql.err.MySQLError as error:
            return error.args[0]
        else:
            return None
        finally:
            await _sql("DELETE FROM capability WHERE `key` = 'zz_sin_modo'")

    assert client.portal.call(correr) == 1364  # ER_NO_DEFAULT_FOR_FIELD


def test_control_un_insert_con_modo_invalido_falla(client):
    """CONTROL del CHECK (spec §0 v3): un modo fuera de {'read_only',
    'mutating'} no entra."""
    async def correr():
        try:
            await _sql(
                "INSERT INTO capability (`key`, risk_level, max_execution_minutes, allowed_callers, mode) "
                "VALUES ('zz_modo_invalido', 'low', 5, '[]', 'escritura')"
            )
        except pymysql.err.MySQLError as error:
            return error.args[0]
        else:
            return None
        finally:
            await _sql("DELETE FROM capability WHERE `key` = 'zz_modo_invalido'")

    assert client.portal.call(correr) == 4025  # CONSTRAINT `chk_capability_mode` failed


def test_una_base_vieja_queda_rellenada_y_not_null(client):
    """Producción hoy (base sin columna, CI, bases viejas): run_migrations()
    la agrega NULL sin CHECK, rellena desde _CAPABILITY_MODE, la pasa a
    VARCHAR(16) NOT NULL y agrega el CHECK (se afirma también acá: la
    mutación (d) -- quitar el paso que agrega el CHECK -- tiene que caer
    en ESTE test, no solo en el de la columna ENUM de abajo).

    El `DROP CONSTRAINT` antes del `DROP COLUMN` es explícito a propósito:
    verificado (2026-09-14, MariaDB 12.3.3, `jax_memory_test`) que
    `DROP COLUMN mode` por sí solo YA arrastra el CHECK que lo referencia
    -- no hace falta soltarlo antes para que el DDL no falle. Se deja
    explícito para no depender de ese comportamiento implícito (el brief
    pedía declarar el porqué si el DROP chocaba; acá no choca, pero
    dejarlo así documenta la intención real: reproducir una base vieja SIN
    columna Y SIN CHECK, no confiar en un efecto colateral del motor)."""
    async def correr():
        await _sql("ALTER TABLE capability DROP CONSTRAINT chk_capability_mode")
        await _sql("ALTER TABLE capability DROP COLUMN mode")
        try:
            await migrations.run_migrations()
            return (
                await _sql(_INFO_MODE),
                await _sql(_INFO_CHECK),
                dict(await _sql("SELECT `key`, mode FROM capability")),
            )
        finally:
            # Si algo de arriba (incluido run_migrations()) falla a mitad de
            # camino, la base de la SESIÓN quedaría con la columna a medio
            # reponer para el resto de los tests. run_migrations() es
            # idempotente: repetirla acá no hace daño si ya salió bien, y
            # repara si no.
            await migrations.run_migrations()

    info, check, filas = client.portal.call(correr)
    assert info == (("NO", None, "varchar(16)"),)
    assert len(check) == 1
    assert {k: filas.get(k) for k in migrations._CAPABILITY_MODE} == migrations._CAPABILITY_MODE


def test_la_columna_enum_de_produccion_queda_convertida(client):
    """El estado de producción de hoy (incidente 2026-09-14, ver spec §0
    v3): la columna quedó como ENUM('read_only','mutating') NOT NULL, SIN
    CHECK. run_migrations() la deja varchar(16)/NOT NULL, con el CHECK y
    los 17 valores intactos (un MODIFY de ENUM a VARCHAR conserva los
    valores).

    El `DROP CONSTRAINT` antes del `MODIFY` es necesario acá por una razón
    distinta a la del test de arriba: verificado que el `MODIFY COLUMN ...
    ENUM(...) NOT NULL` NO choca con el CHECK si se lo deja puesto (conviven
    sin error). Pero el incidente real que este test reproduce (spec §0)
    dejó la columna SIN CHECK -- si no se lo sacara acá, seguiría presente
    al entrar a `run_migrations()` y el test no ejercitaría el paso (c)
    (agregar el CHECK que falta): la aserción `len(check) == 1` de abajo
    sería un no-op, verde incluso si el paso (c) estuviera roto."""
    async def correr():
        await _sql("ALTER TABLE capability DROP CONSTRAINT chk_capability_mode")
        await _sql("ALTER TABLE capability MODIFY COLUMN mode ENUM('read_only','mutating') NOT NULL")
        try:
            await migrations.run_migrations()
            return (
                await _sql(_INFO_MODE),
                await _sql(_INFO_CHECK),
                dict(await _sql("SELECT `key`, mode FROM capability")),
            )
        finally:
            # Misma razón que en test_una_base_vieja_...: dejar la base de
            # la sesión reparada aunque algo de arriba falle a mitad.
            await migrations.run_migrations()

    info, check, filas = client.portal.call(correr)
    assert info == (("NO", None, "varchar(16)"),)
    assert len(check) == 1
    assert {k: filas.get(k) for k in migrations._CAPABILITY_MODE} == migrations._CAPABILITY_MODE


def test_una_capability_no_sembrada_sin_modo_frena_la_migracion(client):
    """Una fila que ninguna migración sembró (SQL a mano) no recibe un modo
    inventado: la migración falla con su nombre y jax-platform no arranca."""
    async def correr():
        await _sql("ALTER TABLE capability MODIFY COLUMN mode VARCHAR(16) NULL")
        await _sql(
            "INSERT INTO capability (`key`, risk_level, max_execution_minutes, allowed_callers) "
            "VALUES ('zz_huerfana', 'low', 5, '[]')"
        )
        try:
            await migrations.run_migrations()
        except RuntimeError as error:
            resultado = str(error)
        else:
            resultado = None
        finally:
            await _sql("DELETE FROM capability WHERE `key` = 'zz_huerfana'")
            await migrations.run_migrations()
        return resultado, await _sql(_INFO_MODE)

    resultado, info = client.portal.call(correr)
    assert resultado is not None and "zz_huerfana" in resultado
    assert info == (("NO", None, "varchar(16)"),)


def test_un_modo_invalido_sin_check_frena_la_migracion_con_su_nombre(client):
    """Si el CHECK todavía no existe (una base a medio migrar) y una fila
    tiene un `mode` fuera de {'read_only','mutating'} metido a mano en la
    columna VARCHAR (que sin CHECK no rechaza nada), el `ADD CONSTRAINT`
    fallaría con el error 4025 SIN decir cuál fila. `_asegurar_forma_de_
    capability_mode` la revisa antes y declara su nombre (mismo criterio
    que la fila huérfana del paso (a))."""
    async def correr():
        await _sql("ALTER TABLE capability DROP CONSTRAINT chk_capability_mode")
        await _sql(
            "INSERT INTO capability (`key`, risk_level, max_execution_minutes, allowed_callers, mode) "
            "VALUES ('zz_modo_raro', 'low', 5, '[]', 'escritura')"
        )
        try:
            await migrations.run_migrations()
        except RuntimeError as error:
            resultado = str(error)
        else:
            resultado = None
        finally:
            await _sql("DELETE FROM capability WHERE `key` = 'zz_modo_raro'")
            await migrations.run_migrations()
        return resultado, await _sql(_INFO_CHECK)

    resultado, check = client.portal.call(correr)
    assert resultado is not None and "zz_modo_raro" in resultado
    assert len(check) == 1
