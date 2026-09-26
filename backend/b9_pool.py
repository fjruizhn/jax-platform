"""Adapter that gives B9 readers/writers mapping (dict) rows.

Extracted from `api/chat.py` (PR-P1, 2026-09-25 — plan §4): the memory-chat
boundary and the project-authority boundary (`backend/proyectos/autoridad.py`)
both need the SAME adapter over `db.connection.get_pool()`'s plain-cursor
pool, so it lives here once instead of being duplicated or imported from a
chat-specific module for an unrelated feature.

`db.connection.get_pool()` deliberately uses aiomysql's default (tuple)
cursor for the rest of the platform. Both B9 readers and the project
authority admin (`jax.memory.project_authority.ProjectAuthorityAdmin`)
consume named fields (`row.get(name)` / `row[pos]` fallback), so adapting at
this narrow integration boundary avoids treating tuple positions as an
authorization-sensitive schema contract.
"""
from __future__ import annotations

import aiomysql


class _B9MappingAcquire:
    """Adapt the platform pool's plain-cursor acquire contract for B9 reads."""
    def __init__(self, acquire_context):
        self._acquire_context = acquire_context

    async def __aenter__(self):
        self._connection = await self._acquire_context.__aenter__()
        return _B9MappingConnection(self._connection)

    async def __aexit__(self, exc_type, exc, traceback):
        return await self._acquire_context.__aexit__(exc_type, exc, traceback)


class _B9MappingConnection:
    """Connection view which asks aiomysql for mapping rows on every cursor."""
    def __init__(self, connection):
        self._connection = connection

    def cursor(self, *args, **kwargs):
        if args or kwargs:
            raise TypeError("B9 mapping adapter does not accept caller cursor overrides")
        return self._connection.cursor(aiomysql.DictCursor)

    def __getattr__(self, name):
        return getattr(self._connection, name)


class _B9MappingPool:
    """Minimal pool adapter required by ``MariaDBB9Reader``/``MariaDBB9Store``."""
    def __init__(self, pool):
        self._pool = pool

    def acquire(self):
        return _B9MappingAcquire(self._pool.acquire())
