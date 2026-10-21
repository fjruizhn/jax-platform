"""Office DDL is idempotent and guards audit history in real MariaDB."""
from __future__ import annotations

import asyncio

import aiomysql
import pytest

from virtual_office.migrations import apply_office_migrations


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
