"""Physical contract for the Office engine catalogue."""
from __future__ import annotations

import pytest

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


def test_preexisting_weakened_engine_catalogue_fails_closed_before_readiness(client):
    """CREATE IF NOT EXISTS must not bless a homonymous table without its authority ties."""
    async def scenario():
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await apply_office_migrations(cur)
                await cur.execute("DROP TABLE office_engine")
                await cur.execute(
                    "CREATE TABLE office_engine ("
                    "engine_key VARCHAR(64) NOT NULL PRIMARY KEY,"
                    "display_name_key VARCHAR(128) NOT NULL,model_ref INT NOT NULL,"
                    "transport VARCHAR(64) NOT NULL,allowed_data_classifications JSON NOT NULL,"
                    "enabled BOOLEAN NOT NULL DEFAULT FALSE,version BIGINT NOT NULL DEFAULT 1,"
                    "created_at DATETIME(6) NOT NULL,updated_at DATETIME(6) NOT NULL"
                    ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
                )
                with pytest.raises(RuntimeError, match="Office engine catalogue .* invalid"):
                    await apply_office_migrations(cur)
                await cur.execute("DROP TABLE office_engine")
                await apply_office_migrations(cur)
    client.portal.call(scenario)
