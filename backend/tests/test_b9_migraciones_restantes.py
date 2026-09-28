"""La base de tests espeja la cadena B9 completa de JAX, no sólo 001-003.

Causa raíz (2026-09-27): `db.migrations.run_migrations()` sólo aplica 001/002
(`_apply_jax_b9_core_migrations`) y 003 (`_apply_jax_project_authority_migration`) --
decisión de diseño correcta, documentada en el README de
`jax/memory/b9_migrations/`: esa cadena "is not executed by application import or
worker startup". En PRODUCCIÓN (`jax_memory`) 004 y 006 ya estaban aplicadas por fuera
de ese flujo (verificado con `SHOW COLUMNS`: `memory_revisions.tenant_id` existe), pero
ninguna base de TEST las tenía. `jax.memory.b9_mariadb._retrieve_scoped` (tras jax#279)
lee `r.tenant_id` de `memory_revisions`, así que cualquier turno de chat real en la
suite reventaba con `pymysql.err.OperationalError: (1054, "Unknown column
'r.tenant_id'")`, envuelto por `api/chat.py` en un 503 `MEMORY_UNAVAILABLE` -- medido
en esta rama, contra el código anterior a este cambio: 34 tests en rojo (chat,
adjuntos, facet wiring, shadow), los 34 con ese mismo código.

`aplicar_migraciones_b9_restantes()` (backend/base_de_test.py) es el arreglo: por
GLOB, contra un MANIFIESTO de NOMBRE + CONTENIDO (`backend/b9_migraciones_en_produccion.json`)
-- no una lista fija ni confianza ciega en lo que el glob encuentre. Dos rondas de
auditoría adversarial (PR #164): MAJOR-2 (un `.sql` nuevo en el repo de JAX no prueba
por sí solo que ya pasó por el flujo revisado de producción -- hace falta un nombre
declarado) y MAJOR-A (declarar el nombre tampoco alcanza -- si el contenido cambia
después de declararlo, nadie se entera sin comparar el `sha256`). Este archivo prueba
el mecanismo en sí -- que las 34 pruebas de arriba ahora pasen es la prueba de
integración; medido aparte (ver el informe de esta rama), no repetido acá.
"""
from __future__ import annotations

import asyncio

import pytest

import base_de_test as modulo_base_de_test
from base_de_test import (
    BaseDeTestInvalida,
    _MIGRACIONES_B9_YA_CUBIERTAS_POR_RUN_MIGRATIONS,
    aplicar_migraciones_b9_restantes,
)
from db import migrations as db_migrations
from db.migrations import _jax_b9_migration_root


def test_glob_descubre_004_y_006_sin_lista_fija():
    """El descubrimiento es por GLOB del directorio real de JAX, no una lista a mano
    acá: si 004/006 desaparecieran de `_MIGRACIONES_B9_YA_CUBIERTAS_POR_RUN_MIGRATIONS`
    (donde NO deben estar -- ver el docstring de esa constante) o si JAX agregara un
    007, este test los ve sin que nadie edite este archivo."""
    directorio = _jax_b9_migration_root()
    encontrados = {p.name for p in directorio.glob("*.sql")}

    assert "004_tenant_legacy_binding.sql" in encontrados
    assert "006_memory_jobs.sql" in encontrados
    assert "004_tenant_legacy_binding.sql" not in _MIGRACIONES_B9_YA_CUBIERTAS_POR_RUN_MIGRATIONS
    assert "006_memory_jobs.sql" not in _MIGRACIONES_B9_YA_CUBIERTAS_POR_RUN_MIGRATIONS


def test_las_ya_cubiertas_son_exactamente_estas_tres():
    """Guardia de deriva simple: un cambio a esta constante (agregar o sacar un
    archivo) sin que nadie lo note. La prueba de que son las CORRECTAS -- lo que
    `run_migrations()` realmente lee -- es de COMPORTAMIENTO, en los tres tests de
    abajo (corrección de la auditoría adversarial del PR #164, MINOR-C: la versión
    anterior de este test derivaba 001/002 de `inspect.getsource(...)`, que prueba que
    el NOMBRE aparece en el texto fuente, no que la función realmente lo USA -- dos
    funciones podrían mencionar un nombre en un comentario y seguir pasando ese
    control sin leer el archivo de verdad)."""
    assert _MIGRACIONES_B9_YA_CUBIERTAS_POR_RUN_MIGRATIONS == {
        "001_b9_shared_memory.sql",
        "002_b9_hardening.sql",
        "003_project_scope_authority.sql",
    }


