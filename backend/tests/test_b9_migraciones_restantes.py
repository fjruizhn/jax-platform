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
GLOB, no por lista fija, para que una 007 futura de JAX se recoja sola. Este archivo
prueba el mecanismo en sí -- que las 34 pruebas de arriba ahora pasen es la prueba de
integración; medido aparte (ver el informe de esta rama), no repetido acá.
"""
from __future__ import annotations

import asyncio
import os

import pytest

from base_de_test import (
    BaseDeTestInvalida,
    _MIGRACIONES_B9_YA_CUBIERTAS_POR_RUN_MIGRATIONS,
    aplicar_migraciones_b9_restantes,
)
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


def test_las_ya_cubiertas_son_exactamente_las_que_corre_run_migrations():
    """Un cambio silencioso a esta constante (agregar o sacar un archivo) es
    exactamente el tipo de deriva que este test existe para atrapar: si alguien
    la amplía sin querer, una migración de JAX deja de aplicarse en la suite sin
    que nada lo grite hasta que un test de integración revienta lejos de acá."""
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
    """006 trae un `CREATE TRIGGER` y un `ADD CONSTRAINT` SIN `IF NOT EXISTS`
    (verificado contra jax master, 2026-09-27): sin el registro de qué migración ya
    corrió, una segunda pasada por el mismo archivo -- exactamente lo que pasa en
    una base de test REUSADA (`JAX_TEST_DB_SUFIJO` explícito, ver
    `base_de_test.py`) -- fallaría con "trigger ya existe" o "constraint
    duplicada". Este test corre la función una SEGUNDA vez sobre la base que el
    fixture `client` ya dejó lista y comprueba que ninguna marca se duplica."""
    from db.connection import get_pool

    pool = client.portal.call(get_pool)

    client.portal.call(aplicar_migraciones_b9_restantes)

    assert client.portal.call(_contar_marcas, pool, "004_tenant_legacy_binding.sql") == 1
    assert client.portal.call(_contar_marcas, pool, "006_memory_jobs.sql") == 1


def test_no_corre_contra_una_base_que_no_es_de_test(monkeypatch):
    """Defensa en profundidad, mismo criterio que `exigir_base_de_test()`: aunque
    hoy nada debería poder llamar a esta función con `JAX_DB_NAME=jax_memory`
    puesto, el control no está para el camino que hoy se ve imposible. Ni siquiera
    intenta conectarse: revienta antes de importar `db.connection`."""
    monkeypatch.setenv("JAX_DB_NAME", "jax_memory")
    with pytest.raises(BaseDeTestInvalida, match="jax_memory"):
        asyncio.run(aplicar_migraciones_b9_restantes())
