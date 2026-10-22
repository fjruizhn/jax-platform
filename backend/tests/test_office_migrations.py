"""Office DDL is idempotent and guards audit history in real MariaDB."""
from __future__ import annotations

import asyncio
import os
import re
import uuid

import aiomysql
import pytest

from virtual_office.migrations import apply_office_migrations
from virtual_office.migrations import (
    _CANONICAL_AUDIT_SHAPE,
    _CANONICAL_MFA_CHECK_DIGESTS,
    _check_clause,
    _check_digest,
    _is_canonical_audit_shape,
)


def test_audit_check_comparison_preserves_binary_literal_case():
    assert _is_canonical_audit_shape(_CANONICAL_AUDIT_SHAPE)
    assert not _is_canonical_audit_shape(_CANONICAL_AUDIT_SHAPE.replace("office-audit-fence-v1", "OFFICE-AUDIT-FENCE-V1"))


async def _schema_state():
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await apply_office_migrations(cur)
            await apply_office_migrations(cur)
            await cur.execute("SELECT COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='jax_users' AND COLUMN_NAME='account_class'")
            column = await cur.fetchone()
            await cur.execute("SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME LIKE 'office_%'")
            tables = {row[0] for row in await cur.fetchall()}
            await cur.execute("SELECT TRIGGER_NAME FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA=DATABASE() AND TRIGGER_NAME IN ('no_update_office_authorization_event','no_delete_office_authorization_event')")
            triggers = {row[0] for row in await cur.fetchall()}
            await cur.execute("SELECT INDEX_NAME FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='office_project_grant'")
            indexes = {row[0] for row in await cur.fetchall()}
            await cur.execute("SELECT CONSTRAINT_NAME FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME IN ('fk_office_authorization_event_scope','fk_office_authorization_event_project','fk_office_authorization_event_tenant','fk_office_external_relation_group_project')")
            constraints = {row[0] for row in await cur.fetchall()}
    return column, tables, triggers, indexes, constraints


def test_office_schema_is_idempotent_and_has_append_only_audit_and_scope_index(client):
    column, tables, triggers, indexes, constraints = client.portal.call(_schema_state)
    assert column == ('account_class',)
    assert {'office_account_profile', 'office_admin_delegation', 'office_project_grant', 'office_recipient_group', 'office_external_relation', 'office_professional_grant', 'office_authorization_event'} <= tables
    assert triggers == {'no_update_office_authorization_event', 'no_delete_office_authorization_event'}
    assert 'idx_office_project_grant_scope' in indexes
    assert constraints == {'fk_office_authorization_event_scope', 'fk_office_authorization_event_project', 'fk_office_authorization_event_tenant', 'fk_office_external_relation_group_project'}


def test_office_authorization_audit_rejects_update_and_delete(client):
    async def scenario():
        from db.connection import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await apply_office_migrations(cur)
                await cur.execute(
                    "INSERT INTO office_authorization_event (actor_user_id,operation,result,reason_code,occurred_at) "
                    "VALUES (1,'VIEW_PROJECT','DENIED','test',NOW(6))")
                event_id = cur.lastrowid
                try:
                    await cur.execute("UPDATE office_authorization_event SET reason_code='changed' WHERE event_id=%s", (event_id,))
                except aiomysql.OperationalError:
                    pass
                else:
                    raise AssertionError('append-only audit accepted UPDATE')
                try:
                    await cur.execute("DELETE FROM office_authorization_event WHERE event_id=%s", (event_id,))
                except aiomysql.OperationalError:
                    pass
                else:
                    raise AssertionError('append-only audit accepted DELETE')
    client.portal.call(scenario)


def test_office_audit_rejects_noncanonical_global_tenant(client):
    async def scenario():
        from db.connection import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await apply_office_migrations(cur)
                with pytest.raises(aiomysql.IntegrityError):
                    await cur.execute(
                        "INSERT INTO office_authorization_event (actor_user_id,operation,tenant_id,result,reason_code,occurred_at) "
                        "VALUES (1,'ADMINISTER_OFFICE',2000000001,'DENIED','test',NOW(6))")
    client.portal.call(scenario)


def test_office_audit_check_discriminates_system_fence_from_authorization(client):
    async def scenario():
        from db.connection import get_pool
        pool = await get_pool()
        invalid = (
            "INSERT INTO office_authorization_event (actor_user_id,actor_principal,event_kind,fence_key,operation,result,reason_code,occurred_at) "
            "VALUES (1,NULL,'SYSTEM_FENCE','office-audit-fence-v1','VIEW_PROJECT','RECORDED','office_audit_fence_v1',NOW(6))",
            "INSERT INTO office_authorization_event (actor_user_id,actor_principal,event_kind,fence_key,operation,result,reason_code,occurred_at) "
            "VALUES (NULL,NULL,'AUTHORIZATION',NULL,'VIEW_PROJECT','DENIED','test',NOW(6))",
            "INSERT INTO office_authorization_event (actor_user_id,actor_principal,event_kind,fence_key,operation,result,reason_code,occurred_at) "
            "VALUES (1,NULL,'AUTHORIZATION',NULL,'AUDIT_FENCE_CANARY','DENIED','test',NOW(6))",
        )
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await apply_office_migrations(cur)
                for statement in invalid:
                    with pytest.raises(aiomysql.OperationalError) as error:
                        await cur.execute(statement)
                    assert error.value.args[0] == 4025
    client.portal.call(scenario)


