import os
import aiomysql

_pool: aiomysql.Pool | None = None


async def get_pool() -> aiomysql.Pool:
    global _pool
    if _pool is None:
        _pool = await aiomysql.create_pool(
            host=os.getenv("JAX_DB_HOST", "localhost"),
            port=3306,
            user=os.getenv("JAX_DB_USER", "jax_user"),
            password=os.getenv("JAX_DB_PASSWORD", ""),
            db=os.getenv("JAX_DB_NAME", "jax_memory"),
            charset="utf8mb4",
            autocommit=True,
            minsize=1,
            maxsize=10,
        )
    return _pool


async def close_pool():
    global _pool
    if _pool:
        _pool.close()
        await _pool.wait_closed()
        _pool = None
