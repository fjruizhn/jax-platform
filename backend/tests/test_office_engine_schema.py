"""Physical contract for the migrator-only Office engine catalogue."""
from __future__ import annotations

import pytest

from db.connection import get_pool
from virtual_office.migrations import apply_office_migrations


def test_engine_catalogue_is_empty_and_refuses_weakened_homonym(client):
    async def scenario():
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await apply_office_migrations(cur)
                await cur.execute("SELECT COUNT(*) FROM office_engine")
                assert await cur.fetchone() == (0,)
                await cur.execute("DROP TABLE office_engine")
                await cur.execute("CREATE TABLE office_engine (engine_key VARCHAR(64) NOT NULL PRIMARY KEY) ENGINE=InnoDB")
                with pytest.raises(RuntimeError, match="Office engine catalogue metadata is invalid"):
                    await apply_office_migrations(cur)
                await cur.execute("DROP TABLE office_engine")
                await apply_office_migrations(cur)
    client.portal.call(scenario)
