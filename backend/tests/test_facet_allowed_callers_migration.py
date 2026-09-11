"""facet.allowed_callers se siembra para los 5 facets HTTP-directos del
chat (hipatia/jekyll/thot/ada + kimi desde 2026-09-11), idempotente (correr
dos veces no duplica ni pisa un valor manual)."""
import json

import pytest

from db.connection import get_pool
from db.migrations import _seed_http_facet_allowed_callers

_HTTP_CHAT_FACETS = ("ada", "hipatia", "jekyll", "kimi", "thot")


@pytest.mark.asyncio
async def test_seed_sets_allowed_callers_for_the_http_chat_facets():
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await _seed_http_facet_allowed_callers(cur)
            await cur.execute(
                "SELECT `key`, allowed_callers FROM facet WHERE `key` IN "
                "('ada','hipatia','jekyll','kimi','thot') ORDER BY `key`"
            )
            rows = {key: json.loads(val) for key, val in await cur.fetchall()}
            assert set(rows) == set(_HTTP_CHAT_FACETS)
            for facet_key in _HTTP_CHAT_FACETS:
                assert rows[facet_key] == ["jacobs", "jax_platform_chat"]


@pytest.mark.asyncio
async def test_seed_does_not_overwrite_manual_value():
    pool = await get_pool()
    try:
        # Set hipatia to a different value to test idempotent guard
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "UPDATE facet SET allowed_callers = %s WHERE `key` = 'hipatia'",
                    (json.dumps(["solo_jacobs"]),),
                )

        # Run the seed in a new connection to ensure isolation
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await _seed_http_facet_allowed_callers(cur)
                await cur.execute("SELECT allowed_callers FROM facet WHERE `key` = 'hipatia'")
                (val,) = await cur.fetchone()
                assert json.loads(val) == ["solo_jacobs"]
    finally:
        # Cleanup: restore hipatia to NULL to prevent test pollution
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("UPDATE facet SET allowed_callers = NULL WHERE `key` = 'hipatia'")


@pytest.mark.asyncio
async def test_seed_leaves_out_of_scope_facets_null():
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await _seed_http_facet_allowed_callers(cur)
            await cur.execute(
                "SELECT allowed_callers FROM facet WHERE `key` IN ('jax_local','hyde')"
            )
            rows = await cur.fetchall()
            assert len(rows) == 2
            for (val,) in rows:
                assert val is None
