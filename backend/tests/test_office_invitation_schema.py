"""Real-MariaDB contract for dormant 3C2 invitation persistence."""
from __future__ import annotations

import asyncio
import os
import secrets
import uuid

import aiomysql
import pytest

from db.connection import get_pool
from tests.entorno_de_produccion import cargar
from virtual_office.migrations import apply_office_migrations
from tests.test_office_mfa import _active_user, _step_up_proof
from tests.test_office_runtime_contract import _grant_exact_runtime_acl, _runtime_pool
from tests.fixtures.office_invitation_b44_guards import GUARD_STATEMENTS, SOURCE_COMMIT


async def _migration_admin_pool():
    """Use the dedicated migrator when provisioned, never the web principal."""
    migrator = cargar("/etc/jax/migrate.env")
    credentials = migrator or os.environ
    return await aiomysql.create_pool(
        host=credentials["JAX_DB_HOST"], port=int(credentials["JAX_DB_PORT"]),
        user=credentials["JAX_DB_USER"], password=credentials["JAX_DB_PASSWORD"],
        db=os.environ["JAX_DB_NAME"], charset="utf8mb4", autocommit=True,
        connect_timeout=10,
    )


async def _require_test_ddl_principal(pool, *, create_user: bool) -> None:
    """Skip locally when no disposable-schema administrator is configured."""
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SHOW GRANTS")
            grants = " ".join(str(row[0]).upper() for row in await cur.fetchall())
    has_global_create = " ON *.*" in grants and (
        "ALL PRIVILEGES" in grants or "CREATE" in grants
    )
    if not has_global_create or (create_user and "CREATE USER" not in grants and "ALL PRIVILEGES" not in grants):
        pool.close()
        await pool.wait_closed()
        pytest.skip("requires the test migrator/root principal for disposable DDL")


def test_invitation_tables_are_runtime_attested_and_keep_immutable_proof_separate(client):
    async def scenario():
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await apply_office_migrations(cur)
                await cur.execute(
                    "SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() "
                    "AND TABLE_NAME IN ('office_invitation_issuance','office_invitation','office_invitation_event','office_invitation_outbox','office_invitation_mutation_receipt')"
                )
                assert {row[0] for row in await cur.fetchall()} == {
                    'office_invitation_issuance', 'office_invitation',
                    'office_invitation_event', 'office_invitation_outbox',
                    'office_invitation_mutation_receipt',
                }
                await cur.execute(
                    "SELECT COLUMN_NAME,DATA_TYPE,IS_NULLABLE FROM information_schema.COLUMNS "
                    "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='office_invitation_issuance' "
                    "AND COLUMN_NAME IN ('token_hash','issuer_proof_event_id','issuer_mfa_version','issuance_authorization_event_id') "
                    "ORDER BY COLUMN_NAME"
                )
                assert await cur.fetchall() == (
                    ('issuance_authorization_event_id', 'bigint', 'NO'),
                    ('issuer_mfa_version', 'bigint', 'NO'),
                    ('issuer_proof_event_id', 'bigint', 'NO'),
                    ('token_hash', 'binary', 'NO'),
                )
                await cur.execute(
                    "SELECT COUNT(*) FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA=DATABASE() "
                    "AND TRIGGER_NAME IN ('no_update_office_invitation_issuance','no_delete_office_invitation_issuance',"
                    "'no_update_office_invitation_event','no_delete_office_invitation_event','guard_office_invitation_update',"
                    "'guard_office_invitation_insert','guard_office_invitation_event_insert','guard_office_invitation_outbox_insert','guard_office_invitation_outbox_update',"
                    "'no_update_office_invitation_mutation_receipt','no_delete_office_invitation_mutation_receipt')"
                )
                assert (await cur.fetchone())[0] == 11
                await cur.execute(
                    "SELECT COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() "
                    "AND TABLE_NAME='office_invitation_mutation_receipt' ORDER BY ORDINAL_POSITION")
                assert tuple(row[0] for row in await cur.fetchall()) == (
                    'idempotency_key', 'request_digest', 'operation', 'actor_user_id', 'project_id',
                    'invitation_id', 'expected_version', 'response_version', 'result_event_id',
                    'authorization_event_id', 'response_contract', 'expires_at', 'created_at')
    client.portal.call(scenario)


def test_invitation_attestation_rejects_a_homonymous_noop_issuance_guard(client):
    async def scenario():
        source = os.environ['JAX_DB_NAME']
        schema = f"office_invitation_attest_{uuid.uuid4().hex[:16]}"
        pool = await _migration_admin_pool()
        await _require_test_ddl_principal(pool, create_user=False)
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                try:
                    await cur.execute(f"CREATE DATABASE `{schema}`")
                    for table in (
                        'jax_users', 'jax_tenants', 'projects', 'jax_project_scope', 'user_admin_audit',
                        'jax_project_membership', 'jax_project_membership_event',
                        'jax_namespace_membership_mutation_request',
                    ):
                        await cur.execute(f"CREATE TABLE `{schema}`.`{table}` LIKE `{source}`.`{table}`")
                    await cur.execute(f"USE `{schema}`")
                    await apply_office_migrations(cur)
                    await cur.execute("DELETE FROM office_schema_revision WHERE component='virtual-office'")
                    await cur.execute("DROP TRIGGER no_update_office_invitation_issuance")
                    await cur.execute(
                        "CREATE TRIGGER no_update_office_invitation_issuance BEFORE UPDATE "
                        "ON office_invitation_issuance FOR EACH ROW SET NEW.issued_at=NEW.issued_at"
                    )
                    with pytest.raises(RuntimeError, match='office_invitation_issuance triggers are missing or invalid'):
                        await apply_office_migrations(cur)
                    await cur.execute("DROP TRIGGER no_update_office_invitation_issuance")
                    await cur.execute(
                        "CREATE TRIGGER no_update_office_invitation_issuance BEFORE UPDATE "
                        "ON office_invitation_issuance FOR EACH ROW SIGNAL SQLSTATE '45000' "
                        "SET MESSAGE_TEXT = 'office_invitation_issuance append-only'"
                    )
                    await cur.execute("ALTER TABLE office_invitation_outbox DROP CONSTRAINT chk_office_invitation_outbox_shape")
                    await cur.execute(
                        "ALTER TABLE office_invitation_outbox ADD CONSTRAINT chk_office_invitation_outbox_shape "
                        "CHECK (state IN ('QUEUED','CLAIMED','SMTP_ACCEPTED','UNKNOWN','CANCELLED'))"
                    )
                    with pytest.raises(RuntimeError, match='invitation check chk_office_invitation_outbox_shape is invalid'):
                        await apply_office_migrations(cur)
                finally:
                    await cur.execute(f"USE `{source}`")
                    await cur.execute(f"DROP DATABASE IF EXISTS `{schema}`")
    client.portal.call(scenario)