def test_core_sin_002_revienta_nombrandolo(tmp_path, monkeypatch):
    """Prueba de COMPORTAMIENTO (MINOR-C): con SÓLO 001 presente en el directorio,
    `_jax_b9_core_migration_statements()` -- la función que `run_migrations()` corre
    de verdad, no una copia -- revienta nombrando `002_b9_hardening.sql`. Si esa
    función alguna vez dejara de pedir 002, este test (que no sabe nada del código
    fuente, sólo del resultado) lo notaría al dejar de fallar donde se espera."""
    (tmp_path / "001_b9_shared_memory.sql").write_text("SELECT 1;\n", encoding="utf-8")
    monkeypatch.setattr(db_migrations, "_jax_b9_migration_root", lambda: tmp_path)

    with pytest.raises(RuntimeError, match="002_b9_hardening.sql"):
        db_migrations._jax_b9_core_migration_statements()


def test_core_con_001_y_002_ignora_cualquier_otro_sql_del_directorio(tmp_path, monkeypatch):
    """Con 001+002 válidos Y un tercer `.sql` cuyo contenido reventaría el parser si
    se leyera (un `DELIMITER` sin cerrar -- ver `_split_jax_b9_sql`), la función NO
    revienta: prueba, por comportamiento, que lee EXACTAMENTE esos dos nombres, nunca
    "todo lo que haya en el directorio" (que es justo el error que
    `aplicar_migraciones_b9_restantes` sí comete a propósito, contra el manifiesto)."""
    (tmp_path / "001_b9_shared_memory.sql").write_text("SELECT 1;\n", encoding="utf-8")
    (tmp_path / "002_b9_hardening.sql").write_text("SELECT 2;\n", encoding="utf-8")
    (tmp_path / "999_no_deberia_leerse.sql").write_text(
        "DELIMITER //\nSELECT 3\n", encoding="utf-8"
    )
    monkeypatch.setattr(db_migrations, "_jax_b9_migration_root", lambda: tmp_path)

    statements = db_migrations._jax_b9_core_migration_statements()

    assert statements == ("SELECT 1", "SELECT 2")


def test_project_authority_003_se_cubre_sin_leer_el_sql_de_esa_carpeta(tmp_path, monkeypatch):
    """Prueba de COMPORTAMIENTO de por qué 003 está en
    `_MIGRACIONES_B9_YA_CUBIERTAS_POR_RUN_MIGRATIONS` aunque `run_migrations()` nunca
    abre `003_project_scope_authority.sql`: lo que cubre a 003 es el hook Python
    `project_authority_migrations.py`, cargado por RUTA ABSOLUTA
    (`<JAX_REPO_PATH>/jax/memory/project_authority_migrations.py`), no por leer nada
    de `b9_migrations/`. Se arma un directorio SIN ese `.sql` -- sólo con 001/002 y el
    hook -- y se confirma que `_apply_jax_project_authority_migration` igual corre. No
    prueba que el CONTENIDO del hook coincida con el de `003_project_scope_authority.sql`
    (eso ya lo hace `tests/test_project_authority_migration.py`, statement por
    statement) -- sólo que su ejecución no depende de que ese archivo exista."""
    raiz = tmp_path
    migraciones = raiz / "jax" / "memory" / "b9_migrations"
    migraciones.mkdir(parents=True)
    (migraciones / "001_b9_shared_memory.sql").write_text("SELECT 1;\n", encoding="utf-8")
    (migraciones / "002_b9_hardening.sql").write_text("SELECT 2;\n", encoding="utf-8")
    # SIN 003_project_scope_authority.sql a propósito.
    (raiz / "jax" / "memory" / "project_authority_migrations.py").write_text(
        "async def apply_project_authority_migration(cursor):\n"
        "    await cursor.execute('SELECT 999')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(db_migrations, "_jax_b9_migration_root", lambda: migraciones)

    class _CursorGrabador:
        def __init__(self):
            self.statements = []

        async def execute(self, statement):
            self.statements.append(statement)

    cursor = _CursorGrabador()
    asyncio.run(db_migrations._apply_jax_project_authority_migration(cursor))

    assert cursor.statements == ["SELECT 999"]


async def _estado_de_la_cadena_b9(pool):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT COLUMN_NAME, IS_NULLABLE FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='memory_revisions' "
                "AND COLUMN_NAME='tenant_id'"
            )
            columna_tenant = await cur.fetchone()
            await cur.execute(
                "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='memory_legacy_bindings' "
                "AND COLUMN_NAME='tenant_id'"
            )
            columna_legacy = await cur.fetchone()
            await cur.execute(
                "SELECT CONSTRAINT_NAME FROM information_schema.TABLE_CONSTRAINTS "
                "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='memory_revisions' "
                "AND CONSTRAINT_NAME='fk_memory_revision_tenant'"
            )
            fk = await cur.fetchone()
            await cur.execute(
                "SELECT TABLE_NAME FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME IN "
                "('memory_extraction_jobs','memory_extraction_results',"
                "'memory_synthesis_jobs','memory_synthesis_job_items')"
            )
            tablas_006 = {row[0] for row in await cur.fetchall()}
    return columna_tenant, columna_legacy, fk, tablas_006


