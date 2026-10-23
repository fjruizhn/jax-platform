"""One-shot schema runner; the web process never invokes this module."""
from __future__ import annotations

import asyncio
import os

import aiomysql

from .connection import close_pool, get_pool
from .migrations import run_migrations


def _positive_bounded_env(name: str, default: int, maximum: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        attempts = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a positive integer") from exc
    if attempts < 1 or attempts > maximum:
        raise RuntimeError(f"{name} must be between 1 and {maximum}")
    return attempts


def _readiness_attempts() -> int:
    return _positive_bounded_env("JAX_MIGRATE_DB_READINESS_ATTEMPTS", 12, 60)


def _readiness_timeout_seconds() -> int:
    return _positive_bounded_env("JAX_MIGRATE_DB_READINESS_TIMEOUT_SECONDS", 10, 60)


async def _readiness_probe() -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT 1")
            if await cur.fetchone() != (1,):
                raise RuntimeError("migration database readiness returned an invalid result")


async def run_with_db_readiness() -> None:
    """Bound migration-only DB readiness check; every failed pool is closed."""
    attempts = _readiness_attempts()
    timeout = _readiness_timeout_seconds()
    for attempt in range(attempts):
        ready = False
        try:
            await asyncio.wait_for(_readiness_probe(), timeout=timeout)
            ready = True
        except (aiomysql.Error, OSError, RuntimeError):
            if attempt + 1 == attempts:
                raise
        finally:
            await close_pool()
        if ready:
            break
        # The wait yields the loop; it never blocks web request handling.
        await asyncio.sleep(1)
    try:
        await run_migrations()
    finally:
        await close_pool()


async def main() -> None:
    await run_with_db_readiness()


if __name__ == "__main__":
    asyncio.run(main())