def test_invitation_attestation_rejects_extra_foreign_key_index_and_check(client):
    """The readiness stamp attests the complete shape, not just required names."""
    async def scenario():
        source = os.environ['JAX_DB_NAME']
        schema = f"office_invitation_extra_{uuid.uuid4().hex[:16]}"
        pool = await _migration_admin_pool()
        await _require_test_ddl_principal(pool, create_user=False)
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                try:
                    await cur.execute(f"CREATE DATABASE `{schema}`")
                    for table in (
                        'jax_users', 'jax_tenants', 'projects', 'jax_project_scope', 'user_admin_audit',
                        'jax_project_membership', 'jax_project_membership_event',
                        'jax_namespace_membership_mutation_request',
                    ):
                        await cur.execute(f"CREATE TABLE `{schema}`.`{table}` LIKE `{source}`.`{table}`")
                    await cur.execute(f"USE `{schema}`")
                    await apply_office_migrations(cur)
                    await cur.execute("DELETE FROM office_schema_revision WHERE component='virtual-office'")
                    await cur.execute(
                        "ALTER TABLE office_invitation_outbox ADD CONSTRAINT fk_extra_invitation "
                        "FOREIGN KEY (invitation_id) REFERENCES office_invitation(invitation_id)"
                    )
                    with pytest.raises(RuntimeError, match='foreign key set is invalid'):
                        await apply_office_migrations(cur)
                    await cur.execute("ALTER TABLE office_invitation_outbox DROP FOREIGN KEY fk_extra_invitation")
                    await cur.execute("CREATE INDEX idx_extra_invitation ON office_invitation_outbox (updated_at)")
                    with pytest.raises(RuntimeError, match='index set is invalid'):
                        await apply_office_migrations(cur)
                    await cur.execute("DROP INDEX idx_extra_invitation ON office_invitation_outbox")
                    await cur.execute(
                        "ALTER TABLE office_invitation_outbox ADD CONSTRAINT chk_extra_invitation "
                        "CHECK (attempt_count >= 0)"
                    )
                    with pytest.raises(RuntimeError, match='check set is invalid'):
                        await apply_office_migrations(cur)
                finally:
                    await cur.execute(f"USE `{source}`")
                    await cur.execute(f"DROP DATABASE IF EXISTS `{schema}`")
    client.portal.call(scenario)


def test_invitation_receipt_attestation_rejects_weaker_named_check(client):
    async def scenario():
        source = os.environ['JAX_DB_NAME']
        schema = f"office_invitation_receipt_check_{uuid.uuid4().hex[:16]}"
        pool = await _migration_admin_pool()
        await _require_test_ddl_principal(pool, create_user=False)
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                try:
                    await cur.execute(f"CREATE DATABASE `{schema}`")
                    for table in ('jax_users', 'jax_tenants', 'projects', 'jax_project_scope',
                                  'user_admin_audit', 'jax_project_membership',
                                  'jax_project_membership_event', 'jax_namespace_membership_mutation_request'):
                        await cur.execute(f"CREATE TABLE `{schema}`.`{table}` LIKE `{source}`.`{table}`")
                    await cur.execute(f"USE `{schema}`")
                    await apply_office_migrations(cur)
                    await cur.execute("DELETE FROM office_schema_revision WHERE component='virtual-office'")
                    await cur.execute(
                        "ALTER TABLE office_invitation_mutation_receipt "
                        "DROP CONSTRAINT chk_office_invitation_mutation_receipt_shape, "
                        "ADD CONSTRAINT chk_office_invitation_mutation_receipt_shape "
                        "CHECK (operation IN ('ISSUE','REVOKE'))")
                    with pytest.raises(RuntimeError, match='mutation receipt check is invalid'):
                        await apply_office_migrations(cur)
                finally:
                    await cur.execute(f"USE `{source}`")
                    await cur.execute(f"DROP DATABASE IF EXISTS `{schema}`")
    client.portal.call(scenario)


def test_invitation_outbox_is_the_only_mutable_delivery_table_in_runtime_acl():
    from db.runtime_privileges import runtime_table_privilege_map

    grants = runtime_table_privilege_map()
    assert grants['office_invitation'] == frozenset({'UPDATE'})
    assert grants['office_invitation_outbox'] == frozenset({'UPDATE'})
    for immutable in ('office_invitation_issuance', 'office_invitation_event',
                      'office_invitation_mutation_receipt'):
        assert immutable not in grants


