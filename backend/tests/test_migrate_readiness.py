"""The one-shot migrator waits only for its own DB and is bounded."""
from __future__ import annotations

import pytest


class _Cursor:
    async def execute(self, statement):
        assert statement == "SELECT 1"

    async def fetchone(self):
        return (1,)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None


class _Connection:
    def cursor(self):
        return _Cursor()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None


class _Pool:
    def acquire(self):
        return _Connection()


@pytest.mark.asyncio
async def test_migrator_retries_unready_db_then_runs_once(monkeypatch):
    from db import migrate

    attempts = 0
    closed = 0
    migrated = 0

    async def get_pool():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("DB still starting")
        return _Pool()

    async def close_pool():
        nonlocal closed
        closed += 1

    async def run_migrations():
        nonlocal migrated
        migrated += 1

    async def no_wait(_):
        return None

    monkeypatch.setenv("JAX_MIGRATE_DB_READINESS_ATTEMPTS", "2")
    monkeypatch.setattr(migrate, "get_pool", get_pool)
    monkeypatch.setattr(migrate, "close_pool", close_pool)
    monkeypatch.setattr(migrate, "run_migrations", run_migrations)
    monkeypatch.setattr(migrate.asyncio, "sleep", no_wait)

    await migrate.run_with_db_readiness()

    assert (attempts, closed, migrated) == (2, 3, 1)


@pytest.mark.asyncio
async def test_migrator_exhausts_readiness_without_running_ddl(monkeypatch):
    from db import migrate

    closed = 0
    migrated = 0

    async def unavailable():
        raise OSError("DB unavailable")

    async def close_pool():
        nonlocal closed
        closed += 1

    async def run_migrations():
        nonlocal migrated
        migrated += 1

    async def no_wait(_):
        return None

    monkeypatch.setenv("JAX_MIGRATE_DB_READINESS_ATTEMPTS", "2")
    monkeypatch.setattr(migrate, "get_pool", unavailable)
    monkeypatch.setattr(migrate, "close_pool", close_pool)
    monkeypatch.setattr(migrate, "run_migrations", run_migrations)
    monkeypatch.setattr(migrate.asyncio, "sleep", no_wait)

    with pytest.raises(OSError, match="DB unavailable"):
        await migrate.run_with_db_readiness()

    assert (closed, migrated) == (2, 0)


def test_migration_readiness_bounds_attempts_and_deadline(monkeypatch):
    from db import migrate

    monkeypatch.setenv("JAX_MIGRATE_DB_READINESS_ATTEMPTS", "61")
    with pytest.raises(RuntimeError, match="between 1 and 60"):
        migrate._readiness_attempts()
    monkeypatch.setenv("JAX_MIGRATE_DB_READINESS_TIMEOUT_SECONDS", "0")
    with pytest.raises(RuntimeError, match="between 1 and 60"):
        migrate._readiness_timeout_seconds()