def test_external_relation_cannot_bind_a_group_from_another_project(client):
    async def scenario():
        import uuid
        from db.connection import get_pool
        tag = uuid.uuid4().hex[:10]
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await apply_office_migrations(cur)
                await cur.execute("INSERT INTO jax_tenants (name,plan,status) VALUES (%s,'test','active')", (f"office-fk-{tag}",))
                tenant = cur.lastrowid
                await cur.execute("INSERT INTO jax_users (tenant_id,email,password_hash,account_class) VALUES (%s,%s,'x','EXTERNAL_CLIENT')", (tenant, f"office-fk-user-{tag}@example.invalid"))
                user = cur.lastrowid
                await cur.execute("INSERT INTO projects (project_uuid,name,status) VALUES (UUID(),%s,'active')", (f"office-fk-a-{tag}",))
                project_a = cur.lastrowid
                await cur.execute("INSERT INTO projects (project_uuid,name,status) VALUES (UUID(),%s,'active')", (f"office-fk-b-{tag}",))
                project_b = cur.lastrowid
                for project in (project_a, project_b):
                    await cur.execute("INSERT INTO jax_project_scope (project_id,tenant_id,authorization_namespace,status,created_at,created_by,updated_at) VALUES (%s,%s,'OFFICE_CONFIDENTIAL','ACTIVE',NOW(6),'test',NOW(6))", (project, tenant))
                await cur.execute("INSERT INTO office_recipient_group (project_id,tenant_id,group_kind,status,created_by) VALUES (%s,%s,'CLIENT','ACTIVE',1)", (project_b, tenant))
                foreign_group = cur.lastrowid
                with pytest.raises(aiomysql.IntegrityError):
                    await cur.execute("INSERT INTO office_external_relation (project_id,user_id,recipient_group_id,status) VALUES (%s,%s,%s,'ACTIVE')", (project_a, user, foreign_group))
    client.portal.call(scenario)


def test_upgrade_converts_prior_utf8_fence_columns_to_ascii_bin(client):
    async def scenario():
        from db.connection import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await apply_office_migrations(cur)
                await cur.execute("ALTER TABLE office_authorization_event MODIFY actor_principal VARCHAR(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL, MODIFY fence_key VARCHAR(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL")
                await apply_office_migrations(cur)
                await cur.execute("SELECT COLUMN_NAME,CHARACTER_SET_NAME,COLLATION_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='office_authorization_event' AND COLUMN_NAME IN ('actor_principal','fence_key') ORDER BY COLUMN_NAME")
                assert await cur.fetchall() == (("actor_principal", "ascii", "ascii_bin"), ("fence_key", "ascii", "ascii_bin"))
    client.portal.call(scenario)


def test_homonymous_audit_shape_constraint_fails_closed_without_readiness(client):
    async def scenario():
        from db.connection import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await apply_office_migrations(cur)
                await cur.execute("DELETE FROM office_schema_revision WHERE component='virtual-office'")
                await cur.execute("ALTER TABLE office_authorization_event DROP CONSTRAINT chk_office_authorization_event_shape")
                await cur.execute("ALTER TABLE office_authorization_event ADD CONSTRAINT chk_office_authorization_event_shape CHECK (event_kind IN ('AUTHORIZATION','SYSTEM_FENCE'))")
                with pytest.raises(RuntimeError, match="shape constraint is incompatible"):
                    await apply_office_migrations(cur)
                await cur.execute("SELECT 1 FROM office_schema_revision WHERE component='virtual-office'")
                assert await cur.fetchone() is None
                await cur.execute("DELETE FROM office_schema_revision WHERE component='virtual-office'")
                await cur.execute("ALTER TABLE office_authorization_event DROP CONSTRAINT chk_office_authorization_event_shape")
                await apply_office_migrations(cur)
        from db.migrations import run_migrations
        await run_migrations()
    client.portal.call(scenario)


@pytest.mark.parametrize("replacement", (
    "CHECK ((event_kind='AUTHORIZATION' AND actor_user_id IS NOT NULL AND actor_principal IS NULL AND fence_key IS NULL AND result IN ('ALLOWED','DENIED') AND operation <> 'AUDIT_FENCE_CANARY') OR (event_kind='SYSTEM_FENCE' AND actor_user_id IS NULL AND actor_principal IS NOT NULL AND BINARY actor_principal=BINARY 'system:office-schema-migrator' AND fence_key IS NOT NULL AND BINARY fence_key=BINARY 'office-audit-fence-v1' AND operation='AUDIT_FENCE_CANARY' AND requested_project_id IS NULL AND project_id IS NULL AND tenant_id IS NULL AND result='RECORDED' AND reason_code='office_audit_fence_v1' AND correlation_id IS NULL) OR 1=1)",
    "CHECK ((event_kind='AUTHORIZATION' OR actor_user_id IS NOT NULL OR actor_principal IS NULL OR fence_key IS NULL OR result IN ('ALLOWED','DENIED') OR operation <> 'AUDIT_FENCE_CANARY') OR (event_kind='SYSTEM_FENCE' OR actor_user_id IS NULL OR actor_principal IS NOT NULL OR BINARY actor_principal=BINARY 'system:office-schema-migrator' OR fence_key IS NOT NULL OR BINARY fence_key=BINARY 'office-audit-fence-v1' OR operation='AUDIT_FENCE_CANARY' OR requested_project_id IS NULL OR project_id IS NULL OR tenant_id IS NULL OR result='RECORDED' OR reason_code='office_audit_fence_v1' OR correlation_id IS NULL))",
))
def test_boolean_weakened_audit_shape_fails_closed(replacement, client):
    async def scenario():
        from db.connection import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await apply_office_migrations(cur)
                await cur.execute("DELETE FROM office_schema_revision WHERE component='virtual-office'")
                await cur.execute("ALTER TABLE office_authorization_event DROP CONSTRAINT chk_office_authorization_event_shape")
                await cur.execute("ALTER TABLE office_authorization_event ADD CONSTRAINT chk_office_authorization_event_shape " + replacement)
                with pytest.raises(RuntimeError, match="shape constraint is incompatible"):
                    await apply_office_migrations(cur)
                await cur.execute("SELECT 1 FROM office_schema_revision WHERE component='virtual-office'")
                assert await cur.fetchone() is None
                await cur.execute("ALTER TABLE office_authorization_event DROP CONSTRAINT chk_office_authorization_event_shape")
                await apply_office_migrations(cur)
        from db.migrations import run_migrations
        await run_migrations()
    client.portal.call(scenario)