def test_invitation_guard_upgrade_resumes_after_partial_drop_without_rewriting_rows(client):
    """A crash between guarded-trigger DDL statements resumes from a subset."""
    async def scenario():
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await apply_office_migrations(cur)
                issuer, enrollment, _ = await _active_user()
                proof = await _step_up_proof(issuer, enrollment.secret, b"U" * 16)
                await cur.execute(
                    "INSERT INTO office_account_profile (user_id,global_role,status) VALUES (%s,'COLLABORATOR','ACTIVE')",
                    (issuer,),
                )
                invitation_id, outbox_id, _, _ = await _seed_pending_invitation(
                    cur, issuer=issuer, proof_event_id=proof.event_id,
                    serial=f"resume-{uuid.uuid4().hex}", target_user_id=issuer,
                )
                await cur.execute("DROP TRIGGER guard_office_invitation_outbox_update")
                await apply_office_migrations(cur)
                await cur.execute(
                    "SELECT invitation_id,status FROM office_invitation WHERE invitation_id=%s", (invitation_id,))
                assert await cur.fetchone() == (invitation_id, 'ISSUED')
                await cur.execute(
                    "SELECT outbox_id,state,payload_ciphertext FROM office_invitation_outbox WHERE outbox_id=%s", (outbox_id,))
                row = await cur.fetchone()
                assert row[:2] == (outbox_id, 'QUEUED') and row[2] is not None
                await cur.execute(
                    "SELECT COUNT(*) FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA=DATABASE() "
                    "AND TRIGGER_NAME IN ('guard_office_invitation_insert','guard_office_invitation_update',"
                    "'guard_office_invitation_event_insert','guard_office_invitation_outbox_insert','guard_office_invitation_outbox_update')"
                )
                assert (await cur.fetchone())[0] == 5
    client.portal.call(scenario)


def test_b44_invitation_guards_upgrade_server_rendered_bodies_without_rewriting_rows(client):
    """The exact reviewed b44 bodies upgrade in MariaDB, preserving evidence."""
    assert SOURCE_COMMIT == "b44dd06cc9fe7cc88f8f83566be7c565f76e6675"
    b44_guards = GUARD_STATEMENTS

    async def scenario():
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await apply_office_migrations(cur)
                issuer, enrollment, _ = await _active_user()
                proof = await _step_up_proof(issuer, enrollment.secret, b"V" * 16)
                await cur.execute(
                    "INSERT INTO office_account_profile (user_id,global_role,status) VALUES (%s,'COLLABORATOR','ACTIVE')",
                    (issuer,),
                )
                invitation_id, outbox_id, issued_event, _ = await _seed_pending_invitation(
                    cur, issuer=issuer, proof_event_id=proof.event_id,
                    serial=f"b44-{uuid.uuid4().hex}", target_user_id=issuer,
                )
                for name in (
                    "guard_office_invitation_insert", "guard_office_invitation_update",
                    "guard_office_invitation_event_insert", "guard_office_invitation_outbox_insert",
                    "guard_office_invitation_outbox_update",
                ):
                    await cur.execute(f"DROP TRIGGER {name}")
                for statement in b44_guards:
                    await cur.execute(statement)
                await cur.execute(
                    "SELECT COUNT(*) FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA=DATABASE() "
                    "AND TRIGGER_NAME LIKE 'guard_office_invitation%'"
                )
                # The immutable request-receipt guard remains intact while
                # the historical lifecycle guards are replaced.
                assert (await cur.fetchone())[0] == 5
                await apply_office_migrations(cur)
                await cur.execute(
                    "SELECT invitation_id,status FROM office_invitation WHERE invitation_id=%s", (invitation_id,))
                assert await cur.fetchone() == (invitation_id, 'ISSUED')
                await cur.execute(
                    "SELECT outbox_id,state,payload_ciphertext FROM office_invitation_outbox WHERE outbox_id=%s", (outbox_id,))
                row = await cur.fetchone()
                assert row[:2] == (outbox_id, 'QUEUED') and row[2] is not None
                await cur.execute(
                    "SELECT event_kind FROM office_invitation_event WHERE event_id=%s", (issued_event,))
                assert await cur.fetchone() == ('ISSUED',)
                await cur.execute(
                    "SELECT COUNT(*) FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA=DATABASE() "
                    "AND TRIGGER_NAME IN ('guard_office_invitation_insert','guard_office_invitation_update',"
                    "'guard_office_invitation_event_insert','guard_office_invitation_outbox_insert','guard_office_invitation_outbox_update')"
                )
                assert (await cur.fetchone())[0] == 5
    client.portal.call(scenario)


async def _seed_pending_invitation(
    cur, *, issuer: int, proof_event_id: int, serial: str, target_user_id: int | None = None,
    expired: bool = False,
):
    """Insert the lawful issued state used to exercise DB lifecycle fences."""
    await cur.execute(
        "INSERT INTO projects (project_uuid,name,status) VALUES (UUID(),%s,'active')",
        (f"invite-lifecycle-{serial}",),
    )
    project_id = cur.lastrowid
    await cur.execute(
        "INSERT INTO jax_project_scope (project_id,tenant_id,authorization_namespace,status,created_at,created_by,updated_at) "
        "VALUES (%s,1,'OFFICE_CONFIDENTIAL','ACTIVE',UTC_TIMESTAMP(6),'test',UTC_TIMESTAMP(6))",
        (project_id,),
    )
    await cur.execute(
        "INSERT INTO office_project_grant (project_id,user_id,grant_role,audience,status,granted_by) "
        "VALUES (%s,%s,'COLLABORATOR','INTERNAL','ACTIVE',%s)",
        (project_id, issuer, issuer),
    )
    await cur.execute(
        "INSERT INTO office_authorization_event (actor_user_id,event_kind,operation,result,reason_code,occurred_at) "
        "VALUES (%s,'AUTHORIZATION','ADMINISTER_PROJECT_MEMBERS','ALLOWED','test',UTC_TIMESTAMP(6))",
        (issuer,),
    )
    authorization_event_id = cur.lastrowid
    issuance_id = str(uuid.uuid4())
    invitation_id = str(uuid.uuid4())
    outbox_id = str(uuid.uuid4())
    issued_at_sql = "DATE_SUB(UTC_TIMESTAMP(6),INTERVAL 2 DAY)" if expired else "UTC_TIMESTAMP(6)"
    expires_at_sql = "DATE_SUB(UTC_TIMESTAMP(6),INTERVAL 1 DAY)" if expired else "DATE_ADD(UTC_TIMESTAMP(6),INTERVAL 1 DAY)"
    await cur.execute(
        "INSERT INTO office_invitation_issuance (issuance_id,token_hash,email_normalized,tenant_id,project_id,recipient_group_id,target_user_id,target_account_class,grant_role,audience,issued_by_user_id,issuer_profile_version,issuer_grant_version,issuer_proof_event_id,issuer_mfa_version,issuer_mfa_at,issuance_authorization_event_id,proof_digest,correlation_id,issued_at,expires_at) "
        "VALUES (%s,UNHEX(SHA2(%s,256)),CONCAT(%s,'@example.invalid'),1,%s,NULL,%s,'OFFICE_INTERNAL','COLLABORATOR','INTERNAL',%s,1,1,%s,1,UTC_TIMESTAMP(6),%s,UNHEX(REPEAT('cd',32)),UNHEX(REPEAT('ef',16)),"
        + issued_at_sql + "," + expires_at_sql + ")",
        (issuance_id, issuance_id, serial, project_id, target_user_id, issuer, proof_event_id, authorization_event_id),
    )
    await cur.execute(
        "INSERT INTO office_invitation (invitation_id,issuance_id,status,version,project_id,email_normalized,target_user_id) "
        "VALUES (%s,%s,'ISSUED',1,%s,CONCAT(%s,'@example.invalid'),%s)",
        (invitation_id, issuance_id, project_id, serial, target_user_id),
    )
    await cur.execute(
        "INSERT INTO office_invitation_outbox (outbox_id,invitation_id,state,attempt_count,cancel_requested,payload_ciphertext,created_at,updated_at) "
        "VALUES (%s,%s,'QUEUED',0,FALSE,X'01',UTC_TIMESTAMP(6),UTC_TIMESTAMP(6))",
        (outbox_id, invitation_id),
    )
    await cur.execute(
        "INSERT INTO office_invitation_event (invitation_id,event_kind,actor_user_id,correlation_id,occurred_at) "
        "VALUES (%s,'ISSUED',%s,UNHEX(REPEAT('ef',16)),UTC_TIMESTAMP(6))",
        (invitation_id, issuer),
    )
    return invitation_id, outbox_id, cur.lastrowid, issuance_id


