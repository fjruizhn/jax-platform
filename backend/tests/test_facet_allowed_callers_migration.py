"""facet.allowed_callers se siembra para los 4 facets HTTP-directos,
idempotente (correr dos veces no duplica ni pisa un valor manual)."""
import json

import pytest

from db.connection import get_pool
from db.migrations import _seed_http_facet_allowed_callers


@pytest.mark.asyncio
async def test_seed_sets_allowed_callers_for_the_4_http_facets():
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await _seed_http_facet_allowed_callers(cur)
            await cur.execute(
                "SELECT `key`, allowed_callers FROM facet WHERE `key` IN "
                "('hipatia','jekyll','thot','ada') ORDER BY `key`"
            )
            rows = {key: json.loads(val) for key, val in await cur.fetchall()}
            for facet_key in ("hipatia", "jekyll", "thot", "ada"):
                assert rows[facet_key] == ["jacobs", "jax_platform_chat"]


@pytest.mark.asyncio
async def test_seed_does_not_overwrite_manual_value():
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE facet SET allowed_callers = %s WHERE `key` = 'hipatia'",
                (json.dumps(["solo_jacobs"]),),
            )
            await conn.commit()

    # Run the seed in a new connection to ensure isolation
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await _seed_http_facet_allowed_callers(cur)
            await cur.execute("SELECT allowed_callers FROM facet WHERE `key` = 'hipatia'")
            (val,) = await cur.fetchone()
            assert json.loads(val) == ["solo_jacobs"]
            await conn.commit()


@pytest.mark.asyncio
async def test_seed_leaves_out_of_scope_facets_null():
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await _seed_http_facet_allowed_callers(cur)
            await cur.execute(
                "SELECT allowed_callers FROM facet WHERE `key` IN ('kimi','jax_local','hyde')"
            )
            for (val,) in await cur.fetchall():
                assert val is None
            await conn.commit()
