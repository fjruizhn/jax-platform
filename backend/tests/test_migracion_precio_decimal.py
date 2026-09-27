"""model.price_*_per_1m_usd -- DECIMAL(10,4) a DECIMAL(12,6) (2026-09-27).

Origen (ver CONTEXT.md): `enrich_from_models_dev()` truncaba en silencio con
`Warning: Data truncated for column 'price_cache_per_1m_usd'` -- 4 decimales
no alcanza para precios por debajo de $0.001/1M tokens, y MariaDB bajo modo
no estricto no revienta, solo trunca y avisa por un canal que nadie miraba.

Mismo patrón que `_COLUMN_WIDENS`/`_column_too_narrow` (VARCHAR) para las 3
columnas de precio, pero sobre precisión/escala DECIMAL en vez de longitud
de texto -- `_column_too_narrow` no aplica (CHARACTER_MAXIMUM_LENGTH es NULL
para DECIMAL).
"""
from db import migrations


async def _precision_de(cur, columna):
    await cur.execute(
        "SELECT NUMERIC_PRECISION, NUMERIC_SCALE FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'model' AND COLUMN_NAME = %s",
        (columna,),
    )
    return await cur.fetchone()


async def _insertar_precio_seis_decimales(cur, provider_id, model_id):
    await cur.execute(
        "INSERT INTO model (provider_id, model_id, price_input_per_1m_usd, "
        "price_output_per_1m_usd, price_cache_per_1m_usd, source, source_checked_at) "
        "VALUES (%s, %s, 0.000123, 1234.567891, 0.000001, 'manual', NOW())",
        (provider_id, model_id),
    )


async def _leer_precio(cur, provider_id, model_id):
    await cur.execute(
        "SELECT price_input_per_1m_usd, price_output_per_1m_usd, price_cache_per_1m_usd "
        "FROM model WHERE provider_id=%s AND model_id=%s",
        (provider_id, model_id),
    )
    return await cur.fetchone()


def test_las_tres_columnas_de_precio_son_decimal_12_6(client):
    """`asegurar_base_de_test()` (conftest/base_de_test.py) ya corrió
    `run_migrations()` para armar la base de esta sesión -- esto verifica el
    RESULTADO contra information_schema, no solo que el ALTER exista en el
    código."""
    async def _verificar():
        from db.connection import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                for columna in (
                    "price_input_per_1m_usd", "price_output_per_1m_usd", "price_cache_per_1m_usd",
                ):
                    precision, scale = await _precision_de(cur, columna)
                    assert (precision, scale) == (12, 6), f"{columna} es DECIMAL({precision},{scale})"
    client.portal.call(_verificar)


def test_correr_run_migrations_dos_veces_no_falla_ni_cambia_la_precision(client):
    """Idempotencia: el ALTER MODIFY sobre una columna que YA está en
    DECIMAL(12,6) no debe fallar ni degradarla -- `_decimal_precision_too_small`
    (o como se llame el guard) tiene que devolver False la segunda vez."""
    async def _verificar():
        from db.connection import get_pool
        await migrations.run_migrations()  # segunda corrida, misma sesión
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                precision, scale = await _precision_de(cur, "price_input_per_1m_usd")
                assert (precision, scale) == (12, 6)
    client.portal.call(_verificar)


def test_guarda_seis_decimales_reales_sin_truncar(client):
    """Antes: DECIMAL(10,4) truncaba 0.000123 -> 0.0001 (Warning silencioso).
    Ahora: DECIMAL(12,6) guarda los 6 decimales completos."""
    provider_id = "zhipu"
    model_id = "test-precio-decimal-6"

    async def _correr():
        from db.connection import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM model WHERE provider_id=%s AND model_id=%s",
                    (provider_id, model_id),
                )
                await _insertar_precio_seis_decimales(cur, provider_id, model_id)
            await conn.commit()
            async with conn.cursor() as cur:
                fila = await _leer_precio(cur, provider_id, model_id)
            async with conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM model WHERE provider_id=%s AND model_id=%s",
                    (provider_id, model_id),
                )
            await conn.commit()
        return fila

    price_input, price_output, price_cache = client.portal.call(_correr)
    assert float(price_input) == 0.000123
    assert float(price_output) == 1234.567891
    assert float(price_cache) == 0.000001
