"""Transacción explícita sobre el pool (2026-09-12, admin usuarios etapa 3).

El pool de db/connection.py es autocommit=True: cada sentencia se confirma
sola. Cuando un cambio y su auditoría (o un conteo y el cambio que ese conteo
autoriza) tienen que ir juntos, esto abre BEGIN, confirma al salir y revierte
ante CUALQUIER excepción -- incluida la HTTPException de una guarda.
"""
from contextlib import asynccontextmanager

from .connection import get_pool

# `aislamiento` (fix ronda 1 de la Task 2, 2026-09-15): nivel para ESTA
# transacción sola (`SET TRANSACTION`, sin SESSION: la conexión vuelve al pool
# con el nivel por defecto). Lista cerrada: el valor va interpolado en el SQL.
# Frente A (2026-09-16, A-28): solo READ COMMITTED -- ningún llamador pidió
# otro nivel (U33); uno nuevo se agrega acá cuando exista quien lo use.
AISLAMIENTOS = frozenset({"READ COMMITTED"})

# READ COMMITTED: bloquea solo filas, sin huecos. Nació para las escrituras de
# admin sobre jax_users (el porqué, en api/admin/users.py, que lo reexporta con
# este mismo nombre). Ruling U33 (etapa 5, Task 3 fix ronda 2, 2026-09-15): vive
# acá para que api/auth.py lo use sin importar api/admin/users.py (ese ciclo de
# import lo cerró la etapa 4). Lo usa también el forgot-password público.
AISLAMIENTO_ADMIN = "READ COMMITTED"


@asynccontextmanager
async def transaccion(aislamiento: str | None = None):
    if aislamiento is not None and aislamiento not in AISLAMIENTOS:
        raise ValueError(f"nivel de aislamiento desconocido: {aislamiento!r}")
    pool = await get_pool()
    async with pool.acquire() as conn:
        if aislamiento is not None:
            async with conn.cursor() as cur:
                await cur.execute(f"SET TRANSACTION ISOLATION LEVEL {aislamiento}")
        await conn.begin()
        try:
            async with conn.cursor() as cur:
                yield cur
        except BaseException:
            await conn.rollback()
            raise
        await conn.commit()
