"""Adaptador de pool para las lecturas y mutaciones de B9 (jax.memory).

Vivia privado en api/chat.py; se movio aqui (2026-10-02, Proyectos E1 T5) para
que api/proyectos.py use el mismo adaptador sin importar un router ajeno.
Comportamiento sin cambios.
"""
import aiomysql


class B9MappingAcquire:
    """Adapt the platform pool's plain-cursor acquire contract for B9 reads."""
    def __init__(self, acquire_context):
        self._acquire_context = acquire_context

    async def __aenter__(self):
        self._connection = await self._acquire_context.__aenter__()
        return B9MappingConnection(self._connection)

    async def __aexit__(self, exc_type, exc, traceback):
        return await self._acquire_context.__aexit__(exc_type, exc, traceback)


class B9MappingConnection:
    """Connection view which asks aiomysql for mapping rows on every cursor.

    ``db.connection.get_pool`` deliberately uses aiomysql's default cursor for
    the rest of the platform.  B9 readers consume named fields, so adapting at
    this narrow integration boundary avoids treating tuple positions as an
    authorization-sensitive schema contract.
    """
    def __init__(self, connection):
        self._connection = connection

    def cursor(self, *args, **kwargs):
        if args or kwargs:
            raise TypeError("B9 mapping adapter does not accept caller cursor overrides")
        return self._connection.cursor(aiomysql.DictCursor)

    def __getattr__(self, name):
        return getattr(self._connection, name)


class B9MappingPool:
    """Minimal pool adapter required by ``MariaDBB9Reader``."""
    def __init__(self, pool):
        self._pool = pool

    def acquire(self):
        return B9MappingAcquire(self._pool.acquire())
