"""Costo real desde `model` (Bloque D), no desde un dict hardcodeado.
Mismo patron de client.portal.call que test_model_catalog_sync.py."""
from api.admin.usage import record_usage


async def _fetch_last_usage_row():
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT tokens_in, tokens_out, cost_usd, model, facet "
                "FROM axioma_usage ORDER BY id DESC LIMIT 1"
            )
            return await cur.fetchone()


async def _seed_priced_model(provider_id, model_id, price_in, price_out):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO model (provider_id, model_id, status, source, source_checked_at, "
                "price_input_per_1m_usd, price_output_per_1m_usd) "
                "VALUES (%s, %s, 'available', 'manual', NOW(), %s, %s) "
                "ON DUPLICATE KEY UPDATE price_input_per_1m_usd=%s, price_output_per_1m_usd=%s",
                (provider_id, model_id, price_in, price_out, price_in, price_out),
            )
        await conn.commit()


def test_record_usage_calcula_costo_desde_tabla_model(client):
    client.portal.call(_seed_priced_model, "deepseek", "deepseek-v4-flash", 0.14, 0.28)
    client.portal.call(
        record_usage,
        "1", "1", "jekyll", "deepseek", "deepseek-v4-flash", 1000, 500, "chat", None,
    )
    row = client.portal.call(_fetch_last_usage_row)
    tokens_in, tokens_out, cost_usd, model, facet = row
    assert tokens_in == 1000
    assert tokens_out == 500
    expected = (1000 * 0.14 + 500 * 0.28) / 1_000_000
    assert abs(float(cost_usd) - expected) < 1e-9


def test_record_usage_modelo_sin_catalogo_da_costo_null(client):
    client.portal.call(
        record_usage,
        "1", "1", "jekyll", "deepseek", "modelo-que-no-existe-nunca", 100, 50, "chat", None,
    )
    row = client.portal.call(_fetch_last_usage_row)
    _tokens_in, _tokens_out, cost_usd, _model, _facet = row
    assert cost_usd is None


def test_record_usage_cost_usd_override_ignora_tabla_model(client):
    client.portal.call(
        record_usage,
        "1", "1", "thot_image", "openai", "gpt-image-1", 0, 0, "imagen", 0.04,
    )
    row = client.portal.call(_fetch_last_usage_row)
    _tokens_in, _tokens_out, cost_usd, _model, _facet = row
    assert abs(float(cost_usd) - 0.04) < 1e-9
