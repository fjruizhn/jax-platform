"""Bloque D -- facetas en riesgo (2026-09-27).

`model_catalog._facetas_en_riesgo()` es la consulta que alimenta
`sync_all()["facetas_en_riesgo"]`: bindings 'primary' (el ÚNICO rol que
algún camino de dispatch real lee hoy -- facet_resolver.py:296,
ejecutor/misiones.py:68, adjuntos/politica.py:24, los tres con
`role = 'primary'`; 'fallback_1'/'fallback_2' existen en el ENUM pero
ningún resolver los consulta, verificado 2026-09-27) cuyo modelo -- resuelto
por `model_ref`, la FK que el dispatch real usa (facet_resolver.py:298,
`JOIN model m ON m.id = b.model_ref`) -- no está disponible.

Usa filas SINTÉTICAS con facet_key/provider/model_id únicos por corrida
(uuid) en vez de depender de qué faceta esté hoy en riesgo de verdad en la
base compartida (jekyll/deepseek-v4-flash, según CONTEXT.md) -- esa faceta
puede quedar resuelta cualquier día y este test no puede depender de eso
para seguir midiendo lo mismo (mismo criterio que
test_sync_marks_missing_model_deprecated_after_three_consecutive_misses:
"arranca de un baseline explícito").
"""
import uuid

import model_catalog


async def _fetch_pool_cursor_facetas_en_riesgo():
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            return await model_catalog._facetas_en_riesgo(cur)


async def _crear_binding_con_model_ref(facet_key, provider_id, model_id, status):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO facet (`key`, display_name, transport) "
                "VALUES (%s, %s, 'http_openai_compat')",
                (facet_key, facet_key),
            )
            await cur.execute(
                "INSERT INTO model (provider_id, model_id, status, source, source_checked_at) "
                "VALUES (%s, %s, %s, 'manual', NOW())",
                (provider_id, model_id, status),
            )
            await cur.execute(
                "SELECT id FROM model WHERE provider_id=%s AND model_id=%s",
                (provider_id, model_id),
            )
            (model_ref,) = await cur.fetchone()
            await cur.execute(
                "INSERT INTO facet_binding (facet_key, provider_id, model_id, model_ref, role) "
                "VALUES (%s, %s, %s, %s, 'primary')",
                (facet_key, provider_id, model_id, model_ref),
            )
        await conn.commit()
    return model_ref


async def _crear_binding_sin_model_ref(facet_key, provider_id, model_id, role="primary"):
    """facet_binding.model_ref NULL -- el estado que dejaban los escritores
    de ANTES de D1.1 paso 4 (facet_resolver.py:284-288). El dispatch real
    hace INNER JOIN sobre model_ref: una fila así no resuelve NADA."""
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO facet (`key`, display_name, transport) "
                "VALUES (%s, %s, 'http_openai_compat')",
                (facet_key, facet_key),
            )
            await cur.execute(
                "INSERT INTO facet_binding (facet_key, provider_id, model_id, role) "
                "VALUES (%s, %s, %s, %s)",
                (facet_key, provider_id, model_id, role),
            )
        await conn.commit()


async def _borrar_facet(facet_key, provider_id=None, model_id=None):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM facet_binding WHERE facet_key=%s", (facet_key,))
            await cur.execute("DELETE FROM facet WHERE `key`=%s", (facet_key,))
            if provider_id and model_id:
                await cur.execute(
                    "DELETE FROM model WHERE provider_id=%s AND model_id=%s",
                    (provider_id, model_id),
                )
        await conn.commit()


def test_facetas_en_riesgo_detecta_modelo_deprecado(client):
    facet_key = f"test-riesgo-{uuid.uuid4().hex[:8]}"
    provider_id = "zhipu"
    model_id = f"test-riesgo-modelo-{uuid.uuid4().hex[:8]}"
    client.portal.call(_crear_binding_con_model_ref, facet_key, provider_id, model_id, "deprecated")
    try:
        filas = client.portal.call(_fetch_pool_cursor_facetas_en_riesgo)
        encontrada = next((f for f in filas if f["facet_key"] == facet_key), None)
        assert encontrada is not None, "una faceta atada a un modelo deprecado tiene que aparecer"
        assert encontrada["status"] == "deprecated"
        assert encontrada["provider_id"] == provider_id
        assert encontrada["model_id"] == model_id
        assert "model_ref_diverge_de_texto" not in encontrada
    finally:
        client.portal.call(_borrar_facet, facet_key, provider_id, model_id)


def test_facetas_en_riesgo_no_incluye_modelo_disponible(client):
    facet_key = f"test-sano-{uuid.uuid4().hex[:8]}"
    provider_id = "zhipu"
    model_id = f"test-sano-modelo-{uuid.uuid4().hex[:8]}"
    client.portal.call(_crear_binding_con_model_ref, facet_key, provider_id, model_id, "available")
    try:
        filas = client.portal.call(_fetch_pool_cursor_facetas_en_riesgo)
        assert not any(f["facet_key"] == facet_key for f in filas)
    finally:
        client.portal.call(_borrar_facet, facet_key, provider_id, model_id)