def test_weakened_result_constraint_fails_closed(client):
    async def scenario():
        from db.connection import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await apply_office_migrations(cur)
                await cur.execute("DELETE FROM office_schema_revision WHERE component='virtual-office'")
                await cur.execute("ALTER TABLE office_authorization_event DROP CONSTRAINT chk_office_authorization_event_result")
                await cur.execute("ALTER TABLE office_authorization_event ADD CONSTRAINT chk_office_authorization_event_result CHECK (result IN ('ALLOWED','DENIED','RECORDED') OR 1=1)")
                with pytest.raises(RuntimeError, match="result constraint is incompatible"):
                    await apply_office_migrations(cur)
                await cur.execute("SELECT 1 FROM office_schema_revision WHERE component='virtual-office'")
                assert await cur.fetchone() is None
                await cur.execute("ALTER TABLE office_authorization_event DROP CONSTRAINT chk_office_authorization_event_result")
                await apply_office_migrations(cur)
        from db.migrations import run_migrations
        await run_migrations()
    client.portal.call(scenario)


def test_mfa_ledger_fresh_schema_and_legacy_challenge_upgrade_are_idempotent(client):
    """Exercise fresh DDL and the pre-surrogate challenge shape in an owned schema."""
    async def scenario():
        from db.connection import get_pool

        source = os.environ["JAX_DB_NAME"]
        assert re.fullmatch(r"[A-Za-z0-9_]+", source)
        schema = f"office_mfa_upgrade_{uuid.uuid4().hex[:16]}"
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                try:
                    await cur.execute(f"CREATE DATABASE `{schema}`")
                    for table in ("jax_users", "jax_tenants", "projects", "jax_project_scope", "user_admin_audit"):
                        await cur.execute(f"CREATE TABLE `{schema}`.`{table}` LIKE `{source}`.`{table}`")
                    await cur.execute(f"USE `{schema}`")
                    class CrashAfterFreshLedgerCreate:
                        def __init__(self, delegate):
                            self._delegate = delegate

                        async def execute(self, statement, *args):
                            result = await self._delegate.execute(statement, *args)
                            if "CREATE TABLE IF NOT EXISTS office_mfa_security_event" in statement:
                                raise ConnectionError("injected post-CREATE disconnect")
                            return result

                        def __getattr__(self, name):
                            return getattr(self._delegate, name)

                    with pytest.raises(ConnectionError, match="post-CREATE"):
                        await apply_office_migrations(CrashAfterFreshLedgerCreate(cur))
                    await apply_office_migrations(cur)
                    await apply_office_migrations(cur)
                    # A v4 budget index is a valid predecessor and must be
                    # replaced without breaking the FK's user-id support.
                    await cur.execute("ALTER TABLE office_mfa_challenge ADD KEY legacy_budget (user_id,state,expires_at)")
                    await cur.execute("ALTER TABLE office_mfa_challenge DROP INDEX idx_office_mfa_challenge_user")
                    await cur.execute("ALTER TABLE office_mfa_challenge RENAME INDEX legacy_budget TO idx_office_mfa_challenge_user")
                    await apply_office_migrations(cur)
                    await cur.execute(
                        "SELECT COLUMN_NAME FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE() "
                        "AND TABLE_NAME='office_mfa_challenge' AND INDEX_NAME='idx_office_mfa_challenge_user' ORDER BY SEQ_IN_INDEX"
                    )
                    assert tuple(row[0] for row in await cur.fetchall()) == (
                        "user_id", "purpose", "expected_token_version", "expected_mfa_version", "state", "expires_at",
                    )
                    # Same v4 columns are insufficient: predecessor metadata
                    # must also be non-unique, unprefixed, ascending BTREE and
                    # usable.  Preserve a FK-supporting user_id index while
                    # replacing the canonical budget index in this owned DB.
                    await cur.execute("ALTER TABLE office_mfa_challenge ADD KEY v4_test_fk_support (user_id)")
                    for definition in (
                        "UNIQUE KEY idx_office_mfa_challenge_user (user_id,state,expires_at,token_hash)",
                        "KEY idx_office_mfa_challenge_user (user_id,state(3),expires_at)",
                    ):
                        await cur.execute("ALTER TABLE office_mfa_challenge DROP INDEX idx_office_mfa_challenge_user")
                        await cur.execute(f"ALTER TABLE office_mfa_challenge ADD {definition}")
                        with pytest.raises(RuntimeError, match="budget index is incompatible"):
                            await apply_office_migrations(cur)
                        await cur.execute("ALTER TABLE office_mfa_challenge DROP INDEX idx_office_mfa_challenge_user")
                        await cur.execute("ALTER TABLE office_mfa_challenge ADD KEY idx_office_mfa_challenge_user (user_id,purpose,expected_token_version,expected_mfa_version,state,expires_at)")
                    await cur.execute("ALTER TABLE office_mfa_challenge ALTER INDEX idx_office_mfa_challenge_user IGNORED")
                    with pytest.raises(RuntimeError, match="budget index is incompatible"):
                        await apply_office_migrations(cur)
                    await cur.execute("ALTER TABLE office_mfa_challenge ALTER INDEX idx_office_mfa_challenge_user NOT IGNORED")
                    await cur.execute("ALTER TABLE office_mfa_challenge DROP INDEX v4_test_fk_support")
                    await cur.execute("ALTER TABLE office_mfa_challenge DROP CONSTRAINT chk_office_mfa_challenge_state")
                    await cur.execute("ALTER TABLE office_mfa_challenge ADD CONSTRAINT chk_office_mfa_challenge_state CHECK (state IN ('ISSUED','CONSUMED','EXHAUSTED','EXPIRED','INVALIDATED') OR 1=1)")
                    with pytest.raises(RuntimeError, match="challenge state is incompatible"):
                        await apply_office_migrations(cur)
                    await cur.execute("ALTER TABLE office_mfa_challenge DROP CONSTRAINT chk_office_mfa_challenge_state")
                    await cur.execute("ALTER TABLE office_mfa_challenge ADD CONSTRAINT chk_office_mfa_challenge_state CHECK (state IN ('ISSUED','CONSUMED','EXHAUSTED','EXPIRED','INVALIDATED'))")
                    await cur.execute("ALTER TABLE office_mfa_security_event DROP FOREIGN KEY fk_office_mfa_security_event_challenge_subject")
                    await cur.execute("ALTER TABLE office_mfa_challenge DROP INDEX uq_office_mfa_challenge_subject")
                    await cur.execute("ALTER TABLE office_mfa_challenge DROP COLUMN challenge_id")
                    await apply_office_migrations(cur)
                    await cur.execute(
                        "SELECT COLUMN_NAME,IS_NULLABLE,EXTRA FROM information_schema.COLUMNS "
                        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='office_mfa_challenge' "
                        "AND COLUMN_NAME='challenge_id'"
                    )
                    assert await cur.fetchone() == ("challenge_id", "NO", "auto_increment")
                    await cur.execute(
                        "SELECT 1 FROM information_schema.TABLE_CONSTRAINTS "
                        "WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='office_mfa_security_event' "
                        "AND CONSTRAINT_NAME='fk_office_mfa_security_event_challenge_subject'"
                    )
                    assert await cur.fetchone() == (1,)
                    await apply_office_migrations(cur)
                    # This is the real fresh-ledger ordering case: an older
                    # challenge table survives, while the ledger does not.
                    # The new ledger FK cannot be declared until the
                    # challenge surrogate and composite uniqueness exist.
                    await cur.execute("DROP TRIGGER no_update_office_bootstrap_state")
                    await cur.execute("DROP TRIGGER no_delete_office_bootstrap_state")
                    await cur.execute("DROP TABLE office_bootstrap_state")
                    await cur.execute("DROP TRIGGER no_update_office_mfa_security_event")
                    await cur.execute("DROP TRIGGER no_delete_office_mfa_security_event")
                    await cur.execute("DROP TABLE office_mfa_security_event")
                    await cur.execute("ALTER TABLE office_mfa_challenge DROP INDEX uq_office_mfa_challenge_subject")
                    await cur.execute("ALTER TABLE office_mfa_challenge DROP COLUMN challenge_id")
                    await apply_office_migrations(cur)
                    await cur.execute(
                        "SELECT 1 FROM information_schema.TABLE_CONSTRAINTS "
                        "WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='office_mfa_security_event' "
                        "AND CONSTRAINT_NAME='fk_office_mfa_security_event_challenge_subject'"
                    )
                    assert await cur.fetchone() == (1,)
                    await apply_office_migrations(cur)
                finally:
                    await cur.execute(f"USE `{source}`")
                    await cur.execute(f"DROP DATABASE IF EXISTS `{schema}`")
    client.portal.call(scenario)


