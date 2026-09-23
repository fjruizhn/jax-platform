"""B9 project authority is JAX-owned DDL executed by the platform schema runner."""
import asyncio
import inspect

import pytest

from db import migrations


class _RecordingCursor:
    def __init__(self):
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)


def test_jax_owned_003_hook_is_the_only_project_authority_ddl_source():
    """The platform invokes the exact JAX hook; it does not duplicate its DDL."""
    cursor = _RecordingCursor()
    asyncio.run(migrations._apply_jax_project_authority_migration(cursor))

    assert len(cursor.statements) == 5
    assert "CREATE TABLE IF NOT EXISTS jax_project_scope" in cursor.statements[0]
    assert "CREATE TABLE IF NOT EXISTS jax_project_membership" in cursor.statements[1]
    assert "CREATE TABLE IF NOT EXISTS jax_project_membership_event" in cursor.statements[2]
    assert "append-only" in cursor.statements[3]
    assert "append-only" in cursor.statements[4]
    assert "jax_project_scope" not in inspect.getsource(migrations.run_migrations)


def test_project_authority_hook_requires_configured_jax_source(monkeypatch):
    monkeypatch.delenv("JAX_REPO_PATH", raising=False)
    with pytest.raises(RuntimeError, match="JAX_REPO_PATH"):
        migrations._jax_project_authority_migration()


async def _schema_snapshot():
    from db.connection import get_pool

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT TABLE_NAME FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN "
                "('jax_project_scope','jax_project_membership','jax_project_membership_event')"
            )
            tables = {row[0] for row in await cur.fetchall()}
            await cur.execute(
                "SELECT TABLE_NAME, COLUMN_NAME FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN "
                "('jax_project_scope','jax_project_membership','jax_project_membership_event')"
            )
            columns = {(row[0], row[1]) for row in await cur.fetchall()}
            await cur.execute(
                "SELECT TRIGGER_NAME FROM information_schema.TRIGGERS "
                "WHERE TRIGGER_SCHEMA = DATABASE() AND TRIGGER_NAME IN "
                "('no_update_jax_project_membership_event',"
                "'no_delete_jax_project_membership_event')"
            )
            triggers = {row[0] for row in await cur.fetchall()}
    return tables, columns, triggers


def test_supported_runner_applies_003_and_expected_schema_is_not_drifted(client):
    """Fresh bootstrap and re-run both pass through run_migrations' real chain."""
    client.portal.call(migrations.run_migrations)
    tables, columns, triggers = client.portal.call(_schema_snapshot)

    assert tables == {
        "jax_project_scope",
        "jax_project_membership",
        "jax_project_membership_event",
    }
    assert {
        ("jax_project_scope", "project_id"),
        ("jax_project_scope", "tenant_id"),
        ("jax_project_scope", "status"),
        ("jax_project_scope", "version"),
        ("jax_project_membership", "membership_id"),
        ("jax_project_membership", "project_role"),
        ("jax_project_membership", "status"),
        ("jax_project_membership", "version"),
        ("jax_project_membership_event", "event_id"),
        ("jax_project_membership_event", "operation"),
        ("jax_project_membership_event", "actor_principal"),
        ("jax_project_membership_event", "request_id"),
        ("jax_project_membership_event", "trace_id"),
    }.issubset(columns)
    assert triggers == {
        "no_update_jax_project_membership_event",
        "no_delete_jax_project_membership_event",
    }
