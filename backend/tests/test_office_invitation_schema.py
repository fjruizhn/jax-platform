"""Real-MariaDB contract for dormant 3C2 invitation persistence."""
from __future__ import annotations

import asyncio
import os
import secrets
import uuid

import aiomysql
import pytest

from db.connection import get_pool
from virtual_office.migrations import apply_office_migrations
from tests.test_office_mfa import _active_user, _step_up_proof
from tests.test_office_runtime_contract import _grant_exact_runtime_acl, _runtime_pool


def test_invitation_tables_are_runtime_attested_and_keep_immutable_proof_separate(client):
    async def scenario():
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await apply_office_migrations(cur)
                await cur.execute(
                    "SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() "
                    "AND TABLE_NAME IN ('office_invitation_issuance','office_invitation','office_invitation_event','office_invitation_outbox')"
                )
                assert {row[0] for row in await cur.fetchall()} == {
                    'office_invitation_issuance', 'office_invitation',
                    'office_invitation_event', 'office_invitation_outbox',
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
                    "'guard_office_invitation_insert','guard_office_invitation_event_insert','guard_office_invitation_outbox_update')"
                )
                assert (await cur.fetchone())[0] == 8
    client.portal.call(scenario)


def test_invitation_attestation_rejects_a_homonymous_noop_issuance_guard(client):
    async def scenario():
        source = os.environ['JAX_DB_NAME']
        schema = f"office_invitation_attest_{uuid.uuid4().hex[:16]}"
        pool = await get_pool()
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


def test_invitation_outbox_is_the_only_mutable_delivery_table_in_runtime_acl():
    from db.runtime_privileges import runtime_table_privilege_map

    grants = runtime_table_privilege_map()
    assert grants['office_invitation'] == frozenset({'UPDATE'})
    assert grants['office_invitation_outbox'] == frozenset({'UPDATE'})
    for immutable in ('office_invitation_issuance', 'office_invitation_event'):
        assert immutable not in grants


def test_invitation_transition_and_outbox_fences_reject_direct_dml(client):
    """The normal web principal may UPDATE state, never invent its history."""
    async def scenario():
        pool = await get_pool()
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
                await cur.execute(f"CREATE USER `{runtime_user}`@'%%' IDENTIFIED BY %s", (runtime_password,))
                await _grant_exact_runtime_acl(cur, database, runtime_user)
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
                            with pytest.raises(aiomysql.OperationalError, match='outbox claim denied'):
                                await runtime_cur.execute("UPDATE office_invitation_outbox SET state='SMTP_ACCEPTED',payload_ciphertext=NULL,message_id='fake',result_code='fake',finished_at=UTC_TIMESTAMP(6) WHERE outbox_id=%s", (outbox,))
                            with pytest.raises(aiomysql.OperationalError, match='outbox claim denied'):
                                await runtime_cur.execute("UPDATE office_invitation_outbox SET payload_ciphertext=X'02' WHERE outbox_id=%s", (outbox,))
                            await runtime_cur.execute("UPDATE office_invitation_outbox SET state='CLAIMED',attempt_id=%s,attempt_count=1,claimed_at=UTC_TIMESTAMP(6) WHERE outbox_id=%s", (attempt, outbox))
                            with pytest.raises(aiomysql.OperationalError, match='outbox terminal denied'):
                                await runtime_cur.execute("UPDATE office_invitation_outbox SET state='UNKNOWN',attempt_id=%s,payload_ciphertext=NULL,result_code='uncertain',finished_at=UTC_TIMESTAMP(6) WHERE outbox_id=%s", (str(uuid.uuid4()), outbox))
                            await runtime_cur.execute("UPDATE office_invitation_outbox SET state='SMTP_ACCEPTED',payload_ciphertext=NULL,message_id='smtp-message',result_code='250',finished_at=UTC_TIMESTAMP(6) WHERE outbox_id=%s", (outbox,))
                            with pytest.raises(aiomysql.OperationalError, match='terminal immutable'):
                                await runtime_cur.execute("UPDATE office_invitation_outbox SET result_code='changed' WHERE outbox_id=%s", (outbox,))
                            with pytest.raises(aiomysql.OperationalError, match='acceptance evidence missing'):
                                await runtime_cur.execute("UPDATE office_invitation SET status='ACCEPTED',version=2,accepted_user_id=%s,accepted_at=UTC_TIMESTAMP(6) WHERE invitation_id=%s", (issuer, invitation))
                            with pytest.raises(aiomysql.OperationalError, match='bearer evidence denied'):
                                await runtime_cur.execute("INSERT INTO office_invitation_event (invitation_id,event_kind,bearer_token_hash,correlation_id,occurred_at) VALUES (%s,'INVITATION_BEARER_PRESENTED',UNHEX(REPEAT('00',32)),UNHEX(REPEAT('01',16)),UTC_TIMESTAMP(6))", (invitation,))
                    await cur.execute("INSERT INTO office_invitation_event (invitation_id,event_kind,actor_user_id,correlation_id,occurred_at) VALUES (%s,'ISSUED',%s,UNHEX(REPEAT('03',16)),UTC_TIMESTAMP(6))", (invitation, issuer))
                    await cur.execute("INSERT INTO office_invitation_event (invitation_id,event_kind,bearer_token_hash,correlation_id,occurred_at) VALUES (%s,'INVITATION_BEARER_PRESENTED',UNHEX(SHA2(%s,256)),UNHEX(REPEAT('01',16)),UTC_TIMESTAMP(6))", (invitation, issuance))
                    await cur.execute("INSERT INTO office_invitation_event (invitation_id,event_kind,actor_user_id,observed_outbox_state,correlation_id,occurred_at) VALUES (%s,'ACCEPTED',%s,'SMTP_ACCEPTED',UNHEX(REPEAT('02',16)),UTC_TIMESTAMP(6))", (invitation, issuer))
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
                    await cur.execute(f"DROP USER IF EXISTS `{runtime_user}`@'%'")
    client.portal.call(scenario)