def test_mfa_schema_upgrade_resumes_only_attested_ddl_interruptions(client):
    """DDL commits implicitly, so each resumable interruption needs a durable attestation."""
    async def scenario():
        from db.connection import get_pool

        source = os.environ["JAX_DB_NAME"]
        schema = f"office_mfa_resume_{uuid.uuid4().hex[:16]}"
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                try:
                    await cur.execute(f"CREATE DATABASE `{schema}`")
                    for table in ("jax_users", "jax_tenants", "projects", "jax_project_scope", "user_admin_audit"):
                        await cur.execute(f"CREATE TABLE `{schema}`.`{table}` LIKE `{source}`.`{table}`")
                    await cur.execute(f"USE `{schema}`")
                    await apply_office_migrations(cur)
                    # The rejected DML journal has no authority.  A successful
                    # migration removes a leftover development table only after
                    # the physical contracts have been attested.
                    await cur.execute("CREATE TABLE office_mfa_schema_transition (transition_name VARCHAR(64) NOT NULL PRIMARY KEY, expected_digest CHAR(64) NOT NULL, started_at DATETIME(6) NOT NULL)")
                    await apply_office_migrations(cur)
                    await cur.execute("SELECT 1 FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='office_mfa_schema_transition'")
                    assert await cur.fetchone() is None

                    # The exact temporary v5 witness can resume the legacy
                    # index-only interruption; no DML journal participates.
                    await cur.execute("ALTER TABLE office_mfa_challenge ADD KEY legacy_budget (user_id,state,expires_at)")
                    await cur.execute("ALTER TABLE office_mfa_challenge DROP INDEX idx_office_mfa_challenge_user")
                    await cur.execute("ALTER TABLE office_mfa_challenge RENAME INDEX legacy_budget TO idx_office_mfa_challenge_user")
                    await cur.execute("ALTER TABLE office_mfa_challenge ADD KEY idx_office_mfa_challenge_budget_v5 (user_id,purpose,expected_token_version,expected_mfa_version,state,expires_at)")
                    await apply_office_migrations(cur)

                    # The legacy temporary-only state is also resumable.
                    await cur.execute("ALTER TABLE office_mfa_challenge ADD KEY idx_office_mfa_challenge_budget_v5 (user_id,purpose,expected_token_version,expected_mfa_version,state,expires_at)")
                    await cur.execute("ALTER TABLE office_mfa_challenge DROP INDEX idx_office_mfa_challenge_user")
                    await apply_office_migrations(cur)

                    # A missing CHECK is never a migration state. It fails
                    # closed and preserves the leftover table for diagnosis.
                    await cur.execute("DELETE FROM office_schema_revision WHERE component='virtual-office'")
                    await cur.execute("CREATE TABLE office_mfa_schema_transition (transition_name VARCHAR(64) NOT NULL PRIMARY KEY, expected_digest CHAR(64) NOT NULL, started_at DATETIME(6) NOT NULL)")
                    await cur.execute("ALTER TABLE office_mfa_challenge DROP CONSTRAINT chk_office_mfa_challenge_state")
                    with pytest.raises(RuntimeError, match="challenge state is incompatible"):
                        await apply_office_migrations(cur)
                    await cur.execute("SELECT 1 FROM office_schema_revision WHERE component='virtual-office'")
                    assert await cur.fetchone() is None
                    await cur.execute("SELECT 1 FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='office_mfa_schema_transition'")
                    assert await cur.fetchone() == (1,)

                    # A pre-existing ledger with a shape check and two absent
                    # contracts is not a fresh-create provenance. It
                    # must fail before consuming its diagnostic table or
                    # stamping readiness.
                    await cur.execute("ALTER TABLE office_mfa_challenge ADD CONSTRAINT chk_office_mfa_challenge_state CHECK (state IN ('ISSUED','CONSUMED','EXHAUSTED','EXPIRED','INVALIDATED'))")
                    await apply_office_migrations(cur)
                    await cur.execute("DELETE FROM office_schema_revision WHERE component='virtual-office'")
                    await cur.execute("CREATE TABLE office_mfa_schema_transition (transition_name VARCHAR(64) NOT NULL PRIMARY KEY, expected_digest CHAR(64) NOT NULL, started_at DATETIME(6) NOT NULL)")
                    await cur.execute("ALTER TABLE office_mfa_security_event DROP CONSTRAINT chk_office_mfa_security_event_fence")
                    await cur.execute("ALTER TABLE office_mfa_security_event DROP CONSTRAINT chk_office_mfa_security_event_truth")
                    with pytest.raises(RuntimeError, match="ledger CHECK history is incomplete"):
                        await apply_office_migrations(cur)
                    await cur.execute("SELECT 1 FROM office_schema_revision WHERE component='virtual-office'")
                    assert await cur.fetchone() is None
                    await cur.execute("SELECT 1 FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='office_mfa_schema_transition'")
                    assert await cur.fetchone() == (1,)
                finally:
                    await cur.execute(f"USE `{source}`")
                    await cur.execute(f"DROP DATABASE IF EXISTS `{schema}`")
    client.portal.call(scenario)


