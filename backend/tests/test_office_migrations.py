"""Office DDL is idempotent and guards audit history in real MariaDB."""
from __future__ import annotations

import asyncio
import os
import re
import uuid

import aiomysql
import pytest

from virtual_office.migrations import apply_office_migrations
from virtual_office.migrations import _CANONICAL_AUDIT_SHAPE, _is_canonical_audit_shape


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
                    for table in ("jax_users", "jax_tenants", "projects", "jax_project_scope"):
                        await cur.execute(f"CREATE TABLE `{schema}`.`{table}` LIKE `{source}`.`{table}`")
                    await cur.execute(f"USE `{schema}`")
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
                    for table in ("jax_users", "jax_tenants", "projects", "jax_project_scope"):
                        await cur.execute(f"CREATE TABLE `{schema}`.`{table}` LIKE `{source}`.`{table}`")
                    await cur.execute(f"USE `{schema}`")
                    await apply_office_migrations(cur)

                    # Simulate a crash after a deliberate CHECK drop.  The
                    # journal makes this one missing constraint distinguishable
                    # from arbitrary tampering, and lets the runner resume.
                    await cur.execute(
                        "INSERT INTO office_mfa_schema_transition "
                        "(transition_name,expected_digest,started_at) VALUES "
                        "('credential-shape-v5','62aabe4d405a17df1389ad19c54d2380074d85346a1a9e09bc905e296e96e01c',NOW(6))"
                    )
                    await cur.execute("ALTER TABLE office_mfa_credential DROP CONSTRAINT chk_office_mfa_credential_shape")
                    await apply_office_migrations(cur)
                    await cur.execute("SELECT 1 FROM office_mfa_schema_transition WHERE transition_name='credential-shape-v5'")
                    assert await cur.fetchone() is None

                    # The other crash boundary is after the journal write but
                    # before ALTER starts: canonical DDL plus its old-digest
                    # attestation is a completed no-op, not a lingering seal.
                    await cur.execute(
                        "INSERT INTO office_mfa_schema_transition "
                        "(transition_name,expected_digest,started_at) VALUES "
                        "('credential-shape-v5','62aabe4d405a17df1389ad19c54d2380074d85346a1a9e09bc905e296e96e01c',NOW(6))"
                    )
                    await apply_office_migrations(cur)
                    await cur.execute("SELECT 1 FROM office_mfa_schema_transition WHERE transition_name='credential-shape-v5'")
                    assert await cur.fetchone() is None

                    # A challenge CHECK has the same recovery states.  Here
                    # the interruption lands after DROP, before ADD.
                    await cur.execute(
                        "INSERT INTO office_mfa_schema_transition "
                        "(transition_name,expected_digest,started_at) VALUES "
                        "('challenge-state-v5','873de927db998ad7d6664ee6e849c3b09f6f3ec57afd20f1316fc90d8784eb9a',NOW(6))"
                    )
                    await cur.execute("ALTER TABLE office_mfa_challenge DROP CONSTRAINT chk_office_mfa_challenge_state")
                    await apply_office_migrations(cur)
                    await cur.execute("SELECT 1 FROM office_mfa_schema_transition WHERE transition_name='challenge-state-v5'")
                    assert await cur.fetchone() is None

                    # An interruption after creating the temporary v5 index
                    # keeps the old v4 index and is atomically swapped.
                    await cur.execute("ALTER TABLE office_mfa_challenge ADD KEY legacy_budget (user_id,state,expires_at)")
                    await cur.execute("ALTER TABLE office_mfa_challenge DROP INDEX idx_office_mfa_challenge_user")
                    await cur.execute("ALTER TABLE office_mfa_challenge RENAME INDEX legacy_budget TO idx_office_mfa_challenge_user")
                    await cur.execute("ALTER TABLE office_mfa_challenge ADD KEY idx_office_mfa_challenge_budget_v5 (user_id,purpose,expected_token_version,expected_mfa_version,state,expires_at)")
                    await apply_office_migrations(cur)

                    # The previous non-atomic implementation could also leave
                    # only the measured temporary index.  Resume its known
                    # legacy state without reconstructing any rows.
                    await cur.execute("ALTER TABLE office_mfa_challenge ADD KEY idx_office_mfa_challenge_budget_v5 (user_id,purpose,expected_token_version,expected_mfa_version,state,expires_at)")
                    await cur.execute("ALTER TABLE office_mfa_challenge DROP INDEX idx_office_mfa_challenge_user")
                    await apply_office_migrations(cur)

                    # A missing CHECK without its transition attestation is
                    # indistinguishable from tampering and must never stamp
                    # readiness or silently repair the contract.
                    await cur.execute("DELETE FROM office_schema_revision WHERE component='virtual-office'")
                    await cur.execute("ALTER TABLE office_mfa_challenge DROP CONSTRAINT chk_office_mfa_challenge_state")
                    with pytest.raises(RuntimeError, match="challenge state is incompatible"):
                        await apply_office_migrations(cur)
                    await cur.execute("SELECT 1 FROM office_schema_revision WHERE component='virtual-office'")
                    assert await cur.fetchone() is None
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
                    for table in ("jax_users", "jax_tenants", "projects", "jax_project_scope"):
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