def test_revocation_and_expiry_stop_queued_or_claimed_outbox_and_erase_ciphertext(client):
    """A terminal invitation cannot leave a replayable delivery payload behind."""
    async def scenario():
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await apply_office_migrations(cur)
                issuer, enrollment, _ = await _active_user()
                proof = await _step_up_proof(issuer, enrollment.secret, b"L" * 16)
                await cur.execute(
                    "INSERT INTO office_account_profile (user_id,global_role,status) VALUES (%s,'COLLABORATOR','ACTIVE')",
                    (issuer,),
                )
                revoked_id, queued_outbox, revoked_issued, revoked_issuance = await _seed_pending_invitation(
                    cur, issuer=issuer, proof_event_id=proof.event_id, serial=f"revoke-{uuid.uuid4().hex}")
                expired_id, claimed_outbox, expired_issued, expired_issuance = await _seed_pending_invitation(
                    cur, issuer=issuer, proof_event_id=proof.event_id, serial=f"expire-{uuid.uuid4().hex}", target_user_id=issuer,
                    expired=True)
                await cur.execute(
                    "UPDATE office_invitation_outbox SET state='CLAIMED',attempt_id=%s,attempt_count=1,claimed_at=UTC_TIMESTAMP(6),updated_at=DATE_ADD(updated_at,INTERVAL 1 MICROSECOND) WHERE outbox_id=%s",
                    (str(uuid.uuid4()), claimed_outbox),
                )
                with pytest.raises(aiomysql.OperationalError, match='expiry evidence denied'):
                    await cur.execute(
                        "INSERT INTO office_invitation_event (invitation_id,event_kind,causation_event_id,correlation_id,occurred_at) "
                        "VALUES (%s,'EXPIRED',%s,UNHEX(REPEAT('aa',16)),UTC_TIMESTAMP(6))",
                        (revoked_id, revoked_issued),
                    )
                with pytest.raises(aiomysql.OperationalError, match='causal evidence denied'):
                    await cur.execute(
                        "INSERT INTO office_invitation_event (invitation_id,event_kind,causation_event_id,actor_user_id,correlation_id,occurred_at) "
                        "VALUES (%s,'REVOKED',%s,%s,UNHEX(REPEAT('ab',16)),UTC_TIMESTAMP(6))",
                        (revoked_id, expired_issued, issuer),
                    )
                with pytest.raises(aiomysql.OperationalError, match='bearer evidence denied'):
                    await cur.execute(
                        "INSERT INTO office_invitation_event (invitation_id,event_kind,causation_event_id,bearer_token_hash,correlation_id,occurred_at) "
                        "VALUES (%s,'INVITATION_BEARER_PRESENTED',%s,UNHEX(SHA2(%s,256)),UNHEX(REPEAT('ad',16)),UTC_TIMESTAMP(6))",
                        (revoked_id, expired_issued, revoked_issuance),
                    )
                await cur.execute(
                    "INSERT INTO office_invitation_event (invitation_id,event_kind,causation_event_id,bearer_token_hash,correlation_id,occurred_at) "
                    "VALUES (%s,'INVITATION_BEARER_PRESENTED',%s,UNHEX(SHA2(%s,256)),UNHEX(REPEAT('ae',16)),UTC_TIMESTAMP(6))",
                    (revoked_id, revoked_issued, revoked_issuance),
                )
                await cur.execute(
                    "INSERT INTO office_invitation_event (invitation_id,event_kind,causation_event_id,bearer_token_hash,correlation_id,occurred_at) "
                    "VALUES (%s,'INVITATION_BEARER_PRESENTED',%s,UNHEX(SHA2(%s,256)),UNHEX(REPEAT('a9',16)),UTC_TIMESTAMP(6))",
                    (expired_id, expired_issued, expired_issuance),
                )
                expired_bearer = cur.lastrowid
                with pytest.raises(aiomysql.OperationalError, match='acceptance event denied'):
                    await cur.execute(
                        "INSERT INTO office_invitation_event (invitation_id,event_kind,causation_event_id,actor_user_id,observed_outbox_state,correlation_id,occurred_at) "
                        "VALUES (%s,'ACCEPTED',%s,%s,'CLAIMED',UNHEX(REPEAT('a9',16)),UTC_TIMESTAMP(6))",
                        (expired_id, expired_bearer, issuer),
                    )
                with pytest.raises(aiomysql.IntegrityError):
                    await cur.execute(
                        "INSERT INTO office_invitation_event (invitation_id,event_kind,causation_event_id,bearer_token_hash,correlation_id,occurred_at) "
                        "VALUES (%s,'INVITATION_BEARER_PRESENTED',%s,UNHEX(SHA2(%s,256)),UNHEX(REPEAT('af',16)),UTC_TIMESTAMP(6))",
                        (revoked_id, revoked_issued, revoked_issuance),
                    )
                with pytest.raises(aiomysql.OperationalError, match='issuance binding denied'):
                    await cur.execute(
                        "INSERT INTO office_invitation (invitation_id,issuance_id,status,version,project_id,email_normalized,target_user_id) "
                        "SELECT %s,issuance_id,'ISSUED',1,project_id,email_normalized,NULL FROM office_invitation WHERE invitation_id=%s",
                        (str(uuid.uuid4()), expired_id),
                    )
                await cur.execute(
                    "INSERT INTO office_invitation_event (invitation_id,event_kind,causation_event_id,actor_user_id,correlation_id,occurred_at) "
                    "VALUES (%s,'REVOKED',%s,%s,UNHEX(REPEAT('ab',16)),UTC_TIMESTAMP(6))",
                    (revoked_id, revoked_issued, issuer),
                )
                await cur.execute(
                    "UPDATE office_invitation SET status='REVOKED',version=2,revoked_by_user_id=%s,revoked_at=UTC_TIMESTAMP(6) WHERE invitation_id=%s",
                    (issuer, revoked_id),
                )
                await cur.execute(
                    "INSERT INTO office_invitation_event (invitation_id,event_kind,causation_event_id,correlation_id,occurred_at) "
                    "VALUES (%s,'EXPIRED',%s,UNHEX(REPEAT('ac',16)),UTC_TIMESTAMP(6))",
                    (expired_id, expired_issued),
                )
                await cur.execute(
                    "UPDATE office_invitation SET status='EXPIRED',version=2 WHERE invitation_id=%s",
                    (expired_id,),
                )
                await cur.execute(
                    "SELECT state,cancel_requested,payload_ciphertext,attempt_id FROM office_invitation_outbox WHERE outbox_id=%s",
                    (queued_outbox,),
                )
                assert await cur.fetchone() == ('CANCELLED', 1, None, None)
                await cur.execute(
                    "SELECT state,cancel_requested,payload_ciphertext,attempt_id FROM office_invitation_outbox WHERE outbox_id=%s",
                    (claimed_outbox,),
                )
                claimed_state, cancelled, ciphertext, original_attempt = await cur.fetchone()
                assert (claimed_state, cancelled, ciphertext) == ('CLAIMED', 1, None)
                with pytest.raises(aiomysql.OperationalError, match='terminal immutable'):
                    await cur.execute(
                        "UPDATE office_invitation_outbox SET state='CLAIMED',attempt_id=%s,updated_at=DATE_ADD(updated_at,INTERVAL 1 MICROSECOND) WHERE outbox_id=%s",
                        (str(uuid.uuid4()), queued_outbox),
                    )
                with pytest.raises(aiomysql.OperationalError, match='terminal denied'):
                    await cur.execute(
                        "UPDATE office_invitation_outbox SET attempt_id=%s,updated_at=DATE_ADD(updated_at,INTERVAL 1 MICROSECOND) WHERE outbox_id=%s",
                        (str(uuid.uuid4()), claimed_outbox),
                    )
                assert original_attempt is not None
                await cur.execute(
                    "INSERT INTO office_invitation_event (invitation_id,event_kind,causation_event_id,observed_outbox_state,correlation_id,occurred_at) "
                    "VALUES (%s,'DELIVERY_OBSERVED',%s,'CANCELLED',UNHEX(REPEAT('b1',16)),UTC_TIMESTAMP(6))",
                    (revoked_id, revoked_issued),
                )
                await cur.execute(
                    "UPDATE office_invitation_outbox SET state='UNKNOWN',payload_ciphertext=NULL,result_code='ambiguous-after-expiry',"
                    "finished_at=UTC_TIMESTAMP(6),updated_at=DATE_ADD(updated_at,INTERVAL 1 MICROSECOND) "
                    "WHERE outbox_id=%s AND attempt_id=%s",
                    (claimed_outbox, original_attempt),
                )
                await cur.execute(
                    "INSERT INTO office_invitation_event (invitation_id,event_kind,causation_event_id,observed_outbox_state,correlation_id,occurred_at) "
                    "VALUES (%s,'DELIVERY_OBSERVED',%s,'UNKNOWN',UNHEX(REPEAT('b2',16)),UTC_TIMESTAMP(6))",
                    (expired_id, expired_issued),
                )
    client.portal.call(scenario)