def test_mfa_v4_incompatible_evidence_fails_preflight_without_rewrite_or_readiness(client):
    async def scenario():
        from db.connection import get_pool

        source = os.environ["JAX_DB_NAME"]
        schema = f"office_mfa_preflight_{uuid.uuid4().hex[:16]}"
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                try:
                    await cur.execute(f"CREATE DATABASE `{schema}`")
                    for table in ("jax_users", "jax_tenants", "projects", "jax_project_scope", "user_admin_audit"):
                        await cur.execute(f"CREATE TABLE `{schema}`.`{table}` LIKE `{source}`.`{table}`")
                    await cur.execute(f"USE `{schema}`")
                    await apply_office_migrations(cur)
                    await cur.execute("SET SESSION check_constraint_checks=OFF")
                    await cur.execute("SET SESSION FOREIGN_KEY_CHECKS=0")
                    await cur.execute(
                        "INSERT INTO office_mfa_challenge "
                        "(token_hash,user_id,purpose,expected_token_version,expected_mfa_version,max_attempts,expires_at,state,created_at) "
                        "VALUES (UNHEX(REPLACE(UUID(),'-','')),1,'LOGIN',0,0,1,NOW(6),'INVALIDATED',NOW(6))"
                    )
                    await cur.execute("ALTER TABLE office_mfa_challenge DROP CONSTRAINT chk_office_mfa_challenge_state")
                    await cur.execute("ALTER TABLE office_mfa_challenge ADD CONSTRAINT chk_office_mfa_challenge_state CHECK (state IN ('ISSUED','CONSUMED','EXHAUSTED','EXPIRED'))")
                    await cur.execute("SET SESSION check_constraint_checks=ON")
                    await cur.execute("SET SESSION FOREIGN_KEY_CHECKS=1")
                    await cur.execute("DELETE FROM office_schema_revision WHERE component='virtual-office'")
                    with pytest.raises(RuntimeError, match="office_mfa_challenge rows are incompatible"):
                        await apply_office_migrations(cur)
                    await cur.execute("SELECT state FROM office_mfa_challenge")
                    assert await cur.fetchone() == ("INVALIDATED",)
                    await cur.execute("SELECT 1 FROM office_schema_revision WHERE component='virtual-office'")
                    assert await cur.fetchone() is None
                finally:
                    await cur.execute("SET SESSION check_constraint_checks=ON")
                    await cur.execute("SET SESSION FOREIGN_KEY_CHECKS=1")
                    await cur.execute(f"USE `{source}`")
                    await cur.execute(f"DROP DATABASE IF EXISTS `{schema}`")
    client.portal.call(scenario)


