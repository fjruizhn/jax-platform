"""Real-MariaDB contract for dormant 3C2 invitation persistence."""
from __future__ import annotations

import asyncio
import os
import uuid

import aiomysql
import pytest

from db.connection import get_pool
from virtual_office.migrations import apply_office_migrations


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
                    "'no_update_office_invitation_event','no_delete_office_invitation_event')"
                )
                assert (await cur.fetchone())[0] == 4
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
