"""Real-MariaDB authority checks for the Virtual Office boundary.

These tests would fail if a classic membership, a revoked grant, or a role
name supplied by a caller were enough to enter OFFICE_CONFIDENTIAL data.
"""
from __future__ import annotations

import asyncio
import uuid

from db.transaccion import transaccion
from virtual_office.audit import record_authorization_event
from virtual_office import authority
from virtual_office.authority import authorize_office, authorize_office_in_cursor
from virtual_office.capabilities import OfficeOperation
from virtual_office.migrations import apply_office_migrations


async def _sql(statement, params=(), fetch=False):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(statement, params)
            return await cur.fetchall() if fetch else cur.lastrowid


async def _seed_office_fixture():
    await apply_office_migrations()
    tag = uuid.uuid4().hex[:12]
    tenant_a = await _sql("INSERT INTO jax_tenants (name, plan, status) VALUES (%s, 'test', 'active')", (f"office-a-{tag}",))
    tenant_b = await _sql("INSERT INTO jax_tenants (name, plan, status) VALUES (%s, 'test', 'active')", (f"office-b-{tag}",))
    actor = await _sql(
        "INSERT INTO jax_users (tenant_id,email,password_hash,role,status,account_class) VALUES (%s,%s,'x','operator','active','INTERNAL')",
        (tenant_a, f"office-actor-{tag}@example.invalid"),
    )
    outsider = await _sql(
        "INSERT INTO jax_users (tenant_id,email,password_hash,role,status,account_class) VALUES (%s,%s,'x','superadmin','active','INTERNAL')",
        (tenant_b, f"office-outsider-{tag}@example.invalid"),
    )
    project = await _sql("INSERT INTO projects (project_uuid,name,status) VALUES (UUID(),%s,'active')", (f"office-{tag}",))
    await _sql(
        "INSERT INTO jax_project_scope (project_id,tenant_id,authorization_namespace,status,created_at,created_by,updated_at) VALUES (%s,%s,'OFFICE_CONFIDENTIAL','ACTIVE',NOW(6),'test',NOW(6))",
        (project, tenant_a),
    )
    await _sql(
        "INSERT INTO jax_project_membership (membership_id,project_id,tenant_id,user_id,project_role,status,created_at,created_by,updated_at) VALUES (%s,%s,%s,%s,'CONTRIBUTOR','ACTIVE',NOW(6),'test',NOW(6))",
        (str(uuid.uuid4()), project, tenant_a, actor),
    )
    await _sql("INSERT INTO office_account_profile (user_id,global_role,status) VALUES (%s,'COLLABORATOR','ACTIVE')", (actor,))
    await _sql(
        "INSERT INTO office_project_grant (project_id,user_id,grant_role,audience,status,granted_by) VALUES (%s,%s,'COLLABORATOR','INTERNAL','ACTIVE',%s)",
        (project, actor, actor),
    )
    return {"tenant_a": tenant_a, "actor": actor, "outsider": outsider, "project": project}


def test_internal_member_needs_active_office_grant_and_revocation_is_re_read(client):
    async def scenario():
        fixture = await _seed_office_fixture()
        allowed = await authorize_office(None, fixture["actor"], OfficeOperation.VIEW_PROJECT, fixture["project"])
        assert allowed.allowed is True
        await _sql("UPDATE office_project_grant SET status='REVOKED' WHERE project_id=%s AND user_id=%s", (fixture["project"], fixture["actor"]))
        revoked = await authorize_office(None, fixture["actor"], OfficeOperation.VIEW_PROJECT, fixture["project"])
        assert revoked.allowed is False
        assert revoked.code == "office_grant_missing"
        await _sql("UPDATE office_project_grant SET status='ACTIVE' WHERE project_id=%s AND user_id=%s", (fixture["project"], fixture["actor"]))
        await _sql("UPDATE jax_project_membership SET status='REVOKED' WHERE project_id=%s AND user_id=%s", (fixture["project"], fixture["actor"]))
        assert (await authorize_office(None, fixture["actor"], OfficeOperation.VIEW_PROJECT, fixture["project"])).code == "office_grant_missing"
    client.portal.call(scenario)


def test_classic_admin_and_cross_tenant_user_are_denied_without_office_authority(client):
    async def scenario():
        fixture = await _seed_office_fixture()
        decision = await authorize_office(None, fixture["outsider"], OfficeOperation.VIEW_PROJECT, fixture["project"])
        assert decision.allowed is False
        assert decision.code == "office_profile_missing"
    client.portal.call(scenario)


def test_project_admin_can_manage_its_project_but_not_global_administration(client):
    async def scenario():
        fixture = await _seed_office_fixture()
        await _sql("UPDATE office_project_grant SET grant_role='PROJECT_ADMIN' WHERE project_id=%s AND user_id=%s", (fixture["project"], fixture["actor"]))
        project_decision = await authorize_office(None, fixture["actor"], OfficeOperation.MANAGE_PROJECT, fixture["project"])
        global_decision = await authorize_office(None, fixture["actor"], OfficeOperation.ADMINISTER_OFFICE)
        assert project_decision.allowed is True
        assert global_decision.allowed is False
        assert global_decision.code == "office_delegation_missing"
    client.portal.call(scenario)