# Exact v4 evidence from ec09e22, retained here so this recovery test does not
# depend on an ignored planning fixture or a historical checkout at runtime.
_V4_MFA_CHECK_SQL = {
    "credential": "CHECK ((state='PENDING' AND secret_ciphertext IS NULL AND pending_secret_ciphertext IS NOT NULL AND pending_enrollment_id IS NOT NULL AND pending_expires_at IS NOT NULL) OR (state='ACTIVE' AND secret_ciphertext IS NOT NULL AND pending_secret_ciphertext IS NULL AND pending_enrollment_id IS NULL AND pending_expires_at IS NULL) OR (state IN ('RECOVERY_REQUIRED','DISABLED') AND secret_ciphertext IS NULL AND pending_secret_ciphertext IS NULL AND pending_enrollment_id IS NULL AND pending_expires_at IS NULL))",
    "challenge": "CHECK (state IN ('ISSUED','CONSUMED','EXHAUSTED','EXPIRED'))",
    "fence": "CHECK ((event_kind='SYSTEM_FENCE' AND fence_key IS NOT NULL AND BINARY fence_key=BINARY 'office-mfa-fence-v1') OR (event_kind IN ('MFA_PROOF','MFA_STATE_CHANGE') AND fence_key IS NULL))",
    "shape": "CHECK ((event_kind='MFA_PROOF' AND operation='MFA_PROOF' AND subject_user_id IS NOT NULL AND purpose IN ('LOGIN','STEP_UP','ENROLLMENT','RECOVERY') AND token_version >= 0 AND mfa_version >= 0 AND previous_token_version IS NULL AND previous_mfa_version IS NULL AND proof_event_id IS NULL AND authorization_event_id IS NULL AND result IN ('VERIFIED','DENIED') AND ((principal_kind='USER_SESSION' AND actor_user_id IS NOT NULL AND challenge_id IS NULL) OR (principal_kind IN ('PASSWORD_PREAUTH','RECOVERY_CAPABILITY','ENROLLMENT_CAPABILITY') AND actor_user_id IS NULL AND challenge_id IS NOT NULL))) OR (event_kind='MFA_STATE_CHANGE' AND subject_user_id IS NOT NULL AND operation IN ('ENROLLMENT_CONFIRM','RECOVERY_CONSUME','MFA_DISABLE','MFA_RESET') AND purpose IN ('ENROLLMENT','RECOVERY') AND token_version >= 0 AND mfa_version >= 0 AND previous_token_version >= 0 AND previous_mfa_version >= 0 AND proof_event_id IS NOT NULL AND result='RECORDED' AND ((principal_kind='USER_SESSION' AND actor_user_id IS NOT NULL) OR (principal_kind IN ('RECOVERY_CAPABILITY','ENROLLMENT_CAPABILITY') AND actor_user_id IS NULL AND challenge_id IS NOT NULL))) OR (event_kind='SYSTEM_FENCE' AND actor_user_id IS NULL AND principal_kind='SYSTEM_MIGRATOR' AND challenge_id IS NULL AND subject_user_id IS NULL AND operation IS NULL AND purpose IS NULL AND token_version IS NULL AND mfa_version IS NULL AND previous_token_version IS NULL AND previous_mfa_version IS NULL AND proof_event_id IS NULL AND authorization_event_id IS NULL AND result='RECORDED' AND server_correlation_id IS NULL AND fence_key IS NOT NULL AND BINARY fence_key=BINARY 'office-mfa-fence-v1'))",
    "truth": "CHECK (COALESCE((BINARY event_kind=BINARY 'SYSTEM_FENCE' AND actor_user_id IS NULL AND BINARY principal_kind=BINARY 'SYSTEM_MIGRATOR' AND challenge_id IS NULL AND subject_user_id IS NULL AND operation IS NULL AND purpose IS NULL AND token_version IS NULL AND mfa_version IS NULL AND previous_token_version IS NULL AND previous_mfa_version IS NULL AND proof_event_id IS NULL AND authorization_event_id IS NULL AND BINARY result=BINARY 'RECORDED' AND server_correlation_id IS NULL AND BINARY fence_key=BINARY 'office-mfa-fence-v1') OR (BINARY event_kind=BINARY 'MFA_PROOF' AND subject_user_id IS NOT NULL AND BINARY operation=BINARY 'MFA_PROOF' AND (BINARY purpose=BINARY 'LOGIN' OR BINARY purpose=BINARY 'STEP_UP' OR BINARY purpose=BINARY 'ENROLLMENT' OR BINARY purpose=BINARY 'RECOVERY') AND token_version IS NOT NULL AND token_version>=0 AND mfa_version IS NOT NULL AND mfa_version>=0 AND previous_token_version IS NULL AND previous_mfa_version IS NULL AND proof_event_id IS NULL AND authorization_event_id IS NULL AND (BINARY result=BINARY 'VERIFIED' OR BINARY result=BINARY 'DENIED') AND server_correlation_id IS NOT NULL AND fence_key IS NULL AND ((BINARY principal_kind=BINARY 'USER_SESSION' AND actor_user_id IS NOT NULL AND challenge_id IS NULL) OR ((BINARY principal_kind=BINARY 'PASSWORD_PREAUTH' OR BINARY principal_kind=BINARY 'RECOVERY_CAPABILITY' OR BINARY principal_kind=BINARY 'ENROLLMENT_CAPABILITY') AND actor_user_id IS NULL AND challenge_id IS NOT NULL))) OR (BINARY event_kind=BINARY 'MFA_STATE_CHANGE' AND subject_user_id IS NOT NULL AND (BINARY operation=BINARY 'ENROLLMENT_CONFIRM' OR BINARY operation=BINARY 'RECOVERY_CONSUME' OR BINARY operation=BINARY 'MFA_DISABLE' OR BINARY operation=BINARY 'MFA_RESET') AND (BINARY purpose=BINARY 'ENROLLMENT' OR BINARY purpose=BINARY 'RECOVERY') AND token_version IS NOT NULL AND token_version>=0 AND mfa_version IS NOT NULL AND mfa_version>=0 AND previous_token_version IS NOT NULL AND previous_token_version>=0 AND previous_mfa_version IS NOT NULL AND previous_mfa_version>=0 AND proof_event_id IS NOT NULL AND BINARY result=BINARY 'RECORDED' AND server_correlation_id IS NOT NULL AND fence_key IS NULL AND ((BINARY principal_kind=BINARY 'USER_SESSION' AND actor_user_id IS NOT NULL AND challenge_id IS NULL) OR ((BINARY principal_kind=BINARY 'RECOVERY_CAPABILITY' OR BINARY principal_kind=BINARY 'ENROLLMENT_CAPABILITY') AND actor_user_id IS NULL AND challenge_id IS NOT NULL))),FALSE))",
}

