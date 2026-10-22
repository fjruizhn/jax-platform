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


def test_engine_activation_epoch_prevents_enabled_binding_replay(client):
    async def scenario():
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await apply_office_migrations(cur)
                await cur.execute("SELECT id FROM model ORDER BY id LIMIT 1")
                model = await cur.fetchone()
                assert model is not None
                await cur.execute(
                    "INSERT INTO office_engine (engine_key,display_name_key,model_ref,transport,allowed_data_classifications,binding_fingerprint) "
                    "VALUES ('epoch-test','office.engine.epoch-test',%s,'http_openai_compat',%s,REPEAT('a',64))",
                    (*model, '["SYSTEM_SMOKE"]'),
                )
                with pytest.raises(Exception) as rejected_without_revision:
                    await cur.execute("UPDATE office_engine SET enabled=1 WHERE engine_key='epoch-test'")
                assert getattr(rejected_without_revision.value, "args", (None,))[0] == 1644
                await cur.execute("UPDATE office_engine SET enabled=1,activation_epoch=1,version=2 WHERE engine_key='epoch-test'")
                await cur.execute("UPDATE office_engine SET enabled=0,version=3 WHERE engine_key='epoch-test'")
                with pytest.raises(Exception) as replay:
                    await cur.execute("UPDATE office_engine SET enabled=1,version=4 WHERE engine_key='epoch-test'")
                assert getattr(replay.value, "args", (None,))[0] == 1644
                await cur.execute("DELETE FROM office_engine WHERE engine_key='epoch-test'")
    client.portal.call(scenario)