def test_acceptance_stops_delivery_without_forging_transport_history(client):
    """Acceptance atomically cancels queued mail and fences a claimed send."""
    async def add_bearer(cur, invitation_id, issued_event, issuance_id, correlation):
        await cur.execute(
            "INSERT INTO office_invitation_event (invitation_id,event_kind,causation_event_id,bearer_token_hash,correlation_id,occurred_at) "
            "VALUES (%s,'INVITATION_BEARER_PRESENTED',%s,UNHEX(SHA2(%s,256)),%s,UTC_TIMESTAMP(6))",
            (invitation_id, issued_event, issuance_id, correlation),
        )
        return cur.lastrowid

    async def scenario():
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await apply_office_migrations(cur)
                issuer, enrollment, _ = await _active_user()
                other_user, _, _ = await _active_user()
                proof = await _step_up_proof(issuer, enrollment.secret, b"M" * 16)
                await cur.execute(
                    "INSERT INTO office_account_profile (user_id,global_role,status) VALUES (%s,'COLLABORATOR','ACTIVE')",
                    (issuer,),
                )
                queued_id, queued_outbox, queued_issued, queued_issuance = await _seed_pending_invitation(
                    cur, issuer=issuer, proof_event_id=proof.event_id,
                    serial=f"accepted-queued-{uuid.uuid4().hex}", target_user_id=issuer,
                )
                claimed_id, claimed_outbox, claimed_issued, claimed_issuance = await _seed_pending_invitation(
                    cur, issuer=issuer, proof_event_id=proof.event_id,
                    serial=f"accepted-claimed-{uuid.uuid4().hex}", target_user_id=issuer,
                )
                with pytest.raises(aiomysql.OperationalError, match='outbox initial state denied'):
                    await cur.execute(
                        "INSERT INTO office_invitation_outbox (outbox_id,invitation_id,state,attempt_id,attempt_count,"
                        "cancel_requested,payload_ciphertext,result_code,created_at,updated_at,finished_at) "
                        "VALUES (%s,%s,'UNKNOWN',%s,1,FALSE,NULL,'forged',UTC_TIMESTAMP(6),UTC_TIMESTAMP(6),UTC_TIMESTAMP(6))",
                        (str(uuid.uuid4()), queued_id, str(uuid.uuid4())),
                    )
                queued_bearer = await add_bearer(
                    cur, queued_id, queued_issued, queued_issuance, bytes.fromhex('11' * 16))
                with pytest.raises(aiomysql.OperationalError, match='acceptance event denied'):
                    await cur.execute(
                        "INSERT INTO office_invitation_event (invitation_id,event_kind,causation_event_id,actor_user_id,"
                        "observed_outbox_state,correlation_id,occurred_at) VALUES (%s,'ACCEPTED',%s,%s,'SMTP_ACCEPTED',%s,UTC_TIMESTAMP(6))",
                        (queued_id, queued_bearer, other_user, bytes.fromhex('11' * 16)),
                    )
                with pytest.raises(aiomysql.OperationalError, match='acceptance event denied'):
                    await cur.execute(
                        "INSERT INTO office_invitation_event (invitation_id,event_kind,causation_event_id,actor_user_id,"
                        "observed_outbox_state,correlation_id,occurred_at) VALUES (%s,'ACCEPTED',%s,%s,'SMTP_ACCEPTED',%s,UTC_TIMESTAMP(6))",
                        (queued_id, queued_bearer, issuer, bytes.fromhex('11' * 16)),
                    )
                await cur.execute(
                    "INSERT INTO office_invitation_event (invitation_id,event_kind,causation_event_id,actor_user_id,"
                    "observed_outbox_state,correlation_id,occurred_at) VALUES (%s,'ACCEPTED',%s,%s,'QUEUED',%s,UTC_TIMESTAMP(6))",
                    (queued_id, queued_bearer, issuer, bytes.fromhex('11' * 16)),
                )
                await cur.execute(
                    "UPDATE office_invitation SET status='ACCEPTED',version=2,accepted_user_id=%s,accepted_at=UTC_TIMESTAMP(6) "
                    "WHERE invitation_id=%s",
                    (issuer, queued_id),
                )
                await cur.execute(
                    "SELECT state,cancel_requested,payload_ciphertext FROM office_invitation_outbox WHERE outbox_id=%s",
                    (queued_outbox,),
                )
                assert await cur.fetchone() == ('CANCELLED', 1, None)

                await cur.execute(
                    "UPDATE office_invitation_outbox SET state='CLAIMED',attempt_id=%s,attempt_count=1,claimed_at=UTC_TIMESTAMP(6),"
                    "updated_at=DATE_ADD(updated_at,INTERVAL 1 MICROSECOND) WHERE outbox_id=%s",
                    (str(uuid.uuid4()), claimed_outbox),
                )
                claimed_bearer = await add_bearer(
                    cur, claimed_id, claimed_issued, claimed_issuance, bytes.fromhex('22' * 16))
                await cur.execute(
                    "INSERT INTO office_invitation_event (invitation_id,event_kind,causation_event_id,actor_user_id,"
                    "observed_outbox_state,correlation_id,occurred_at) VALUES (%s,'ACCEPTED',%s,%s,'CLAIMED',%s,UTC_TIMESTAMP(6))",
                    (claimed_id, claimed_bearer, issuer, bytes.fromhex('22' * 16)),
                )
                await cur.execute(
                    "UPDATE office_invitation SET status='ACCEPTED',version=2,accepted_user_id=%s,accepted_at=UTC_TIMESTAMP(6) "
                    "WHERE invitation_id=%s",
                    (issuer, claimed_id),
                )
                await cur.execute(
                    "SELECT state,cancel_requested,payload_ciphertext,attempt_id FROM office_invitation_outbox WHERE outbox_id=%s",
                    (claimed_outbox,),
                )
                state, cancel_requested, ciphertext, attempt_id = await cur.fetchone()
                assert (state, cancel_requested, ciphertext) == ('CLAIMED', 1, None)
                with pytest.raises(aiomysql.OperationalError, match='outbox terminal denied'):
                    await cur.execute(
                        "UPDATE office_invitation_outbox SET state='SMTP_ACCEPTED',cancel_requested=FALSE,payload_ciphertext=NULL,"
                        "message_id='forged-reset',result_code='250',finished_at=UTC_TIMESTAMP(6),"
                        "updated_at=DATE_ADD(updated_at,INTERVAL 1 MICROSECOND) WHERE outbox_id=%s AND attempt_id=%s",
                        (claimed_outbox, attempt_id),
                    )
                await cur.execute(
                    "UPDATE office_invitation_outbox SET state='SMTP_ACCEPTED',payload_ciphertext=NULL,message_id='accepted-after-cancel',"
                    "result_code='250',finished_at=UTC_TIMESTAMP(6),updated_at=DATE_ADD(updated_at,INTERVAL 1 MICROSECOND) "
                    "WHERE outbox_id=%s AND attempt_id=%s",
                    (claimed_outbox, attempt_id),
                )
                with pytest.raises(aiomysql.OperationalError, match='delivery evidence denied'):
                    await cur.execute(
                        "INSERT INTO office_invitation_event (invitation_id,event_kind,causation_event_id,observed_outbox_state,"
                        "correlation_id,occurred_at) VALUES (%s,'DELIVERY_OBSERVED',%s,'UNKNOWN',%s,UTC_TIMESTAMP(6))",
                        (claimed_id, claimed_issued, bytes.fromhex('23' * 16)),
                    )
                await cur.execute(
                    "INSERT INTO office_invitation_event (invitation_id,event_kind,causation_event_id,observed_outbox_state,"
                    "correlation_id,occurred_at) VALUES (%s,'DELIVERY_OBSERVED',%s,'SMTP_ACCEPTED',%s,UTC_TIMESTAMP(6))",
                    (claimed_id, claimed_issued, bytes.fromhex('24' * 16)),
                )
    client.portal.call(scenario)


