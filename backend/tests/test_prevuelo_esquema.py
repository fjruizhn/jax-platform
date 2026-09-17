"""Esquema que el pre-vuelo de Jacobs necesita (spec 2026-09-17 §4.4 y §4.5).
El DDL de `capability` y de `facet_health_event` vive en este repo; Jacobs
(plan J) lee `min_output_tokens` y escribe `source='preflight'`. Los puros
corren sin DB; los que piden `client` van contra jax_memory_test."""
import re

import facet_health
from db import migrations as m
from tests.identidades import sql


def test_el_escritor_conoce_el_source_preflight():
    assert False, 'CANARIO: rojo a propósito, se revierte en el commit siguiente'
    assert facet_health.SOURCE_PREFLIGHT == "preflight"
    assert "preflight" in facet_health.SOURCES


def test_el_ENUM_de_source_coincide_exacto_con_SOURCES():
    """Mismo cierre mecánico que outcome (test_facet_health_outcomes.py): el
    ENUM de la DB y el frozenset no se derivan uno del otro sin una base."""
    valores = set(re.findall(r"'([a-z_]+)'",
                             re.search(r"source\s+ENUM\(([^)]+)\)", m.CREATE_FACET_HEALTH_EVENT).group(1)))
    for tabla, columna, valor, _ddl in m._ENUM_EXTENSIONS:
        if tabla == "facet_health_event" and columna == "source":
            valores.add(valor)
    assert valores == facet_health.SOURCES


def test_la_columna_min_output_tokens_esta_declarada():
    assert ("capability", "min_output_tokens",
            "ALTER TABLE capability ADD COLUMN min_output_tokens INT NOT NULL DEFAULT 0") in m._COLUMNS


def test_min_output_tokens_es_int_not_null_default_0(client):
    filas = client.portal.call(
        sql,
        "SELECT DATA_TYPE, IS_NULLABLE, COLUMN_DEFAULT FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'capability' AND COLUMN_NAME = 'min_output_tokens'",
        (), True)
    assert filas == (("int", "NO", "0"),)


async def _insertar_y_leer_preflight():
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            try:
                await cur.execute(
                    "INSERT INTO facet_health_event (facet, outcome, source, detail, ts) "
                    "VALUES ('test-prevuelo-esquema', 'ok', 'preflight', NULL, 1)")
            except Exception as exc:  # fail-soft: el test devuelve el error para afirmar AFUERA del portal (conftest._envolver_portal_call)
                return repr(exc)
            fila_id = cur.lastrowid
            await cur.execute("SELECT source FROM facet_health_event WHERE id = %s", (fila_id,))
            fila = await cur.fetchone()
            await cur.execute("DELETE FROM facet_health_event WHERE id = %s", (fila_id,))
    return fila


def test_facet_health_event_acepta_source_preflight(client):
    assert client.portal.call(_insertar_y_leer_preflight) == ("preflight",)
