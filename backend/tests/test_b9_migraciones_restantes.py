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
GLOB, contra un MANIFIESTO (`backend/b9_migraciones_en_produccion.json`) -- no una
lista fija ni confianza ciega en lo que el glob encuentre (corrección de la auditoría
adversarial del PR #164, MAJOR-2: un `.sql` nuevo en el repo de JAX no prueba por sí
solo que ya pasó por el flujo revisado de producción). Este archivo prueba el
mecanismo en sí -- que las 34 pruebas de arriba ahora pasen es la prueba de
integración; medido aparte (ver el informe de esta rama), no repetido acá.
"""
from __future__ import annotations

import asyncio
import inspect

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


def test_las_ya_cubiertas_coinciden_con_lo_que_run_migrations_realmente_lee():
    """Corrección de la auditoría adversarial del PR #164 (MINOR-5): la versión
    anterior de este test comparaba la constante contra otra lista escrita a mano acá
    mismo -- tautológico, dos copias de la misma opinión que se mueven juntas sin
    decir nada sobre el código real. 001 y 002 se derivan del CÓDIGO FUENTE de
    `_jax_b9_core_migration_statements()` (la función que `run_migrations()` usa de
    verdad): si algún día esa función deja de leer uno de los dos archivos, este test
    lo nota sin que nadie edite las dos listas a la vez. 003 no se lee de un archivo
    en `run_migrations()` -- se aplica vía el hook Python `project_authority_migrations.py`
    (una copia deliberada de `003_project_scope_authority.sql`, ver el docstring de
    `_apply_jax_project_authority_migration`); ese contenido ya lo prueba, statement
    por statement, `tests/test_project_authority_migration.py` -- acá sólo se comprueba
    que la excepción sigue declarada, sin repetir esa prueba."""
    fuente_core = inspect.getsource(db_migrations._jax_b9_core_migration_statements)
    for archivo in ("001_b9_shared_memory.sql", "002_b9_hardening.sql"):
        assert archivo in fuente_core, (
            f"{archivo} ya no aparece en _jax_b9_core_migration_statements(): "
            "actualizar _MIGRACIONES_B9_YA_CUBIERTAS_POR_RUN_MIGRATIONS"
        )

    assert _MIGRACIONES_B9_YA_CUBIERTAS_POR_RUN_MIGRATIONS == {
        "001_b9_shared_memory.sql",
        "002_b9_hardening.sql",
        "003_project_scope_authority.sql",
    }


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


def test_el_manifiesto_committeado_declara_exactamente_004_y_006():
    """El manifiesto real del repo (no uno de prueba): hoy declara 004 y 006, ni de
    más ni de menos, porque eso es exactamente lo que `run_migrations()` no cubre y
    lo que `jax_memory` de producción ya tiene aplicado (ver el informe de esta
    rama). Un cambio a este archivo que agregue o saque una clave sin que nadie lo
    note es justo el tipo de deriva silenciosa que este test atrapa."""
    manifiesto = modulo_base_de_test._manifiesto_b9_de_produccion()
    assert set(manifiesto) == {"004_tenant_legacy_binding.sql", "006_memory_jobs.sql"}
    for archivo, datos in manifiesto.items():
        assert datos["aplicada_en_produccion"], archivo
        assert datos["por"], archivo
        assert datos["verificado"], archivo
