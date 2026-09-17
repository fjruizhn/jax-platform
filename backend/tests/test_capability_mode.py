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
rompe el esquema a propósito, lo hace dentro de `_esquema_roto_con_respaldo`
(respaldo en diario, filas ajenas apartadas, restauración verificada y
recuperación ante un kill; ver el bloque de ese helper).

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

import contextlib
import uuid
import warnings

import pymysql
import pytest

from db import migrations

_INFO_MODE = (
    "SELECT IS_NULLABLE, COLUMN_DEFAULT, COLUMN_TYPE FROM information_schema.COLUMNS "
    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'capability' AND COLUMN_NAME = 'mode'"
)

# Diario persistente del respaldo de `capability` (ver _esquema_roto_con_respaldo).
_DIARIO_PREFIJO = "zz_test_capability_mode_respaldo"
_DIARIO = _DIARIO_PREFIJO
_DIARIO_MOTOR = f"{_DIARIO_PREFIJO}_motor"

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


# ---------------------------------------------------------------------------
# Respaldo de `capability` alrededor de una ruptura de esquema (2026-09-17)
# ---------------------------------------------------------------------------
# `jax_memory_test` es COMPARTIDA en hall9000 (varias sesiones y ramas a la
# vez) y `jax_user` no puede crear otra base. El DDL de columna toca TODAS las
# filas, y run_migrations() sólo sabe rellenar las claves de SU
# _CAPABILITY_MODE: una fila que sembró otra rama (p.ej. `delegate`) quedaba
# con `mode` NULL tras el DROP COLUMN y la migración abortaba para todos
# (503 en todo pre-vuelo de jax, `client` roto en cualquier suite).
#
# Diseño:
#  1. Antes del primer DDL destructivo se copian TODAS las filas de
#     `capability` al diario `_DIARIO` (con la marca `ajena`), y las filas de
#     `capability_motor` de las ajenas a `_DIARIO_MOTOR`. `_DIARIO` se crea
#     ÚLTIMO: su existencia es la señal de "hay algo que restaurar".
#  2. Las ajenas se APARTAN (se borran de `capability_motor` y `capability`
#     en una sola transacción): la migración bajo prueba ve sólo lo que el
#     test controla.
#  3. Al salir (o al arrancar el próximo test si un kill no dejó correr el
#     `finally`) `_recuperar_diario_si_existe` repone: migra con lo
#     controlado, reinserta las ajenas y sus motores, repone los modos, vuelve
#     a migrar, VERIFICA contra el diario y recién entonces lo borra.
# Límite declarado: no protege contra otra sesión que siembre filas DURANTE
# la ventana del test (eso sólo lo resuelve una base propia, fuera de alcance).
_FILAS_PROPIAS_DE_LOS_TESTS = ("zz_huerfana", "zz_modo_raro")


async def _tabla_existe(nombre):
    return bool(await _sql(
        "SELECT 1 FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() "
        "AND TABLE_NAME = %s", (nombre,)
    ))


async def _en_transaccion(sentencias):
    """[(sql, args), ...] en UNA transacción (el pool es autocommit)."""
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("START TRANSACTION")
            try:
                for sentencia, args in sentencias:
                    await cur.execute(sentencia, args)
            except BaseException:
                await conn.rollback()
                raise
            await conn.commit()


async def _columnas(tabla):
    filas = await _sql(
        "SELECT COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() "
        "AND TABLE_NAME = %s ORDER BY ORDINAL_POSITION", (tabla,)
    )
    return [fila[0] for fila in filas]


