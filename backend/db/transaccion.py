"""Transacción explícita sobre el pool (2026-09-12, admin usuarios etapa 3).

El pool de db/connection.py es autocommit=True: cada sentencia se confirma
sola. Cuando un cambio y su auditoría (o un conteo y el cambio que ese conteo
autoriza) tienen que ir juntos, esto abre BEGIN, confirma al salir y revierte
ante CUALQUIER excepción -- incluida la HTTPException de una guarda.
"""
from contextlib import asynccontextmanager

from .connection import get_pool


@asynccontextmanager
async def transaccion():
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.begin()
        try:
            async with conn.cursor() as cur:
                yield cur
        except BaseException:
            await conn.rollback()
            raise
        await conn.commit()
