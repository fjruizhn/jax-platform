"""Physical contract for the Office engine catalogue."""
from __future__ import annotations

from db.connection import get_pool
from virtual_office.migrations import apply_office_migrations


async def _engine_schema():
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await apply_office_migrations(cur)
            await cur.execute(
                "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='office_engine' "
                "ORDER BY ORDINAL_POSITION"
            )
            columns = tuple(row[0] for row in await cur.fetchall())
            await cur.execute("SELECT COUNT(*) FROM office_engine")
            (row_count,) = await cur.fetchone()
    return columns, row_count


def test_office_engine_catalogue_is_empty_and_binds_only_to_central_model_rows(client):
    """A migration that seeds GLM or stores a URL/credential must fail this contract."""
    columns, row_count = client.portal.call(_engine_schema)

    assert columns == (
        "engine_key", "display_name_key", "model_ref", "transport",
        "allowed_data_classifications", "enabled", "version", "created_at", "updated_at",
    )
    assert row_count == 0