async def _recuperar_diario_si_existe(*, por_kill=True):
    """Restaura `capability` desde el diario si existe. Devuelve True si
    restauró. `por_kill` sólo decide el warning: al arrancar, un diario
    presente significa que una corrida anterior murió sin su `finally`."""
    if not await _tabla_existe(_DIARIO):
        # Un kill entre la copia de motores y la de capability: nada se
        # apartó todavía, la copia parcial sobra.
        await _sql(f"DROP TABLE IF EXISTS {_DIARIO_MOTOR}")
        return False
    if por_kill:
        warnings.warn(
            f"{_DIARIO} existe en {await _nombre_de_base()}: una corrida anterior de "
            "test_capability_mode.py murió a mitad. Se restaura capability desde el diario.",
            stacklevel=2,
        )
    ajenas = [fila[0] for fila in await _sql(f"SELECT `key` FROM {_DIARIO} WHERE ajena")]

    # Si el kill cayó antes de apartar, las ajenas siguen en capability (y
    # quizá sin modo): están completas en el diario, se sacan para migrar.
    borrar = list(_FILAS_PROPIAS_DE_LOS_TESTS) + ajenas
    marcas = ", ".join(["%s"] * len(borrar))
    await _en_transaccion([
        (f"DELETE FROM capability_motor WHERE capability_key IN ({marcas})", borrar),
        (f"DELETE FROM capability WHERE `key` IN ({marcas})", borrar),
    ])
    await migrations.run_migrations()

    comunes = [c for c in await _columnas(_DIARIO) if c in set(await _columnas("capability"))]
    lista = ", ".join(f"`{c}`" for c in comunes)
    sentencias = [(
        f"INSERT INTO capability ({lista}) SELECT {lista} FROM {_DIARIO} WHERE ajena", None
    )]
    if await _tabla_existe(_DIARIO_MOTOR):
        sentencias.append((
            "INSERT IGNORE INTO capability_motor (capability_key, motor_key, priority) "
            f"SELECT capability_key, motor_key, priority FROM {_DIARIO_MOTOR}", None
        ))
    sentencias.append((
        f"UPDATE capability c JOIN {_DIARIO} d ON d.`key` = c.`key` "
        "SET c.mode = d.mode WHERE NOT d.ajena AND d.mode IS NOT NULL", None
    ))
    await _en_transaccion(sentencias)
    await migrations.run_migrations()

    # Verificación ANTES de borrar el diario: cada fila con su modo y cada
    # motor de las ajenas de vuelta. Si falta algo, el diario queda.
    faltan = await _sql(
        f"SELECT d.`key`, d.mode, c.mode FROM {_DIARIO} d "
        "LEFT JOIN capability c ON c.`key` = d.`key` "
        "WHERE c.`key` IS NULL OR NOT (c.mode <=> d.mode)"
    )
    if await _tabla_existe(_DIARIO_MOTOR):
        faltan += await _sql(
            f"SELECT m.capability_key, m.motor_key, NULL FROM {_DIARIO_MOTOR} m "
            "LEFT JOIN capability_motor c ON c.capability_key = m.capability_key "
            "AND c.motor_key = m.motor_key WHERE c.capability_key IS NULL"
        )
    if faltan:
        raise RuntimeError(
            f"La restauración de capability desde {_DIARIO} no cuadra: {faltan}. "
            "El diario NO se borró; restaurar a mano desde él."
        )
    await _sql(f"DROP TABLE IF EXISTS {_DIARIO_MOTOR}")
    await _sql(f"DROP TABLE {_DIARIO}")
    return True


async def _nombre_de_base():
    return (await _sql("SELECT DATABASE()"))[0][0]


@contextlib.asynccontextmanager
async def _esquema_roto_con_respaldo():
    """Envuelve TODA ruptura de esquema de `capability` en estos tests."""
    await _recuperar_diario_si_existe(por_kill=True)
    claves = list(migrations._CAPABILITY_MODE)
    marcas = ", ".join(["%s"] * len(claves))
    await _sql(f"DROP TABLE IF EXISTS {_DIARIO_MOTOR}")
    await _sql(
        f"CREATE TABLE {_DIARIO_MOTOR} AS SELECT * FROM capability_motor "
        f"WHERE capability_key NOT IN ({marcas})", claves,
    )
    await _sql(
        f"CREATE TABLE {_DIARIO} AS SELECT c.*, (c.`key` NOT IN ({marcas})) AS ajena "
        "FROM capability c", claves,
    )
    await _en_transaccion([
        (f"DELETE FROM capability_motor WHERE capability_key NOT IN ({marcas})", claves),
        (f"DELETE FROM capability WHERE `key` NOT IN ({marcas})", claves),
    ])
    try:
        yield
    finally:
        await _recuperar_diario_si_existe(por_kill=False)


@pytest.fixture
def fila_ajena(client):
    """Una capability que sembró OTRA rama (clave fuera de _CAPABILITY_MODE,
    como `delegate`/`integrate` del frente G en `jax_memory_test`, base
    compartida), con su `mode` válido y una fila en capability_motor. Los
    tests que rompen el esquema tienen que dejarla intacta. Siempre se borra
    al final; el run_migrations() del final repara la base si el test la
    dejó a medio migrar."""
    key = f"zz_ajena_{uuid.uuid4().hex[:8]}"

    async def sembrar():
        motor = (await _sql("SELECT `key` FROM motor ORDER BY `key` LIMIT 1"))[0][0]
        await _sql(
            "INSERT INTO capability (`key`, risk_level, max_execution_minutes, allowed_callers, mode) "
            "VALUES (%s, 'low', 5, '[]', 'read_only')", (key,)
        )
        await _sql(
            "INSERT INTO capability_motor (capability_key, motor_key, priority) VALUES (%s, %s, 0)",
            (key, motor),
        )

    async def limpiar():
        await _sql("DELETE FROM capability_motor WHERE capability_key = %s", (key,))
        await _sql("DELETE FROM capability WHERE `key` = %s", (key,))
        await migrations.run_migrations()

    client.portal.call(sembrar)
    try:
        yield key
    finally:
        client.portal.call(limpiar)