def test_invitation_transition_and_outbox_fences_reject_direct_dml(client):
    """The normal web principal may UPDATE state, never invent its history."""
    async def scenario():
        pool = await get_pool()
        migration_admin = await _migration_admin_pool()
        await _require_test_ddl_principal(migration_admin, create_user=True)
        issuance = str(uuid.uuid4())
        invitation = str(uuid.uuid4())
        outbox = str(uuid.uuid4())
        attempt = str(uuid.uuid4())
        runtime_user = f"office_invitation_rt_{uuid.uuid4().hex[:12]}"
        runtime_password = secrets.token_urlsafe(24)
        runtime = None
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await apply_office_migrations(cur)
                issuer, enrollment, _ = await _active_user()
                proof = await _step_up_proof(issuer, enrollment.secret, b"I" * 16)
                await cur.execute("INSERT INTO office_account_profile (user_id,global_role,status) VALUES (%s,'COLLABORATOR','ACTIVE')", (issuer,))
                await cur.execute("INSERT INTO projects (project_uuid,name,status) VALUES (UUID(),%s,'active')", (f"invite-{issuance}",))
                project = cur.lastrowid
                await cur.execute("INSERT INTO jax_project_scope (project_id,tenant_id,authorization_namespace,status,created_at,created_by,updated_at) VALUES (%s,1,'OFFICE_CONFIDENTIAL','ACTIVE',UTC_TIMESTAMP(6),'test',UTC_TIMESTAMP(6))", (project,))
                await cur.execute("INSERT INTO office_project_grant (project_id,user_id,grant_role,audience,status,granted_by) VALUES (%s,%s,'COLLABORATOR','INTERNAL','ACTIVE',%s)", (project, issuer, issuer))
                await cur.execute("INSERT INTO office_authorization_event (actor_user_id,event_kind,operation,result,reason_code,occurred_at) VALUES (%s,'AUTHORIZATION','ADMINISTER_PROJECT_MEMBERS','ALLOWED','test',UTC_TIMESTAMP(6))", (issuer,))
                authorization_event = cur.lastrowid
                database = os.environ['JAX_DB_NAME']
                async with migration_admin.acquire() as admin_conn:
                    async with admin_conn.cursor() as admin_cur:
                        await admin_cur.execute(f"CREATE USER `{runtime_user}`@'%%' IDENTIFIED BY %s", (runtime_password,))
                        await _grant_exact_runtime_acl(admin_cur, database, runtime_user)
                runtime = await _runtime_pool(runtime_user, runtime_password, database)
                try:
                    await cur.execute(
                        "INSERT INTO office_invitation_issuance (issuance_id,token_hash,email_normalized,tenant_id,project_id,recipient_group_id,target_user_id,target_account_class,grant_role,audience,issued_by_user_id,issuer_profile_version,issuer_grant_version,issuer_proof_event_id,issuer_mfa_version,issuer_mfa_at,issuance_authorization_event_id,proof_digest,correlation_id,issued_at,expires_at) "
                        "VALUES (%s,UNHEX(SHA2(%s,256)),CONCAT(%s,'@example.invalid'),1,%s,NULL,NULL,'OFFICE_INTERNAL','COLLABORATOR','INTERNAL',%s,1,1,%s,1,UTC_TIMESTAMP(6),%s,UNHEX(REPEAT('cd',32)),UNHEX(REPEAT('ef',16)),UTC_TIMESTAMP(6),DATE_ADD(UTC_TIMESTAMP(6),INTERVAL 1 DAY))",
                        (issuance, issuance, issuance, project, issuer, proof.event_id, authorization_event),
                    )
                    await cur.execute(
                        "INSERT INTO office_invitation (invitation_id,issuance_id,status,version,project_id,email_normalized,target_user_id) VALUES (%s,%s,'ISSUED',1,%s,CONCAT(%s,'@example.invalid'),NULL)",
                        (invitation, issuance, project, issuance),
                    )
                    await cur.execute(
                        "INSERT INTO office_invitation_outbox (outbox_id,invitation_id,state,attempt_count,cancel_requested,payload_ciphertext,created_at,updated_at) VALUES (%s,%s,'QUEUED',0,FALSE,X'01',UTC_TIMESTAMP(6),UTC_TIMESTAMP(6))",
                        (outbox, invitation),
                    )
                    async with runtime.acquire() as runtime_conn:
                        async with runtime_conn.cursor() as runtime_cur:
                            with pytest.raises(aiomysql.OperationalError, match='outbox (identity|claim) denied'):
                                await runtime_cur.execute("UPDATE office_invitation_outbox SET state='SMTP_ACCEPTED',payload_ciphertext=NULL,message_id='fake',result_code='fake',finished_at=UTC_TIMESTAMP(6) WHERE outbox_id=%s", (outbox,))
                            with pytest.raises(aiomysql.OperationalError, match='outbox (identity|claim) denied'):
                                await runtime_cur.execute("UPDATE office_invitation_outbox SET payload_ciphertext=X'02' WHERE outbox_id=%s", (outbox,))
                            await runtime_cur.execute("UPDATE office_invitation_outbox SET state='CLAIMED',attempt_id=%s,attempt_count=1,claimed_at=UTC_TIMESTAMP(6),updated_at=UTC_TIMESTAMP(6) WHERE outbox_id=%s", (attempt, outbox))
                            with pytest.raises(aiomysql.OperationalError, match='outbox (identity|terminal) denied'):
                                await runtime_cur.execute("UPDATE office_invitation_outbox SET state='UNKNOWN',attempt_id=%s,payload_ciphertext=NULL,result_code='uncertain',finished_at=UTC_TIMESTAMP(6) WHERE outbox_id=%s", (str(uuid.uuid4()), outbox))
                            await runtime_cur.execute("UPDATE office_invitation_outbox SET state='SMTP_ACCEPTED',payload_ciphertext=NULL,message_id='smtp-message',result_code='250',finished_at=UTC_TIMESTAMP(6),updated_at=UTC_TIMESTAMP(6) WHERE outbox_id=%s", (outbox,))
                            with pytest.raises(aiomysql.OperationalError, match='(identity denied|terminal immutable)'):
                                await runtime_cur.execute("UPDATE office_invitation_outbox SET result_code='changed' WHERE outbox_id=%s", (outbox,))
                            with pytest.raises(aiomysql.OperationalError, match='acceptance evidence missing'):
                                await runtime_cur.execute("UPDATE office_invitation SET status='ACCEPTED',version=2,accepted_user_id=%s,accepted_at=UTC_TIMESTAMP(6) WHERE invitation_id=%s", (issuer, invitation))
                            with pytest.raises(aiomysql.OperationalError, match='bearer evidence denied'):
                                await runtime_cur.execute("INSERT INTO office_invitation_event (invitation_id,event_kind,bearer_token_hash,correlation_id,occurred_at) VALUES (%s,'INVITATION_BEARER_PRESENTED',UNHEX(REPEAT('00',32)),UNHEX(REPEAT('01',16)),UTC_TIMESTAMP(6))", (invitation,))
                    await cur.execute("INSERT INTO office_invitation_event (invitation_id,event_kind,actor_user_id,correlation_id,occurred_at) VALUES (%s,'ISSUED',%s,UNHEX(REPEAT('ef',16)),UTC_TIMESTAMP(6))", (invitation, issuer))
                    issued_event = cur.lastrowid
                    with pytest.raises(aiomysql.OperationalError, match='acceptance event denied'):
                        await cur.execute("INSERT INTO office_invitation_event (invitation_id,event_kind,causation_event_id,actor_user_id,observed_outbox_state,correlation_id,occurred_at) VALUES (%s,'ACCEPTED',%s,%s,'SMTP_ACCEPTED',UNHEX(REPEAT('01',16)),UTC_TIMESTAMP(6))", (invitation, issued_event, issuer))
                    await cur.execute("INSERT INTO office_invitation_event (invitation_id,event_kind,causation_event_id,bearer_token_hash,correlation_id,occurred_at) VALUES (%s,'INVITATION_BEARER_PRESENTED',%s,UNHEX(SHA2(%s,256)),UNHEX(REPEAT('01',16)),UTC_TIMESTAMP(6))", (invitation, issued_event, issuance))
                    bearer_event = cur.lastrowid
                    with pytest.raises(aiomysql.OperationalError, match='acceptance event denied'):
                        await cur.execute("INSERT INTO office_invitation_event (invitation_id,event_kind,causation_event_id,actor_user_id,observed_outbox_state,correlation_id,occurred_at) VALUES (%s,'ACCEPTED',%s,%s,'SMTP_ACCEPTED',UNHEX(REPEAT('02',16)),UTC_TIMESTAMP(6))", (invitation, bearer_event, issuer))
                    await cur.execute("INSERT INTO office_invitation_event (invitation_id,event_kind,causation_event_id,actor_user_id,observed_outbox_state,correlation_id,occurred_at) VALUES (%s,'ACCEPTED',%s,%s,'SMTP_ACCEPTED',UNHEX(REPEAT('01',16)),UTC_TIMESTAMP(6))", (invitation, bearer_event, issuer))
                    async with runtime.acquire() as runtime_conn:
                        async with runtime_conn.cursor() as runtime_cur:
                            await runtime_cur.execute("UPDATE office_invitation SET status='ACCEPTED',version=2,accepted_user_id=%s,accepted_at=UTC_TIMESTAMP(6) WHERE invitation_id=%s", (issuer, invitation))
                            with pytest.raises(aiomysql.OperationalError, match='transition denied'):
                                await runtime_cur.execute("UPDATE office_invitation SET status='REVOKED',version=3,accepted_user_id=NULL,accepted_at=NULL,revoked_by_user_id=%s,revoked_at=UTC_TIMESTAMP(6) WHERE invitation_id=%s", (issuer, invitation))
                            with pytest.raises(aiomysql.OperationalError):
                                await runtime_cur.execute("UPDATE office_invitation_issuance SET issuer_grant_version=2 WHERE issuance_id=%s", (issuance,))
                            with pytest.raises(aiomysql.OperationalError):
                                await runtime_cur.execute("UPDATE office_invitation_event SET event_kind='EXPIRED' WHERE invitation_id=%s", (invitation,))
                finally:
                    if runtime is not None:
                        runtime.close(); await runtime.wait_closed()
                    async with migration_admin.acquire() as admin_conn:
                        async with admin_conn.cursor() as admin_cur:
                            await admin_cur.execute(
                                "SELECT ID FROM information_schema.PROCESSLIST WHERE USER=%s",
                                (runtime_user,),
                            )
                            for (connection_id,) in await admin_cur.fetchall():
                                try:
                                    await admin_cur.execute(f"KILL CONNECTION {int(connection_id)}")
                                except aiomysql.OperationalError as exc:
                                    # PROCESSLIST is only a snapshot: a pool connection can
                                    # finish between discovery and KILL.  Any other cleanup
                                    # failure still fails the test.
                                    if exc.args[:1] != (1094,):
                                        raise
                            await admin_cur.execute(f"DROP USER IF EXISTS `{runtime_user}`@'%'")
                    migration_admin.close()
                    await migration_admin.wait_closed()
    client.portal.call(scenario)