def test_memory_revisions_y_legacy_bindings_espejan_la_cadena_completa(client):
    """Contra la base real de la sesión (el fixture `client` ya corrió
    `aplicar_migraciones_b9_restantes` al arrancar): 004 y 006 quedaron aplicadas,
    con la misma forma final que describe cada archivo -- `tenant_id` NOT NULL en
    `memory_revisions`, la FK compuesta hacia `memory_objects`, y las cuatro tablas
    de `memory_extraction_jobs`/`memory_synthesis_jobs` que 006 crea."""
    from db.connection import get_pool

    pool = client.portal.call(get_pool)
    columna_tenant, columna_legacy, fk, tablas_006 = client.portal.call(
        _estado_de_la_cadena_b9, pool)

    assert columna_tenant == ("tenant_id", "NO")
    assert columna_legacy == ("tenant_id",)
    assert fk == ("fk_memory_revision_tenant",)
    assert tablas_006 == {
        "memory_extraction_jobs",
        "memory_extraction_results",
        "memory_synthesis_jobs",
        "memory_synthesis_job_items",
    }


async def _contar_marcas(pool, archivo):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT COUNT(*) FROM _test_b9_migraciones_aplicadas WHERE archivo=%s",
                (archivo,),
            )
            return (await cur.fetchone())[0]


def test_segunda_corrida_es_idempotente_no_revienta_trigger_ni_constraint(client):
    """Ni 004 ni 006 son idempotentes de punta a punta (ver el docstring de
    `CREATE_TABLA_MIGRACIONES_B9_DE_TEST` en `base_de_test.py`: `ADD COLUMN` sin
    `IF NOT EXISTS`, `DROP PRIMARY KEY`/`DROP INDEX` que asumen que lo que borran
    sigue ahí, `CREATE TRIGGER`/`ADD CONSTRAINT` sin `IF NOT EXISTS`). Sin el
    registro de qué migración ya corrió, una segunda pasada por el mismo archivo --
    exactamente lo que pasa en una base de test REUSADA (`JAX_TEST_DB_SUFIJO`
    explícito, ver `base_de_test.py`) -- fallaría. Este test corre la función una
    SEGUNDA vez sobre la base que el fixture `client` ya dejó lista y comprueba que
    ninguna marca se duplica."""
    from db.connection import get_pool

    pool = client.portal.call(get_pool)

    client.portal.call(aplicar_migraciones_b9_restantes)

    assert client.portal.call(_contar_marcas, pool, "004_tenant_legacy_binding.sql") == 1
    assert client.portal.call(_contar_marcas, pool, "006_memory_jobs.sql") == 1