def test_authority_grant_lookup_uses_index_without_sort_or_temporary_table(client):
    async def scenario():
        fixture = await _seed_office_fixture()
        rows = await _sql(
            "EXPLAIN SELECT g.grant_role,g.audience,g.status,m.status "
            "FROM office_project_grant g JOIN jax_project_membership m "
            "ON m.project_id=g.project_id AND m.user_id=g.user_id "
            "WHERE g.project_id=%s AND g.user_id=%s",
            (fixture["project"], fixture["actor"]), True)
        assert rows
        assert all(str(row[-1] or "") not in {"Using filesort", "Using temporary"} for row in rows)
        assert all(row[3] != "ALL" for row in rows)
    client.portal.call(scenario)


def test_missing_inactive_tenant_or_profile_fail_closed(client):
    async def scenario():
        fixture = await _seed_office_fixture()
        missing = await authorize_office(None, 2_000_000_000, OfficeOperation.VIEW_PROJECT, fixture["project"])
        assert missing.code == "office_actor_missing"
        await _sql("UPDATE jax_users SET status='inactive' WHERE user_id=%s", (fixture["actor"],))
        assert (await authorize_office(None, fixture["actor"], OfficeOperation.VIEW_PROJECT, fixture["project"])).code == "office_actor_inactive"
        await _sql("UPDATE jax_users SET status='active' WHERE user_id=%s", (fixture["actor"],))
        await _sql("UPDATE jax_tenants SET status='inactive' WHERE tenant_id=%s", (fixture["tenant_a"],))
        assert (await authorize_office(None, fixture["actor"], OfficeOperation.VIEW_PROJECT, fixture["project"])).code == "office_tenant_inactive"
        await _sql("UPDATE jax_tenants SET status='active' WHERE tenant_id=%s", (fixture["tenant_a"],))
        await _sql("DELETE FROM office_account_profile WHERE user_id=%s", (fixture["actor"],))
        assert (await authorize_office(None, fixture["actor"], OfficeOperation.VIEW_PROJECT, fixture["project"])).code == "office_profile_missing"
    client.portal.call(scenario)


def test_external_superuser_is_denied_before_global_or_project_role_bypass(client):
    async def scenario():
        fixture = await _seed_office_fixture()
        await _sql("UPDATE jax_users SET account_class='EXTERNAL_CLIENT' WHERE user_id=%s", (fixture["actor"],))
        await _sql("UPDATE office_account_profile SET global_role='OFFICE_SUPERUSER' WHERE user_id=%s", (fixture["actor"],))
        global_decision = await authorize_office(None, fixture["actor"], OfficeOperation.ADMINISTER_OFFICE)
        project_decision = await authorize_office(None, fixture["actor"], OfficeOperation.VIEW_PROJECT, fixture["project"])
        assert global_decision.code == "office_account_class_denied"
        assert project_decision.code == "office_account_class_denied"
    client.portal.call(scenario)


def test_classic_namespace_and_jax_owner_without_office_grant_are_denied(client):
    async def scenario():
        fixture = await _seed_office_fixture()
        await _sql("UPDATE office_account_profile SET global_role='OFFICE_SUPERUSER' WHERE user_id=%s", (fixture["actor"],))
        await _sql("UPDATE jax_project_scope SET authorization_namespace='CLASSIC' WHERE project_id=%s", (fixture["project"],))
        assert (await authorize_office(None, fixture["actor"], OfficeOperation.VIEW_PROJECT, fixture["project"])).code == "office_namespace_denied"
        await _sql("UPDATE jax_project_scope SET authorization_namespace='OFFICE_CONFIDENTIAL' WHERE project_id=%s", (fixture["project"],))
        await _sql("UPDATE office_account_profile SET global_role='COLLABORATOR' WHERE user_id=%s", (fixture["actor"],))
        await _sql("DELETE FROM office_project_grant WHERE project_id=%s AND user_id=%s", (fixture["project"], fixture["actor"]))
        await _sql("UPDATE jax_project_membership SET project_role='OWNER' WHERE project_id=%s AND user_id=%s", (fixture["project"], fixture["actor"]))
        assert (await authorize_office(None, fixture["actor"], OfficeOperation.VIEW_PROJECT, fixture["project"])).code == "office_grant_missing"
    client.portal.call(scenario)


def test_cursor_reauthorization_and_audit_rollback_are_atomic(client):
    class RollbackProbe(RuntimeError):
        pass

    async def scenario():
        fixture = await _seed_office_fixture()
        try:
            async with transaccion() as cur:
                decision = await authorize_office_in_cursor(cur, fixture["actor"], OfficeOperation.VIEW_PROJECT, fixture["project"])
                assert decision.allowed is True
                await record_authorization_event(cur, decision, correlation_id="rollback-test")
                raise RollbackProbe()
        except RollbackProbe:
            pass
        rows = await _sql("SELECT event_id FROM office_authorization_event WHERE correlation_id='rollback-test'", fetch=True)
        assert rows == ()
    client.portal.call(scenario)