async def _estado_fila_ajena(key):
    """(mode, filas en capability_motor) de la fila ajena; None si no está."""
    try:
        fila = await _sql("SELECT mode FROM capability WHERE `key` = %s", (key,))
    except pymysql.err.MySQLError as error:  # fail-soft: la columna puede no existir si el test dejó la base rota; se DEVUELVE el código y la aserción de afuera falla con él
        return ("error", error.args[0])
    motores = await _sql("SELECT COUNT(*) FROM capability_motor WHERE capability_key = %s", (key,))
    return (fila[0][0] if fila else None, motores[0][0])


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


def test_una_base_vieja_queda_rellenada_y_not_null(client, fila_ajena):
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
        async with _esquema_roto_con_respaldo():
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
    assert client.portal.call(_estado_fila_ajena, fila_ajena) == ("read_only", 1)


def test_la_columna_enum_de_produccion_queda_convertida(client, fila_ajena):
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
        async with _esquema_roto_con_respaldo():
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
    assert client.portal.call(_estado_fila_ajena, fila_ajena) == ("read_only", 1)


def test_una_capability_no_sembrada_sin_modo_frena_la_migracion(client, fila_ajena):
    """Una fila que ninguna migración sembró (SQL a mano) no recibe un modo
    inventado: la migración falla con su nombre y jax-platform no arranca."""
    async def correr():
        async with _esquema_roto_con_respaldo():
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
    # La migración bajo prueba ve sólo lo que el test controla: la fila
    # ajena no aparece como huérfana.
    assert fila_ajena not in resultado
    assert client.portal.call(_estado_fila_ajena, fila_ajena) == ("read_only", 1)


def test_un_modo_invalido_sin_check_frena_la_migracion_con_su_nombre(client, fila_ajena):
    """Si el CHECK todavía no existe (una base a medio migrar) y una fila
    tiene un `mode` fuera de {'read_only','mutating'} metido a mano en la
    columna VARCHAR (que sin CHECK no rechaza nada), el `ADD CONSTRAINT`
    fallaría con el error 4025 SIN decir cuál fila. `_asegurar_forma_de_
    capability_mode` la revisa antes y declara su nombre (mismo criterio
    que la fila huérfana del paso (a))."""
    async def correr():
        async with _esquema_roto_con_respaldo():
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
    assert fila_ajena not in resultado
    assert client.portal.call(_estado_fila_ajena, fila_ajena) == ("read_only", 1)


def test_un_diario_dejado_por_un_kill_se_restaura_al_arrancar(client, fila_ajena):
    """Recuperación ante un kill a mitad (sin `finally`): queda el diario con
    la fila ajena apartada y los modos, la fila ajena fuera de `capability` y
    el esquema roto. El arranque del helper repone todo desde el diario, lo
    avisa con un warning y borra el diario sólo después de verificar."""
    async def correr():
        claves = list(migrations._CAPABILITY_MODE)
        marcas = ", ".join(["%s"] * len(claves))
        await _sql(f"DROP TABLE IF EXISTS {_DIARIO_MOTOR}")
        await _sql(f"DROP TABLE IF EXISTS {_DIARIO}")
        await _sql(
            f"CREATE TABLE {_DIARIO_MOTOR} AS SELECT * FROM capability_motor "
            f"WHERE capability_key NOT IN ({marcas})", claves,
        )
        await _sql(
            f"CREATE TABLE {_DIARIO} AS SELECT c.*, (c.`key` NOT IN ({marcas})) AS ajena "
            "FROM capability c", claves,
        )
        await _sql("DELETE FROM capability_motor WHERE capability_key = %s", (fila_ajena,))
        await _sql("DELETE FROM capability WHERE `key` = %s", (fila_ajena,))
        await _sql("ALTER TABLE capability DROP CONSTRAINT chk_capability_mode")
        await _sql("ALTER TABLE capability MODIFY COLUMN mode VARCHAR(16) NULL")
        await _sql("UPDATE capability SET mode = NULL WHERE `key` = 'file_write'")
        try:
            with warnings.catch_warnings(record=True) as avisos:
                warnings.simplefilter("always")
                # El ARRANQUE del helper es el que tiene que reparar: entrar y
                # salir sin tocar nada.
                async with _esquema_roto_con_respaldo():
                    pass
            return (
                [str(a.message) for a in avisos],
                await _sql(f"SHOW TABLES LIKE '{_DIARIO_PREFIJO}%'"),
                await _sql(_INFO_MODE),
                await _sql(_INFO_CHECK),
                dict(await _sql("SELECT `key`, mode FROM capability")),
            )
        finally:
            await _sql(f"DROP TABLE IF EXISTS {_DIARIO_MOTOR}")
            await _sql(f"DROP TABLE IF EXISTS {_DIARIO}")

    avisos, diarios, info, check, filas = client.portal.call(correr)
    assert any(_DIARIO in aviso for aviso in avisos)
    assert diarios == ()
    assert info == (("NO", None, "varchar(16)"),)
    assert len(check) == 1
    assert filas["file_write"] == "mutating"
    assert client.portal.call(_estado_fila_ajena, fila_ajena) == ("read_only", 1)