def test_facetas_en_riesgo_binding_sin_model_ref_es_riesgo(client):
    """Una fila con `model_ref` NULL no resuelve nada para el dispatch real
    (INNER JOIN): tiene que aparecer como riesgo aunque no haya ningún
    `model` al que apuntar todavía."""
    facet_key = f"test-sin-ref-{uuid.uuid4().hex[:8]}"
    client.portal.call(_crear_binding_sin_model_ref, facet_key, "zhipu", "algun-texto-viejo")
    try:
        filas = client.portal.call(_fetch_pool_cursor_facetas_en_riesgo)
        encontrada = next((f for f in filas if f["facet_key"] == facet_key), None)
        assert encontrada is not None
        assert encontrada["status"] == "sin_model_ref"
    finally:
        client.portal.call(_borrar_facet, facet_key)


def test_facetas_en_riesgo_ignora_role_no_primary(client):
    """'fallback_1'/'fallback_2'/'disabled' -- ningún camino de dispatch real
    los lee hoy (verificado contra el código, ver docstring del módulo).
    Un binding en esos roles atado a un modelo deprecado NO es una faceta en
    riesgo: nada despacha a través de él."""
    facet_key = f"test-fallback-{uuid.uuid4().hex[:8]}"
    provider_id = "zhipu"
    model_id = f"test-fallback-modelo-{uuid.uuid4().hex[:8]}"
    pool_model_ref = None
    from db.connection import get_pool

    async def _crear():
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO facet (`key`, display_name, transport) "
                    "VALUES (%s, %s, 'http_openai_compat')",
                    (facet_key, facet_key),
                )
                await cur.execute(
                    "INSERT INTO model (provider_id, model_id, status, source, source_checked_at) "
                    "VALUES (%s, %s, 'deprecated', 'manual', NOW())",
                    (provider_id, model_id),
                )
                await cur.execute(
                    "SELECT id FROM model WHERE provider_id=%s AND model_id=%s",
                    (provider_id, model_id),
                )
                (model_ref,) = await cur.fetchone()
                await cur.execute(
                    "INSERT INTO facet_binding (facet_key, provider_id, model_id, model_ref, role) "
                    "VALUES (%s, %s, %s, %s, 'fallback_1')",
                    (facet_key, provider_id, model_id, model_ref),
                )
            await conn.commit()

    client.portal.call(_crear)
    try:
        filas = client.portal.call(_fetch_pool_cursor_facetas_en_riesgo)
        assert not any(f["facet_key"] == facet_key for f in filas)
    finally:
        client.portal.call(_borrar_facet, facet_key, provider_id, model_id)


def test_facetas_en_riesgo_marca_divergencia_entre_model_ref_y_texto(client):
    """Antecedente real de deriva entre `facet_binding.model_ref` y sus
    columnas de texto (provider_id/model_id): D1.1 paso 4 dejó el texto de
    solo lectura, pero una fila vieja puede seguir sin sincronizarse. La
    resolución real (por model_ref) manda, y la divergencia queda marcada
    como evidencia -- nunca oculta."""
    facet_key = f"test-diverge-{uuid.uuid4().hex[:8]}"
    provider_real = "zhipu"
    model_id_real = f"test-diverge-real-{uuid.uuid4().hex[:8]}"
    model_id_texto_viejo = f"test-diverge-viejo-{uuid.uuid4().hex[:8]}"
    from db.connection import get_pool

    async def _crear():
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO facet (`key`, display_name, transport) "
                    "VALUES (%s, %s, 'http_openai_compat')",
                    (facet_key, facet_key),
                )
                await cur.execute(
                    "INSERT INTO model (provider_id, model_id, status, source, source_checked_at) "
                    "VALUES (%s, %s, 'deprecated', 'manual', NOW())",
                    (provider_real, model_id_real),
                )
                await cur.execute(
                    "SELECT id FROM model WHERE provider_id=%s AND model_id=%s",
                    (provider_real, model_id_real),
                )
                (model_ref,) = await cur.fetchone()
                # el texto queda apuntando a otro model_id (deriva real, no un accidente de test)
                await cur.execute(
                    "INSERT INTO facet_binding (facet_key, provider_id, model_id, model_ref, role) "
                    "VALUES (%s, %s, %s, %s, 'primary')",
                    (facet_key, provider_real, model_id_texto_viejo, model_ref),
                )
            await conn.commit()

    client.portal.call(_crear)
    try:
        filas = client.portal.call(_fetch_pool_cursor_facetas_en_riesgo)
        encontrada = next(f for f in filas if f["facet_key"] == facet_key)
        assert encontrada["model_id"] == model_id_real  # el dispatch real usa esto, no el texto
        assert encontrada["model_ref_diverge_de_texto"] is True
    finally:
        client.portal.call(_borrar_facet, facet_key, provider_real, model_id_real)