_V4_MFA_CHECK_DIGESTS = {
    "chk_office_mfa_security_event_fence": "2d6660b9a27057373a0ad21bbd9439aecae1c45288b562da51c4940fc28d28cf",
    "chk_office_mfa_security_event_shape": "8920714b7142b37c55ffc599c0a205bda7c16c1eab5cc30ca172a4aa1dca2ae4",
    "chk_office_mfa_security_event_truth": "a1f06b9717b4349ec37bc52ff16013e9653668b84771e66eb63ab93d4f3a291d",
}
_V4_CREDENTIAL_DIGEST = "62aabe4d405a17df1389ad19c54d2380074d85346a1a9e09bc905e296e96e01c"
_V4_CHALLENGE_DIGEST = "873de927db998ad7d6664ee6e849c3b09f6f3ec57afd20f1316fc90d8784eb9a"


@pytest.mark.parametrize(
    ("target", "timing"),
    (
        ("credential", "before"), ("credential", "after"),
        ("challenge", "before"), ("challenge", "after"),
        ("ledger", "before"), ("ledger", "after"),
        ("all", None),
    ),
)
def test_mfa_v4_check_upgrade_is_atomic_across_real_disconnects(target, timing, client):
    """A disconnected caller sees only exact v4 or exact v5 CHECK contracts."""
    async def digests(cur):
        return {
            "credential": _check_digest(await _check_clause(cur, "chk_office_mfa_credential_shape")),
            "challenge": _check_digest(await _check_clause(cur, "chk_office_mfa_challenge_state")),
            **{
                name: _check_digest(await _check_clause(cur, name))
                for name in _V4_MFA_CHECK_DIGESTS
            },
        }

    async def scenario():
        from db.connection import get_pool

        source = os.environ["JAX_DB_NAME"]
        assert re.fullmatch(r"[A-Za-z0-9_]+", source)
        schema = f"office_mfa_check_matrix_{uuid.uuid4().hex[:16]}"
        pool = await get_pool()
        conn = None
        cur = None
        try:
            conn = await pool.acquire()
            cur = await conn.cursor()
            await cur.execute(f"CREATE DATABASE `{schema}`")
            for table in ("jax_users", "jax_tenants", "projects", "jax_project_scope", "user_admin_audit"):
                await cur.execute(f"CREATE TABLE `{schema}`.`{table}` LIKE `{source}`.`{table}`")
            await cur.execute(f"USE `{schema}`")
            await apply_office_migrations(cur)
            await cur.execute("DELETE FROM office_schema_revision WHERE component='virtual-office'")
            await cur.execute(
                "ALTER TABLE office_mfa_credential DROP CONSTRAINT chk_office_mfa_credential_shape, "
                "ADD CONSTRAINT chk_office_mfa_credential_shape " + _V4_MFA_CHECK_SQL["credential"]
            )
            await cur.execute(
                "ALTER TABLE office_mfa_challenge DROP CONSTRAINT chk_office_mfa_challenge_state, "
                "ADD CONSTRAINT chk_office_mfa_challenge_state " + _V4_MFA_CHECK_SQL["challenge"]
            )
            await cur.execute(
                "ALTER TABLE office_mfa_security_event "
                "DROP CONSTRAINT chk_office_mfa_security_event_shape, "
                "DROP CONSTRAINT chk_office_mfa_security_event_truth, "
                "ADD CONSTRAINT chk_office_mfa_security_event_shape " + _V4_MFA_CHECK_SQL["shape"] + ", "
                "ADD CONSTRAINT chk_office_mfa_security_event_truth " + _V4_MFA_CHECK_SQL["truth"]
            )
            assert await digests(cur) == {
                "credential": _V4_CREDENTIAL_DIGEST,
                "challenge": _V4_CHALLENGE_DIGEST,
                **_V4_MFA_CHECK_DIGESTS,
            }

            if timing is None:
                await apply_office_migrations(cur)
            else:
                needles = {
                    "credential": "ALTER TABLE office_mfa_credential DROP CONSTRAINT chk_office_mfa_credential_shape",
                    "challenge": "ALTER TABLE office_mfa_challenge DROP CONSTRAINT chk_office_mfa_challenge_state",
                    "ledger": "ALTER TABLE office_mfa_security_event DROP CONSTRAINT chk_office_mfa_security_event_shape",
                }

                class DisconnectAtAtomicAlter:
                    def __init__(self, delegate):
                        self._delegate = delegate
                        self.calls = 0

                    async def execute(self, statement, *args):
                        if needles[target] in statement:
                            self.calls += 1
                            if timing == "before":
                                raise ConnectionError("injected pre-ALTER disconnect")
                            result = await self._delegate.execute(statement, *args)
                            raise ConnectionError("injected post-ALTER disconnect")
                        return await self._delegate.execute(statement, *args)

                    def __getattr__(self, name):
                        return getattr(self._delegate, name)

                injected = DisconnectAtAtomicAlter(cur)
                with pytest.raises(ConnectionError, match="disconnect"):
                    await apply_office_migrations(injected)
                assert injected.calls == 1
                await cur.close()
                cur = None
                conn.close()
                pool.release(conn)
                conn = await pool.acquire()
                cur = await conn.cursor()
                await cur.execute(f"USE `{schema}`")
                interrupted = await digests(cur)
                assert interrupted["credential"] in (_V4_CREDENTIAL_DIGEST, "8f6da6c31260662ef5833b216f54b834491089325e817ae1ceb7547f3935d8c9")
                assert interrupted["challenge"] in (_V4_CHALLENGE_DIGEST, "2ea7f4621b9352e2c26ae6f5bf2826ccbcd3bda1c748fe0b1dcf6ae625444dc3")
                assert interrupted["chk_office_mfa_security_event_fence"] == _V4_MFA_CHECK_DIGESTS["chk_office_mfa_security_event_fence"]
                ledger_pair = (
                    interrupted["chk_office_mfa_security_event_shape"],
                    interrupted["chk_office_mfa_security_event_truth"],
                )
                assert ledger_pair in (
                    (_V4_MFA_CHECK_DIGESTS["chk_office_mfa_security_event_shape"], _V4_MFA_CHECK_DIGESTS["chk_office_mfa_security_event_truth"]),
                    (_CANONICAL_MFA_CHECK_DIGESTS["chk_office_mfa_security_event_shape"], _CANONICAL_MFA_CHECK_DIGESTS["chk_office_mfa_security_event_truth"]),
                )
                await cur.execute("SELECT 1 FROM office_schema_revision WHERE component='virtual-office'")
                assert await cur.fetchone() is None
                await apply_office_migrations(cur)

            assert await digests(cur) == {
                "credential": "8f6da6c31260662ef5833b216f54b834491089325e817ae1ceb7547f3935d8c9",
                "challenge": "2ea7f4621b9352e2c26ae6f5bf2826ccbcd3bda1c748fe0b1dcf6ae625444dc3",
                **_CANONICAL_MFA_CHECK_DIGESTS,
            }
        finally:
            if cur is not None:
                await cur.close()
            if conn is not None:
                pool.release(conn)
            cleanup = await pool.acquire()
            cleanup_cur = await cleanup.cursor()
            try:
                await cleanup_cur.execute(f"USE `{source}`")
                await cleanup_cur.execute(f"DROP DATABASE IF EXISTS `{schema}`")
            finally:
                await cleanup_cur.close()
                pool.release(cleanup)

    client.portal.call(scenario)