def test_no_corre_contra_una_base_que_no_es_de_test(monkeypatch):
    """Defensa en profundidad, mismo criterio que `exigir_base_de_test()`: aunque hoy
    nada debería poder llamar a esta función con `JAX_DB_NAME=jax_memory` puesto, el
    control no está para el camino que hoy se ve imposible.

    Corrección de la auditoría adversarial del PR #164 (MAJOR-1): la versión
    anterior de este test decía en su docstring "revienta antes de importar
    `db.connection`" -- FALSO: el `import` está al principio del cuerpo de la
    función y Python lo ejecuta igual (sólo el uso del nombre importado queda
    después de la guarda). Ese test corría con las credenciales REALES de
    `/etc/jax/.env` que `tests/conftest.py` ya cargó al arrancar la sesión: si
    `es_base_de_test()` tuviera un bug, este mismo test hubiera sido el primero en
    aplicar DDL de JAX contra `jax_memory` de PRODUCCIÓN. Ahora el test no confía
    sólo en la guarda: `db.connection.get_pool` queda reemplazado por algo que hace
    fallar el test EXPLÍCITAMENTE si algo intenta usarlo, y `JAX_DB_HOST`/
    `JAX_DB_PORT` apuntan a un destino que no escucha nada -- ni un bug en la guarda
    llegaría a un socket real.

    Mutación verificada a mano (no queda en el repo): comentar el
    `if not es_base_de_test(nombre): raise ...` de `aplicar_migraciones_b9_restantes()`
    pone a ESTE test en rojo por el motivo correcto -- el `pytest.fail` de
    `_no_deberia_llamarse`, no un error de conexión ni un DDL real -- confirmando que
    la red de seguridad (y no sólo la guarda) es lo que este test certifica."""
    from db import connection as db_connection

    async def _no_deberia_llamarse(*args, **kwargs):
        pytest.fail(
            "aplicar_migraciones_b9_restantes() intentó abrir un pool de conexión "
            "pese a que JAX_DB_NAME no es una base de tests -- si ves esto, "
            "es_base_de_test() dejó de frenar a tiempo."
        )

    monkeypatch.setattr(db_connection, "get_pool", _no_deberia_llamarse)
    # Puerto que no escucha nada en ningún host (ver conftest.py:
    # DESTINO_DE_SERVICIO_INVALIDO usa el mismo criterio con :9): si el monkeypatch
    # de arriba fallara en enganchar, esto es la segunda barrera, no la primera.
    monkeypatch.setenv("JAX_DB_HOST", "127.0.0.1")
    monkeypatch.setenv("JAX_DB_PORT", "1")
    monkeypatch.setenv("JAX_DB_NAME", "jax_memory")

    with pytest.raises(BaseDeTestInvalida, match="jax_memory"):
        asyncio.run(aplicar_migraciones_b9_restantes())


def test_un_archivo_b9_no_declarado_en_el_manifiesto_revienta_sin_tocar_la_db(
    tmp_path, monkeypatch
):
    """MAJOR-2 de la auditoría adversarial del PR #164: que un `.sql` exista en el
    repo de JAX no prueba que ya pasó por el flujo revisado de producción. Un
    archivo nuevo (acá simulado con `007_extra_no_declarada.sql`) que el glob
    encuentra y que NADIE declaró en `backend/b9_migraciones_en_produccion.json`
    tiene que reventar con un mensaje que nombre exactamente ese archivo -- antes de
    abrir ninguna conexión (`db.connection.get_pool` queda igual de vigilado que en
    el test de arriba)."""
    from db import connection as db_connection

    directorio = tmp_path / "b9_migrations"
    directorio.mkdir()
    for nombre in (
        "004_tenant_legacy_binding.sql",
        "006_memory_jobs.sql",
        "007_extra_no_declarada.sql",
    ):
        (directorio / nombre).write_text("SELECT 1;", encoding="utf-8")

    async def _no_deberia_llamarse(*args, **kwargs):
        pytest.fail("intentó abrir un pool de conexión antes de validar el manifiesto")

    monkeypatch.setattr(db_connection, "get_pool", _no_deberia_llamarse)
    monkeypatch.setattr(db_migrations, "_jax_b9_migration_root", lambda: directorio)
    monkeypatch.setattr(
        modulo_base_de_test,
        "_manifiesto_b9_de_produccion",
        lambda: {"004_tenant_legacy_binding.sql": {}, "006_memory_jobs.sql": {}},
    )
    monkeypatch.setenv("JAX_DB_NAME", "jax_memory_test_zz_manifiesto")

    with pytest.raises(BaseDeTestInvalida, match="007_extra_no_declarada.sql"):
        asyncio.run(aplicar_migraciones_b9_restantes())