def test_external_client_requires_its_active_group_and_client_published_grant(client):
    async def scenario():
        fixture = await _seed_office_fixture()
        external = await _sql(
            "INSERT INTO jax_users (tenant_id,email,password_hash,role,status,account_class) VALUES (%s,%s,'x','operator','active','EXTERNAL_CLIENT')",
            (fixture["tenant_a"], f"office-client-{uuid.uuid4().hex[:10]}@example.invalid"))
        await _sql("INSERT INTO office_account_profile (user_id,global_role,status) VALUES (%s,'COLLABORATOR','ACTIVE')", (external,))
        group = await _sql("INSERT INTO office_recipient_group (project_id,tenant_id,group_kind,status,created_by) VALUES (%s,%s,'CLIENT','ACTIVE',%s)", (fixture["project"], fixture["tenant_a"], fixture["actor"]))
        await _sql("INSERT INTO office_external_relation (project_id,user_id,recipient_group_id,status) VALUES (%s,%s,%s,'ACTIVE')", (fixture["project"], external, group))
        await _sql("INSERT INTO office_project_grant (project_id,user_id,grant_role,audience,status,granted_by) VALUES (%s,%s,'COLLABORATOR','CLIENT_PUBLISHED','ACTIVE',%s)", (fixture["project"], external, fixture["actor"]))
        assert (await authorize_office(None, external, OfficeOperation.VIEW_PROJECT, fixture["project"], group)).allowed is True
        assert (await authorize_office(None, external, OfficeOperation.VIEW_PROJECT, fixture["project"])).code == "office_account_class_denied"
        assert (await authorize_office(None, external, OfficeOperation.VIEW_PROJECT, fixture["project"], group + 1)).code == "office_external_relation_missing"
        await _sql("UPDATE office_external_relation SET status='REVOKED' WHERE project_id=%s AND user_id=%s", (fixture["project"], external))
        assert (await authorize_office(None, external, OfficeOperation.VIEW_PROJECT, fixture["project"], group)).code == "office_external_relation_missing"
        await _sql("UPDATE office_external_relation SET status='ACTIVE' WHERE project_id=%s AND user_id=%s", (fixture["project"], external))
        await _sql("UPDATE office_project_grant SET audience='INTERNAL' WHERE project_id=%s AND user_id=%s", (fixture["project"], external))
        assert (await authorize_office(None, external, OfficeOperation.VIEW_PROJECT, fixture["project"], group)).code == "office_grant_insufficient"
        await _sql("UPDATE office_project_grant SET audience='CLIENT_PUBLISHED' WHERE project_id=%s AND user_id=%s", (fixture["project"], external))
        await _sql("UPDATE office_recipient_group SET status='REVOKED' WHERE recipient_group_id=%s", (group,))
        assert (await authorize_office(None, external, OfficeOperation.VIEW_PROJECT, fixture["project"], group)).code == "office_external_relation_missing"
        await _sql("DELETE FROM office_account_profile WHERE user_id=%s", (external,))
        assert await _sql("SELECT account_class FROM jax_users WHERE user_id=%s", (external,), True) == (("EXTERNAL_CLIENT",),)
    client.portal.call(scenario)


def test_project_admin_cannot_use_professional_capability_without_explicit_professional_grant(client):
    async def scenario():
        fixture = await _seed_office_fixture()
        await _sql("UPDATE office_project_grant SET grant_role='PROJECT_ADMIN' WHERE project_id=%s AND user_id=%s", (fixture["project"], fixture["actor"]))
        decision = await authorize_office(None, fixture["actor"], OfficeOperation.PROFESSIONAL_ACTION, fixture["project"])
        assert decision.code == "office_professional_grant_missing"
    client.portal.call(scenario)


def test_unknown_namespace_fails_closed_even_for_internal_office_superuser(monkeypatch):
    async def actor_row(*_args, **_kwargs):
        return {"tenant_id": 1, "user_status": "active", "account_class": "INTERNAL", "tenant_status": "active", "global_role": "OFFICE_SUPERUSER", "profile_status": "ACTIVE"}

    async def scope_row(*_args, **_kwargs):
        return {"tenant_id": 1, "status": "ACTIVE", "namespace": "UNRECOGNIZED"}

    monkeypatch.setattr(authority.repository, "fetch_actor", actor_row)
    monkeypatch.setattr(authority.repository, "fetch_project_scope", scope_row)
    decision = asyncio.run(authority.authorize_office_in_cursor(object(), 7, OfficeOperation.VIEW_PROJECT, 9))
    assert decision.code == "office_namespace_denied"


def test_unknown_account_class_fails_closed_before_global_role(monkeypatch):
    async def actor_row(*_args, **_kwargs):
        return {"tenant_id": 1, "user_status": "active", "account_class": "UNKNOWN", "tenant_status": "active", "global_role": "OFFICE_SUPERUSER", "profile_status": "ACTIVE"}

    monkeypatch.setattr(authority.repository, "fetch_actor", actor_row)
    decision = asyncio.run(authority.authorize_office_in_cursor(object(), 7, OfficeOperation.ADMINISTER_OFFICE))
    assert decision.code == "office_account_class_denied"
