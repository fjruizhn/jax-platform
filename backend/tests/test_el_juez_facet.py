# backend/tests/test_el_juez_facet.py
"""`el_juez`: la faceta auditora de C5 que usa el MISMO modelo que el cerebro.

**Por qué (2026-09-20, decisión de Fernando).** Medido con los canarios reales
de C5: ningún auditor de proveedor separado servía. `qwen3:14b` 7/8 a ~250 s por
vuelta, `qwen3.6:35b-a3b` 6/8 a ~286 s, `granite4.2:8b` 2/4 a 412-717 s. El único
que pasa todo es `thot` (6/6, ~10 s), pero es de nube: manda la salida de los
comandos de máquinas con datos de clientes fuera de la casa, que es lo que la
Fase 0 prohibió. El modelo del cerebro, ya cargado en GPU, dio **8/8 con mediana
80 s** y no saca un byte de la casa.

**Por qué SIGUE al cerebro en vez de nombrar un modelo.** El punto es que sea el
mismo modelo que produce, no un modelo concreto: si mañana la Mesa cambia de
motor, el juez tiene que cambiar con ella. Fijar `qwen3.6-mesa-131k` acá crearía
una segunda fuente de verdad que se desincroniza en silencio -- el mismo defecto
que dejó a `depends_on` de `jacobs_steps` existiendo sólo en producción.

**Lo que esto NO hace:** no abre ninguna compuerta. `el_juez` comparte proveedor
con el cerebro, así que el arranque lo sigue RECHAZANDO hasta que Fernando abra
`ejecutor.c5_auditor_admite_mismo_proveedor`, que nace cerrada y cuya apertura
queda en `axioma_config_audit`. Sembrar la faceta y permitir su uso son dos
decisiones distintas, a propósito.
"""
import asyncio
import os
import secrets

import aiomysql
import pytest

from base_de_test import BASE_COMPARTIDA, _parametros_de_conexion, es_base_de_test
from db.connection import close_pool, get_pool
from db.migrations import _EJECUTOR_CONFIG_C5, _seed_el_juez_facet, run_migrations
from db_connect_config import db_connect_timeout_seconds
from tests.identidades import sql

CLAVE_COMPUERTA = "ejecutor.c5_auditor_admite_mismo_proveedor"


async def _correr():
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await _seed_el_juez_facet(cur)
        await conn.commit()


def test_la_compuerta_del_mismo_proveedor_nace_cerrada():
    """Sembrarla abierta convertiría una decisión de Fernando en un default."""
    assert dict(_EJECUTOR_CONFIG_C5)[CLAVE_COMPUERTA] == "false"


def test_la_compuerta_esta_entre_las_claves_sembradas():
    """`eleccion_c5.config_desde_filas` la exige: sin la fila, el Ejecutor no
    arranca (fail-closed). Si no se siembra, el arranque queda roto."""
    assert CLAVE_COMPUERTA in dict(_EJECUTOR_CONFIG_C5)


def test_la_faceta_existe_y_no_es_auto_seleccionable(client):
    """`auto_selectable=FALSE`: el juez no puede aparecer como productor en un
    plan. Si pudiera, produciría y aprobaría lo suyo -- la sala limpia que
    `_con_arbitro` hace cumplir dejaría de tener sentido."""
    client.portal.call(_correr)
    filas = client.portal.call(sql, "SELECT transport, auto_selectable FROM facet WHERE `key`='el_juez'", (), True)
    assert filas, "la faceta el_juez no se sembró"
    transporte, auto = filas[0]
    assert transporte == "ollama"
    assert not auto


def test_el_juez_usa_EL_MISMO_proveedor_y_modelo_que_el_cerebro(client):
    """El corazón de esta rama. Si el binding del juez se separara del cerebro,
    dejaría de ser 'el mismo modelo que trabaja' y la medición de 8/8 que
    justificó la decisión no aplicaría al modelo que realmente audita."""
    client.portal.call(_correr)
    cerebro = client.portal.call(
        sql, "SELECT provider_id, model_id FROM facet_binding WHERE facet_key='jax_local' AND role='primary'", (), True)
    juez = client.portal.call(
        sql, "SELECT provider_id, model_id FROM facet_binding WHERE facet_key='el_juez' AND role='primary'", (), True)
    assert cerebro, "no hay binding del cerebro: el test no puede afirmar nada"
    assert juez, "el juez no quedó bindeado"
    assert juez[0] == cerebro[0], f"juez={juez[0]} cerebro={cerebro[0]}"


def test_sigue_al_cerebro_si_el_cerebro_cambia(client):
    """Una segunda fuente de verdad que se desincroniza en silencio es el defecto
    que esta rama viene a evitar. Se mueve el binding del cerebro y se comprueba
    que el juez lo sigue en el siguiente arranque."""
    antes = client.portal.call(
        sql, "SELECT provider_id, model_id, model_ref FROM facet_binding WHERE facet_key='jax_local' AND role='primary'",
        (), True)[0]
    try:
        client.portal.call(sql, "UPDATE facet_binding SET model_id=%s WHERE facet_key='jax_local' AND role='primary'",
                           ("modelo-de-prueba-el-juez",))
        client.portal.call(_correr)
        juez = client.portal.call(
            sql, "SELECT model_id FROM facet_binding WHERE facet_key='el_juez' AND role='primary'", (), True)[0]
        assert juez[0] == "modelo-de-prueba-el-juez", "el juez NO siguió al cerebro"
    finally:
        client.portal.call(sql, "UPDATE facet_binding SET model_id=%s WHERE facet_key='jax_local' AND role='primary'",
                           (antes[1],))
        client.portal.call(_correr)


