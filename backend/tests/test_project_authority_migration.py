"""B9 project authority is JAX-owned DDL executed by the platform schema runner."""
import asyncio
import inspect

import pytest

from db import migrations


class _RecordingCursor:
    def __init__(self):
        self.statements = []
        self.check_constraint_queries = []
        # The JAX-owned 005 hook inspects the schema after applying 003.  Model
        # the schema just upgraded from 003: only the old scope constraint is
        # present, and none of the 005 additions have been applied yet.
        self._last_statement = ""
        self._last_parameters = ()

    async def execute(self, statement, parameters=None):
        self.statements.append(statement)
        self._last_statement = statement
        self._last_parameters = parameters or ()
        if "CHECK_CLAUSE FROM information_schema.CHECK_CONSTRAINTS" in statement:
            self.check_constraint_queries.append((statement, self._last_parameters))

    async def fetchone(self):
        # Answer information-schema predicates from their query, rather than
        # from a fragile call-order queue.  JAX owns this migration and may
        # add another guarded preflight without invalidating the JXP harness.
        if "COLUMN_NAME='IGNORED'" in self._last_statement:
            return (0,)
        if "CHECK_CLAUSE FROM information_schema.CHECK_CONSTRAINTS" in self._last_statement:
            # JAX validates the expression of a managed constraint, not just
            # its name.  Return the canonical legacy clause for the one
            # constraint created by 003; every later 005 constraint is absent
            # from this pre-upgrade snapshot.  A numeric existence sentinel
            # would make the JAX fail-closed validation reject the harness.
            if self._last_parameters == ("chk_jax_project_scope_status",):
                return ("status IN ('ACTIVE','DISABLED')",)
            return None
        if "information_schema.STATISTICS" in self._last_statement:
            return (0,)
        if "SELECT COLUMN_TYPE" in self._last_statement:
            if self._last_parameters == ("projects", "status"):
                return ("enum('planning','active','paused','completed','archived')",)
            return None
        return None

    async def fetchall(self):
        # The newer JAX hook inspects index metadata as a complete ordered
        # shape.  This recorder models a schema with no pre-existing Office
        # list index, so the migration must create it.
        return ()


def test_jax_owned_003_hook_is_the_only_project_authority_ddl_source():
    """The platform invokes the exact JAX hook; it does not duplicate its DDL."""
    cursor = _RecordingCursor()
    asyncio.run(migrations._apply_jax_project_authority_migration(cursor))

    # The first five statements are exactly the JAX-owned 003 DDL.  Later
    # statements belong to JAX-owned 005, whose information-schema guards use
    # this cursor double as well.
    # 21 -> 23 (2026-10-02, Proyectos E1.1, Jax#321): la migracion 005i suma dos
    # sentencias, la comprobacion de existencia y el CREATE INDEX de
    # idx_jax_users_tenant_email en jax_users.  23 -> 24 (2026-10-03,
    # Proyectos E2a / JAX migration 006a): the JAX-owned hook appends its
    # canonical project_documents DDL after those statements.
    assert len(cursor.statements) >= 24
    assert "CREATE TABLE IF NOT EXISTS jax_project_scope" in cursor.statements[0]
    assert "CREATE TABLE IF NOT EXISTS jax_project_membership" in cursor.statements[1]
    assert "CREATE TABLE IF NOT EXISTS jax_project_membership_event" in cursor.statements[2]
    assert "append-only" in cursor.statements[3]
    assert "append-only" in cursor.statements[4]
    assert "CREATE TABLE IF NOT EXISTS project_documents" in cursor.statements[-1]
    assert any("idx_jax_project_scope_namespace_status_project" in sql for sql in cursor.statements)
    assert any(
        "SELECT CHECK_CLAUSE FROM information_schema.CHECK_CONSTRAINTS" in sql
        and parameters == ("chk_jax_project_scope_status",)
        for sql, parameters in cursor.check_constraint_queries
    )
    assert "jax_project_scope" not in inspect.getsource(migrations.run_migrations)


def test_jax_owned_b9_core_001_002_are_loaded_in_tracked_order_not_copied():
    """The platform runner executes the reviewed JAX core DDL before 003."""
    statements = migrations._jax_b9_core_migration_statements()

    assert "CREATE TABLE IF NOT EXISTS memory_objects" in statements[0]
    assert any("CREATE TABLE IF NOT EXISTS memory_revision_payloads" in sql for sql in statements)
    assert any("CREATE TRIGGER no_update_memory_events" in sql for sql in statements)
    runner = inspect.getsource(migrations.run_migrations)
    assert "_apply_jax_b9_core_migrations(cur)" in runner
    assert runner.index("_apply_jax_b9_core_migrations(cur)") < runner.index(
        "_apply_jax_project_authority_migration(cur)"
    )


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
                "('memory_objects','memory_revisions','memory_provenance','memory_events',"
                "'memory_projections','memory_revision_payloads','memory_legacy_bindings',"
                "'embedding_spaces','embedding_generations','jax_project_scope',"
                "'jax_project_membership','jax_project_membership_event')"
            )
            tables = {row[0] for row in await cur.fetchall()}
            await cur.execute(
                "SELECT TABLE_NAME, COLUMN_NAME FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN "
                "('memory_objects','memory_revisions','memory_provenance','memory_events',"
                "'memory_projections','memory_revision_payloads','memory_legacy_bindings',"
                "'embedding_spaces','embedding_generations','jax_project_scope',"
                "'jax_project_membership','jax_project_membership_event')"
            )
            columns = {(row[0], row[1]) for row in await cur.fetchall()}
            await cur.execute(
                "SELECT TRIGGER_NAME FROM information_schema.TRIGGERS "
                "WHERE TRIGGER_SCHEMA = DATABASE() AND TRIGGER_NAME IN "
                "('no_update_jax_project_membership_event',"
                "'no_delete_jax_project_membership_event','no_update_memory_events',"
                "'no_delete_memory_events')"
            )
            triggers = {row[0] for row in await cur.fetchall()}
    return tables, columns, triggers


def test_supported_runner_applies_003_and_expected_schema_is_not_drifted(client):
    """Fresh bootstrap and re-run both pass through run_migrations' real chain."""
    client.portal.call(migrations.run_migrations)
    tables, columns, triggers = client.portal.call(_schema_snapshot)

    assert tables == {
        "memory_objects",
        "memory_revisions",
        "memory_provenance",
        "memory_events",
        "memory_projections",
        "memory_revision_payloads",
        "memory_legacy_bindings",
        "embedding_spaces",
        "embedding_generations",
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
        ("memory_objects", "memory_id"),
        ("memory_revisions", "project_id"),
        ("memory_events", "actor_type"),
        ("memory_events", "request_id"),
        ("memory_revision_payloads", "revision_id"),
    }.issubset(columns)
    assert triggers == {
        "no_update_jax_project_membership_event",
        "no_delete_jax_project_membership_event",
        "no_update_memory_events",
        "no_delete_memory_events",
    }