def test_un_archivo_declarado_con_contenido_distinto_revienta_sin_tocar_la_db(
    tmp_path, monkeypatch
):
    """MAJOR-A de la auditoría adversarial del PR #164 (ronda 2): el manifiesto
    declara CONTENIDO, no sólo nombre. Simula `004_tenant_legacy_binding.sql` con un
    contenido que YA NO coincide con el `sha256` que un humano verificó contra
    producción (declarado acá con un hash inventado) -- tiene que reventar nombrando
    ESE archivo, sin conectarse a nada, mientras que `006` (con su hash real)
    no dispara nada por sí solo."""
    from db import connection as db_connection

    directorio = tmp_path / "b9_migrations"
    directorio.mkdir()
    (directorio / "004_tenant_legacy_binding.sql").write_text(
        "ALTER TABLE memory_legacy_bindings ADD COLUMN algo_que_nadie_revisó INT;",
        encoding="utf-8",
    )
    (directorio / "006_memory_jobs.sql").write_text("SELECT 1;\n", encoding="utf-8")

    async def _no_deberia_llamarse(*args, **kwargs):
        pytest.fail("intentó abrir un pool de conexión antes de validar el contenido")

    monkeypatch.setattr(db_connection, "get_pool", _no_deberia_llamarse)
    monkeypatch.setattr(db_migrations, "_jax_b9_migration_root", lambda: directorio)
    monkeypatch.setattr(
        modulo_base_de_test,
        "_manifiesto_b9_de_produccion",
        lambda: {
            "004_tenant_legacy_binding.sql": {"sha256": "0" * 64},  # deliberadamente falso
            "006_memory_jobs.sql": {
                "sha256": modulo_base_de_test._sha256_de_archivo(
                    directorio / "006_memory_jobs.sql"
                )
            },
        },
    )
    monkeypatch.setenv("JAX_DB_NAME", "jax_memory_test_zz_hash")

    with pytest.raises(BaseDeTestInvalida, match="004_tenant_legacy_binding.sql"):
        asyncio.run(aplicar_migraciones_b9_restantes())


async def _leer_hash_guardado(pool, archivo):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT sha256 FROM _test_b9_migraciones_aplicadas WHERE archivo=%s",
                (archivo,),
            )
            fila = await cur.fetchone()
            return fila[0] if fila else None


async def _forzar_hash_guardado(pool, archivo, sha256):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE _test_b9_migraciones_aplicadas SET sha256=%s WHERE archivo=%s",
                (sha256, archivo),
            )
        await conn.commit()


async def _borrar_marca_sintetica(pool, archivo):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "DELETE FROM _test_b9_migraciones_aplicadas WHERE archivo=%s",
                (archivo,),
            )
        await conn.commit()


async def _ejercicio_hash_sintetico(archivo, sha_correcto):
    """Todo en UN solo event loop (un solo `asyncio.run()` desde el test): `get_pool()`
    cachea un pool POR LOOP (ver `db/connection.py`), así que abrir uno acá y leerlo
    con otro `asyncio.run()` distinto sería cruzar loops -- exactamente el defecto que
    ese módulo ya documenta haber cerrado. `close_pool()` al final limpia el pool
    propio de este test (no el de la sesión: `client` ni se pide acá)."""
    from db.connection import close_pool, get_pool

    try:
        await aplicar_migraciones_b9_restantes()
        pool = await get_pool()
        guardado = await _leer_hash_guardado(pool, archivo)
        assert guardado == sha_correcto, (
            "la primera corrida debería haber insertado la fila sintética con su "
            "hash real"
        )

        await _forzar_hash_guardado(pool, archivo, "f" * 64)
        with pytest.raises(BaseDeTestInvalida, match=archivo):
            await aplicar_migraciones_b9_restantes()
    finally:
        pool = await get_pool()
        await _borrar_marca_sintetica(pool, archivo)
        await close_pool()