def test_el_binding_queda_con_model_ref_resuelto(client):
    """Hallazgo MEDIO del 2026-09-18 con `auditor_local`: un binding con
    `model_ref` NULL hace que `resolve_facet` reviente con FacetUnavailableError
    mientras `misiones.py` --que sólo mira `provider.is_local`-- sigue diciendo
    'elegible'. Las dos mitades en desacuerdo: fail-closed pero opaco."""
    client.portal.call(_correr)
    filas = client.portal.call(
        sql, "SELECT model_ref FROM facet_binding WHERE facet_key='el_juez' AND role='primary'", (), True)
    assert filas and filas[0][0] is not None, "model_ref quedó NULL"


# ---------------------------------------------------------------------------
# La base VIRGEN (2026-09-21). Los tests de arriba usan `client`, cuyo fixture
# de sesión (`asegurar_base_de_test()`) clona la base con los DATOS de las
# tablas chicas de la plantilla `jax_memory_test` -- `facet_binding`/`model`
# YA vienen resueltos ahí, así que llamar a `_seed_el_juez_facet` suelto,
# después, nunca ejercita el defecto real: el orden de `run_migrations()`.
#
# Antes del fix: `_seed_el_juez_facet` corría en la línea de `run_migrations`
# ANTES que `_seed_models_and_backfill` -- en una base sin una sola fila de
# `model`, se rendía con un `return` silencioso y `el_juez` nacía SIN
# `facet_binding`. Esto crea una base física nueva (sin clonar nada, cero
# tablas) y corre `run_migrations()` COMPLETO, tal como arranca una
# instalación de cero.
# ---------------------------------------------------------------------------

def _nombre_base_virgen() -> str:
    nombre = f"{BASE_COMPARTIDA}_virgenjuez{secrets.token_hex(4)}"
    assert es_base_de_test(nombre)
    return nombre


async def _crear_base_virgen(nombre: str) -> None:
    conn = await aiomysql.connect(
        db=BASE_COMPARTIDA, connect_timeout=db_connect_timeout_seconds(),
        **_parametros_de_conexion())
    try:
        async with conn.cursor() as cur:
            await cur.execute(f"CREATE DATABASE `{nombre}`")
    finally:
        conn.close()


async def _dropear_base_virgen(nombre: str) -> None:
    conn = await aiomysql.connect(
        db=BASE_COMPARTIDA, connect_timeout=db_connect_timeout_seconds(),
        **_parametros_de_conexion())
    try:
        async with conn.cursor() as cur:
            await cur.execute(f"DROP DATABASE IF EXISTS `{nombre}`")
    finally:
        conn.close()


async def _migrar_y_leer_bindings():
    """`run_migrations()` COMPLETO -- no sólo `_seed_el_juez_facet` suelto --
    contra la base que `JAX_DB_NAME` apunte en ESTE momento. El pool queda
    atado al loop de este `asyncio.run()`, aislado del pool de la sesión
    (`db/connection.py`: un pool por event loop)."""
    await run_migrations()
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT provider_id, model_id, model_ref FROM facet_binding "
                "WHERE facet_key='jax_local' AND role='primary'")
            cerebro = await cur.fetchone()
            await cur.execute(
                "SELECT provider_id, model_id, model_ref FROM facet_binding "
                "WHERE facet_key='el_juez' AND role='primary'")
            juez = await cur.fetchone()
    await close_pool()
    return cerebro, juez


@pytest.mark.skipif(not os.environ.get("JAX_DB_HOST"), reason="sin MariaDB a mano no hay base que crear")
def test_una_base_virgen_nace_con_el_juez_bindeado(monkeypatch):
    """El corazón de este archivo. Antes del fix (2026-09-21): esto daba
    `juez is None` -- `el_juez` no llegaba a tener NINGÚN `facet_binding` en
    una instalación de cero, porque `_seed_el_juez_facet` corría antes que
    `_seed_models_and_backfill` en `run_migrations` y se rendía en silencio."""
    nombre = _nombre_base_virgen()
    asyncio.run(_crear_base_virgen(nombre))
    monkeypatch.setenv("JAX_DB_NAME", nombre)
    try:
        cerebro, juez = asyncio.run(_migrar_y_leer_bindings())
    finally:
        asyncio.run(_dropear_base_virgen(nombre))
    assert cerebro is not None, "el cerebro (jax_local) no se sembró: el test no puede afirmar nada"
    assert juez is not None, "el_juez nació SIN facet_binding en una base virgen"
    provider_cerebro, model_cerebro, _ = cerebro
    provider_juez, model_juez, model_ref_juez = juez
    assert (provider_juez, model_juez) == (provider_cerebro, model_cerebro), (
        f"juez={(provider_juez, model_juez)} cerebro={(provider_cerebro, model_cerebro)}")
    assert model_ref_juez is not None, "el_juez quedó con model_ref NULL en una base virgen"
