"""Office DDL is idempotent and guards audit history in real MariaDB."""
from __future__ import annotations

import asyncio

import aiomysql

from virtual_office.migrations import apply_office_migrations


async def _schema_state():
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await apply_office_migrations()
            await apply_office_migrations()
            await cur.execute("SELECT COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='jax_users' AND COLUMN_NAME='account_class'")
            column = await cur.fetchone()
            await cur.execute("SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME LIKE 'office_%'")
            tables = {row[0] for row in await cur.fetchall()}
            await cur.execute("SELECT TRIGGER_NAME FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA=DATABASE() AND TRIGGER_NAME IN ('no_update_office_authorization_event','no_delete_office_authorization_event')")
            triggers = {row[0] for row in await cur.fetchall()}
            await cur.execute("SELECT INDEX_NAME FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='office_project_grant'")
            indexes = {row[0] for row in await cur.fetchall()}
    return column, tables, triggers, indexes


def test_office_schema_is_idempotent_and_has_append_only_audit_and_scope_index(client):
    column, tables, triggers, indexes = client.portal.call(_schema_state)
    assert column == ('account_class',)
    assert {'office_account_profile', 'office_admin_delegation', 'office_project_grant', 'office_recipient_group', 'office_external_relation', 'office_professional_grant', 'office_authorization_event'} <= tables
    assert triggers == {'no_update_office_authorization_event', 'no_delete_office_authorization_event'}
    assert 'idx_office_project_grant_scope' in indexes


def test_office_authorization_audit_rejects_update_and_delete(client):
    async def scenario():
        from db.connection import get_pool
        await apply_office_migrations()
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
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