def test_un_hash_guardado_distinto_deja_la_base_invalida_para_ese_archivo(
    tmp_path, monkeypatch
):
    """MAJOR-A, la otra mitad: no sólo el archivo de origen puede mentir -- la base de
    tests puede tener una fila de una corrida anterior con otro contenido (sea porque
    el manifiesto cambió después, sea una fila vieja de antes de que la columna
    `sha256` existiera).

    Corrección de la auditoría adversarial del PR #164 (MINOR-3, ronda 3): la versión
    anterior de este test pedía el fixture `client` y pisaba a mano el hash guardado
    de `004_tenant_legacy_binding.sql` -- la fila REAL que ese fixture, session-scoped,
    ya aplicó para el RESTO de la suite. El `finally` restauraba el valor, así que
    dentro de ese test no había ventana de corrupción (Python no interfoliza dos
    tests), pero el docstring anterior sobrevendía la garantía: decía que eso "no
    dejaba la base corrompida para el resto de la suite" sin decir qué pasaba si el
    `finally` no llegaba a correr (una excepción al restaurar, `client` cayendo en el
    medio) -- la fila de 004 vive en la base COMPARTIDA por TODA la sesión, así que
    cualquier otro test que pidiera `client` (la mayoría de la suite) hubiera visto esa
    fila corrompida y terminado en ERROR de fixture, no en un fallo limpio y acotado a
    este test.

    Ahora se usa un archivo SINTÉTICO (`999_prueba_hash_sintetica.sql`, que no es
    ninguna migración real de JAX y no aparece en
    `_MIGRACIONES_B9_YA_CUBIERTAS_POR_RUN_MIGRATIONS`) con su propia fila en
    `_test_b9_migraciones_aplicadas`: nunca se toca la fila de 004 ni de 006, y por
    eso ya no hace falta el fixture `client` -- este test abre su PROPIO pool
    (`_ejercicio_hash_sintetico`), contra la misma base física de la sesión, vía
    `db.connection.get_pool()` directo. La fila sintética se BORRA en un `finally` en
    vez de restaurarse a un valor anterior: si el `finally` no llegara a correr, lo
    peor que queda es una fila huérfana con un nombre que ninguna migración real usa
    jamás -- inofensiva, y que cualquier recreación de la base se lleva puesta."""
    archivo = "999_prueba_hash_sintetica.sql"
    directorio = tmp_path / "b9_migrations"
    directorio.mkdir()
    (directorio / archivo).write_text("SELECT 1;\n", encoding="utf-8")
    sha_correcto = modulo_base_de_test._sha256_de_archivo(directorio / archivo)

    monkeypatch.setattr(db_migrations, "_jax_b9_migration_root", lambda: directorio)
    monkeypatch.setattr(
        modulo_base_de_test,
        "_manifiesto_b9_de_produccion",
        lambda: {archivo: {"sha256": sha_correcto}},
    )

    asyncio.run(_ejercicio_hash_sintetico(archivo, sha_correcto))


def test_el_manifiesto_committeado_declara_exactamente_004_y_006():
    """El manifiesto real del repo (no uno de prueba): hoy declara 004 y 006, ni de
    más ni de menos, porque eso es exactamente lo que `run_migrations()` no cubre y
    lo que `jax_memory` de producción ya tiene aplicado (ver el informe de esta
    rama). Un cambio a este archivo que agregue o saque una clave sin que nadie lo
    note es justo el tipo de deriva silenciosa que este test atrapa. El `sha256`
    declarado tiene que coincidir con el archivo REAL del checkout de JAX que usa
    esta sesión (`JAX_REPO_PATH`) -- si no coincide, es la prueba de integración
    (`test_memory_revisions_y_legacy_bindings_espejan_la_cadena_completa`, que pide
    `client`) la que revienta, no ésta, que es intencionalmente sin DB."""
    manifiesto = modulo_base_de_test._manifiesto_b9_de_produccion()
    assert set(manifiesto) == {"004_tenant_legacy_binding.sql", "006_memory_jobs.sql"}
    for archivo, datos in manifiesto.items():
        assert datos["aplicada_en_produccion"], archivo
        assert datos["por"], archivo
        assert datos["verificado"], archivo
        assert datos["sha256_origen"], archivo
        sha256 = datos["sha256"]
        assert isinstance(sha256, str) and len(sha256) == 64, archivo
        assert all(c in "0123456789abcdef" for c in sha256), archivo
