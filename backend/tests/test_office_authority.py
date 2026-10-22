"""Real-MariaDB authority checks for the Virtual Office boundary.

These tests would fail if a classic membership, a revoked grant, or a role
name supplied by a caller were enough to enter OFFICE_CONFIDENTIAL data.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import uuid

import aiomysql
import pyotp
import pytest

from db.transaccion import transaccion
from virtual_office.audit import record_authorization_event
from virtual_office import authority
from virtual_office.authority import authorize_office, authorize_office_in_cursor
from virtual_office.capabilities import OfficeOperation
from virtual_office.mfa import (
    MFAConfig, MFADenied, confirm_enrollment_in_cursor,
    issue_step_up_challenge_in_cursor, start_first_enrollment_after_password_in_cursor,
    verify_and_consume_totp_in_cursor,
)


_MFA_CONFIG = MFAConfig(120, 3, 300, 300, 3)


def _otp(secret: str, offset: int = 0) -> str:
    return pyotp.TOTP(secret, digits=6, interval=30).at(
        (dt.datetime.now().timestamp() // 30 + offset) * 30,
    )


async def _real_mfa_proof(user_id: int, *, step_up: bool = False):
    """Produce a DB-backed proof through the public cursor primitives."""
    async with transaccion() as cur:
        enrollment = await start_first_enrollment_after_password_in_cursor(
            cur, preauthenticated_user_id=user_id, config=_MFA_CONFIG,
        )
    async with transaccion() as cur:
        confirmed = await confirm_enrollment_in_cursor(
            cur, enrollment_capability_token=enrollment.enrollment_id,
            otp=_otp(enrollment.secret), config=_MFA_CONFIG, server_correlation_id=b"A" * 16,
        )
        assert not isinstance(confirmed, MFADenied)
    if not step_up:
        # A LOGIN-like durable proof must be produced by its own primitive;
        # authority accepts either current verified LOGIN or STEP_UP for views.
        async with transaccion() as cur:
            challenge = await issue_step_up_challenge_in_cursor(
                cur, actor_user_id=user_id, config=_MFA_CONFIG,
            )
        async with transaccion() as cur:
            proof = await verify_and_consume_totp_in_cursor(
                cur, challenge_token=challenge.token, otp=_otp(enrollment.secret, -1),
                server_correlation_id=b"B" * 16,
            )
        assert not isinstance(proof, MFADenied)
        return proof
    async with transaccion() as cur:
        challenge = await issue_step_up_challenge_in_cursor(cur, actor_user_id=user_id, config=_MFA_CONFIG)
    async with transaccion() as cur:
        proof = await verify_and_consume_totp_in_cursor(
            cur, challenge_token=challenge.token, otp=_otp(enrollment.secret, -1),
            server_correlation_id=b"C" * 16,
        )
    assert not isinstance(proof, MFADenied)
    return proof


def _with_proof(proof, *, recent: bool = False):
    return {"mfa_proof_event_id": proof.event_id, "mfa_config": _MFA_CONFIG,
            "require_recent_mfa": recent}


async def _sql(statement, params=(), fetch=False):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(statement, params)
            return await cur.fetchall() if fetch else cur.lastrowid


async def _seed_office_fixture():
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
        proof = await _real_mfa_proof(fixture["actor"], step_up=True)
        project_decision = await authorize_office(None, fixture["actor"], OfficeOperation.MANAGE_PROJECT, fixture["project"], **_with_proof(proof))
        global_decision = await authorize_office(None, fixture["actor"], OfficeOperation.ADMINISTER_OFFICE)
        assert project_decision.allowed is True
        assert global_decision.allowed is False
        assert global_decision.code == "office_delegation_missing"
    client.portal.call(scenario)


def test_derived_mfa_matrix_keeps_collaborator_view_open_and_mutations_recent(client):
    async def scenario():
        fixture = await _seed_office_fixture()
        # Ordinary collaboration has no implicit MFA barrier.
        assert (await authorize_office(
            None, fixture["actor"], OfficeOperation.VIEW_PROJECT, fixture["project"],
        )).allowed is True
        await _sql(
            "UPDATE office_project_grant SET grant_role='PROJECT_ADMIN' "
            "WHERE project_id=%s AND user_id=%s", (fixture["project"], fixture["actor"]),
        )
        # A false compatibility flag cannot lower the role-derived requirement.
        denied = await authorize_office(
            None, fixture["actor"], OfficeOperation.VIEW_PROJECT, fixture["project"],
            require_recent_mfa=False,
        )
        assert denied.code == "office_mfa_required"
        proof = await _real_mfa_proof(fixture["actor"], step_up=True)
        assert (await authorize_office(
            None, fixture["actor"], OfficeOperation.VIEW_PROJECT, fixture["project"],
            **_with_proof(proof),
        )).allowed is True
        assert (await authorize_office(
            None, fixture["actor"], OfficeOperation.MANAGE_PROJECT, fixture["project"],
            **_with_proof(proof),
        )).allowed is True
    client.portal.call(scenario)


def test_project_membership_administration_is_separate_from_content_and_can_clean_archived_scope(client):
    async def scenario():
        fixture = await _seed_office_fixture()
        await _sql("UPDATE office_project_grant SET grant_role='PROJECT_ADMIN' WHERE project_id=%s AND user_id=%s", (fixture["project"], fixture["actor"]))
        proof = await _real_mfa_proof(fixture["actor"], step_up=True)
        assert (await authorize_office(None, fixture["actor"], OfficeOperation.ADMINISTER_PROJECT_MEMBERS, fixture["project"], **_with_proof(proof))).allowed is True
        await _sql("UPDATE jax_project_scope SET status='ARCHIVED' WHERE project_id=%s", (fixture["project"],))
        assert (await authorize_office(None, fixture["actor"], OfficeOperation.MANAGE_PROJECT, fixture["project"])).code == "office_namespace_denied"
        archived_cleanup = await authorize_office(None, fixture["actor"], OfficeOperation.ADMINISTER_PROJECT_MEMBERS, fixture["project"], **_with_proof(proof))
        assert archived_cleanup.allowed is True
        assert archived_cleanup.code == "office_project_admin_members"
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


def test_denied_audit_uses_canonical_scope_and_keeps_requested_project_untrusted(client):
    async def scenario():
        fixture = await _seed_office_fixture()
        audit_tag = uuid.uuid4().hex
        missing_project = 2_000_000_001
        missing = await authorize_office(None, fixture["actor"], OfficeOperation.VIEW_PROJECT, missing_project)
        assert missing.code == "office_project_missing"
        assert missing.requested_project_id == missing_project
        assert missing.project_id is None
        async with transaccion() as cur:
            await record_authorization_event(cur, missing, correlation_id=f"missing-project-audit-{audit_tag}")
        missing_row = await _sql("SELECT requested_project_id,project_id,tenant_id FROM office_authorization_event WHERE correlation_id=%s", (f"missing-project-audit-{audit_tag}",), fetch=True)
        assert missing_row == ((missing_project, None, fixture["tenant_a"]),)

        await _sql("INSERT INTO office_account_profile (user_id,global_role,status) VALUES (%s,'COLLABORATOR','ACTIVE')", (fixture["outsider"],))
        cross = await authorize_office(None, fixture["outsider"], OfficeOperation.VIEW_PROJECT, fixture["project"])
        assert cross.code == "office_tenant_denied"
        assert cross.requested_project_id == fixture["project"]
        assert cross.project_id == fixture["project"]
        assert cross.tenant_id == fixture["tenant_a"]
        async with transaccion() as cur:
            await record_authorization_event(cur, cross, correlation_id=f"cross-tenant-audit-{audit_tag}")
        cross_row = await _sql("SELECT requested_project_id,project_id,tenant_id FROM office_authorization_event WHERE correlation_id=%s", (f"cross-tenant-audit-{audit_tag}",), fetch=True)
        assert cross_row == ((fixture["project"], fixture["project"], fixture["tenant_a"]),)

        unknown = await authorize_office(None, 2_000_000_000, OfficeOperation.VIEW_PROJECT, fixture["project"])
        async with transaccion() as cur:
            with pytest.raises(ValueError, match="unknown Office actor"):
                await record_authorization_event(cur, unknown)
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


def test_audience_role_contract_and_decision_provenance_are_canonical(client):
    async def scenario():
        fixture = await _seed_office_fixture()
        await _sql("UPDATE office_project_grant SET audience='CLIENT_PUBLISHED' WHERE project_id=%s AND user_id=%s", (fixture["project"], fixture["actor"]))
        assert (await authorize_office(None, fixture["actor"], OfficeOperation.VIEW_PROJECT, fixture["project"])).code == "office_grant_insufficient"
        external = await _sql("INSERT INTO jax_users (tenant_id,email,password_hash,role,status,account_class) VALUES (%s,%s,'x','operator','active','EXTERNAL_CLIENT')", (fixture["tenant_a"], f"office-audience-{uuid.uuid4().hex[:10]}@example.invalid"))
        await _sql("INSERT INTO office_account_profile (user_id,global_role,status) VALUES (%s,'COLLABORATOR','ACTIVE')", (external,))
        group = await _sql("INSERT INTO office_recipient_group (project_id,tenant_id,group_kind,status,created_by) VALUES (%s,%s,'CLIENT','ACTIVE',%s)", (fixture["project"], fixture["tenant_a"], fixture["actor"]))
        await _sql("INSERT INTO office_external_relation (project_id,user_id,recipient_group_id,status) VALUES (%s,%s,%s,'ACTIVE')", (fixture["project"], external, group))
        # The database contract makes this invalid state unrepresentable;
        # it is not merely something the resolver notices afterwards.
        with pytest.raises(aiomysql.OperationalError):
            await _sql("INSERT INTO office_project_grant (project_id,user_id,grant_role,audience,status,granted_by) VALUES (%s,%s,'PROJECT_ADMIN','CLIENT_PUBLISHED','ACTIVE',%s)", (fixture["project"], external, fixture["actor"]))
        await _sql("INSERT INTO office_project_grant (project_id,user_id,grant_role,audience,status,granted_by) VALUES (%s,%s,'COLLABORATOR','CLIENT_PUBLISHED','ACTIVE',%s)", (fixture["project"], external, fixture["actor"]))
        decision = await authorize_office(None, external, OfficeOperation.VIEW_PROJECT, fixture["project"], group)
        assert (decision.audience, decision.grant_role, decision.recipient_group_id) == ("CLIENT_PUBLISHED", "COLLABORATOR", group)
    client.portal.call(scenario)


def test_root_cross_tenant_and_delegated_general_admin_without_implicit_content(client):
    async def scenario():
        fixture = await _seed_office_fixture()
        await _sql("INSERT INTO office_account_profile (user_id,global_role,status) VALUES (%s,'OFFICE_SUPERUSER','ACTIVE')", (fixture["outsider"],))
        root_proof = await _real_mfa_proof(fixture["outsider"], step_up=True)
        assert (await authorize_office(None, fixture["outsider"], OfficeOperation.VIEW_PROJECT, fixture["project"], **_with_proof(root_proof))).allowed is True
        manager = await _sql("INSERT INTO jax_users (tenant_id,email,password_hash,role,status,account_class) VALUES (%s,%s,'x','operator','active','INTERNAL')", (fixture["tenant_a"], f"office-manager-{uuid.uuid4().hex[:10]}@example.invalid"))
        await _sql("INSERT INTO office_account_profile (user_id,global_role,status) VALUES (%s,'GENERAL_ADMIN','ACTIVE')", (manager,))
        await _sql("INSERT INTO office_admin_delegation (tenant_id,user_id,capability,status,granted_by) VALUES (%s,%s,'ADMINISTER_OFFICE','ACTIVE',%s)", (fixture["tenant_a"], manager, fixture["actor"]))
        manager_proof = await _real_mfa_proof(manager, step_up=True)
        assert (await authorize_office(None, manager, OfficeOperation.ADMINISTER_OFFICE, **_with_proof(manager_proof))).allowed is True
        assert (await authorize_office(None, manager, OfficeOperation.VIEW_PROJECT, fixture["project"])).code == "office_grant_missing"
    client.portal.call(scenario)


def test_general_admin_cannot_receive_or_use_create_office_project_delegation(client):
    async def scenario():
        fixture = await _seed_office_fixture()
        manager = await _sql("INSERT INTO jax_users (tenant_id,email,password_hash,role,status,account_class) VALUES (%s,%s,'x','operator','active','INTERNAL')", (fixture["tenant_a"], f"office-create-{uuid.uuid4().hex[:10]}@example.invalid"))
        await _sql("INSERT INTO office_account_profile (user_id,global_role,status) VALUES (%s,'GENERAL_ADMIN','ACTIVE')", (manager,))
        try:
            await _sql("INSERT INTO office_admin_delegation (tenant_id,user_id,capability,status,granted_by) VALUES (%s,%s,'CREATE_OFFICE_PROJECT','ACTIVE',%s)", (fixture["tenant_a"], manager, fixture["actor"]))
        except aiomysql.OperationalError:
            pass
        else:
            decision = await authorize_office(None, manager, OfficeOperation.CREATE_OFFICE_PROJECT)
            assert decision.code == "office_delegation_missing"
            raise AssertionError("CREATE_OFFICE_PROJECT delegation was accepted")
    client.portal.call(scenario)


def test_office_internal_is_staff_only_inside_office_with_real_grants(client):
    async def scenario():
        fixture = await _seed_office_fixture()
        staff = await _sql("INSERT INTO jax_users (tenant_id,email,password_hash,role,status,account_class) VALUES (%s,%s,'x','operator','active','OFFICE_INTERNAL')", (fixture["tenant_a"], f"office-staff-{uuid.uuid4().hex[:10]}@example.invalid"))
        await _sql("INSERT INTO office_account_profile (user_id,global_role,status) VALUES (%s,'COLLABORATOR','ACTIVE')", (staff,))
        await _sql("INSERT INTO jax_project_membership (membership_id,project_id,tenant_id,user_id,project_role,status,created_at,created_by,updated_at) VALUES (%s,%s,%s,%s,'CONTRIBUTOR','ACTIVE',NOW(6),'test',NOW(6))", (str(uuid.uuid4()), fixture["project"], fixture["tenant_a"], staff))
        await _sql("INSERT INTO office_project_grant (project_id,user_id,grant_role,audience,status,granted_by) VALUES (%s,%s,'COLLABORATOR','INTERNAL','ACTIVE',%s)", (fixture["project"], staff, fixture["actor"]))
        assert (await authorize_office(None, staff, OfficeOperation.VIEW_PROJECT, fixture["project"])).allowed is True
        await _sql("UPDATE office_project_grant SET grant_role='PROJECT_ADMIN' WHERE project_id=%s AND user_id=%s", (fixture["project"], staff))
        proof = await _real_mfa_proof(staff, step_up=True)
        assert (await authorize_office(None, staff, OfficeOperation.MANAGE_PROJECT, fixture["project"], **_with_proof(proof))).allowed is True
        await _sql("UPDATE office_account_profile SET global_role='OFFICE_SUPERUSER' WHERE user_id=%s", (staff,))
        assert (await authorize_office(None, staff, OfficeOperation.VIEW_PROJECT, fixture["project"], **_with_proof(proof))).allowed is True
        await _sql("UPDATE jax_project_scope SET authorization_namespace='CLASSIC' WHERE project_id=%s", (fixture["project"],))
        assert (await authorize_office(None, staff, OfficeOperation.VIEW_PROJECT, fixture["project"])).code == "office_namespace_denied"
    client.portal.call(scenario)


def test_explain_covers_each_canonical_authority_lookup_without_sort_or_temporary(client):
    async def scenario():
        fixture = await _seed_office_fixture()
        external = await _sql("INSERT INTO jax_users (tenant_id,email,password_hash,role,status,account_class) VALUES (%s,%s,'x','operator','active','EXTERNAL_CLIENT')", (fixture["tenant_a"], f"office-plan-{uuid.uuid4().hex[:10]}@example.invalid"))
        group = await _sql("INSERT INTO office_recipient_group (project_id,tenant_id,group_kind,status,created_by) VALUES (%s,%s,'CLIENT','ACTIVE',%s)", (fixture["project"], fixture["tenant_a"], fixture["actor"]))
        await _sql("INSERT INTO office_external_relation (project_id,user_id,recipient_group_id,status) VALUES (%s,%s,%s,'ACTIVE')", (fixture["project"], external, group))
        await _sql("INSERT INTO office_project_grant (project_id,user_id,grant_role,audience,status,granted_by) VALUES (%s,%s,'COLLABORATOR','CLIENT_PUBLISHED','ACTIVE',%s)", (fixture["project"], external, fixture["actor"]))
        await _sql("INSERT INTO office_admin_delegation (tenant_id,user_id,capability,status,granted_by) VALUES (%s,%s,'ADMINISTER_OFFICE','ACTIVE',%s)", (fixture["tenant_a"], fixture["actor"], fixture["actor"]))
        await _sql("INSERT INTO office_professional_grant (project_id,user_id,capability,basis,evidence_ref,status,granted_by) VALUES (%s,%s,'PROFESSIONAL_ACTION','test','test','ACTIVE',%s)", (fixture["project"], fixture["actor"], fixture["actor"]))
        plans = (
            ("SELECT u.tenant_id,u.status,u.account_class,t.status,p.global_role,p.status FROM jax_users u JOIN jax_tenants t ON t.tenant_id=u.tenant_id LEFT JOIN office_account_profile p ON p.user_id=u.user_id WHERE u.user_id=%s", (fixture["actor"],)),
            ("SELECT tenant_id,status,authorization_namespace FROM jax_project_scope WHERE project_id=%s", (fixture["project"],)),
            ("SELECT g.grant_role,g.audience,g.status,m.status FROM office_project_grant g JOIN jax_project_membership m ON m.project_id=g.project_id AND m.user_id=g.user_id WHERE g.project_id=%s AND g.user_id=%s", (fixture["project"], fixture["actor"])),
            ("SELECT g.grant_role,g.audience,g.status,r.status,r.recipient_group_id,rg.status FROM office_project_grant g JOIN office_external_relation r ON r.project_id=g.project_id AND r.user_id=g.user_id JOIN office_recipient_group rg ON rg.recipient_group_id=r.recipient_group_id AND rg.project_id=r.project_id WHERE g.project_id=%s AND g.user_id=%s AND r.recipient_group_id=%s", (fixture["project"], external, group)),
            ("SELECT status FROM office_admin_delegation WHERE tenant_id=%s AND user_id=%s AND capability=%s", (fixture["tenant_a"], fixture["actor"], "ADMINISTER_OFFICE")),
            ("SELECT status FROM office_professional_grant WHERE project_id=%s AND user_id=%s AND capability=%s", (fixture["project"], fixture["actor"], "PROFESSIONAL_ACTION")),
        )
        for query, params in plans:
            rows = await _sql("EXPLAIN " + query, params, True)
            assert rows
            extra = " ".join(str(row[-1] or "") for row in rows)
            assert "Using filesort" not in extra
            assert "Using temporary" not in extra
            assert all(str(row[3]).upper() != "ALL" for row in rows)
            assert all(row[5] is not None for row in rows)
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


async def _insert_controlled_step_up_proof(
    user_id: int, *, timestamp: str = "current", result: str = "VERIFIED",
    token_version_delta: int = 0, mfa_version_delta: int = 0,
) -> int:
    """Append a schema-valid STEP_UP ledger fixture at a controlled DB time.

    The security ledger is append-only, so stale and future-proof cases must be
    represented by new truthful test events rather than rewriting occurred_at.
    Every caller first creates a durable proof through the real MFA primitives;
    this fixture only controls the event time/version needed to exercise the
    resolver's re-read checks.
    """
    timestamp_sql = {
        "current": "UTC_TIMESTAMP(6)",
        "old": "DATE_SUB(UTC_TIMESTAMP(6), INTERVAL 301 SECOND)",
        "future": "DATE_ADD(UTC_TIMESTAMP(6), INTERVAL 1 SECOND)",
    }[timestamp]
    rows = await _sql(
        "SELECT token_version,mfa_version FROM jax_users WHERE user_id=%s",
        (user_id,), True,
    )
    token_version, mfa_version = map(int, rows[0])
    return await _sql(
        "INSERT INTO office_mfa_security_event "
        "(event_kind,actor_user_id,principal_kind,challenge_id,subject_user_id,"
        "operation,purpose,token_version,mfa_version,previous_token_version,"
        "previous_mfa_version,proof_event_id,authorization_event_id,result,"
        "server_correlation_id,fence_key,occurred_at) "
        "VALUES ('MFA_PROOF',%s,'USER_SESSION',NULL,%s,'MFA_PROOF','STEP_UP',"
        "%s,%s,NULL,NULL,NULL,NULL,%s,%s,NULL," + timestamp_sql + ")",
        (user_id, user_id, token_version + token_version_delta,
         mfa_version + mfa_version_delta, result, uuid.uuid4().bytes),
    )


async def _add_internal_office_user(fixture, *, global_role: str = "COLLABORATOR") -> int:
    user_id = await _sql(
        "INSERT INTO jax_users "
        "(tenant_id,email,password_hash,role,status,account_class) "
        "VALUES (%s,%s,'x','operator','active','INTERNAL')",
        (fixture["tenant_a"], f"office-matrix-{uuid.uuid4().hex[:10]}@example.invalid"),
    )
    await _sql(
        "INSERT INTO office_account_profile (user_id,global_role,status) VALUES (%s,%s,'ACTIVE')",
        (user_id, global_role),
    )
    await _sql(
        "INSERT INTO jax_project_membership "
        "(membership_id,project_id,tenant_id,user_id,project_role,status,created_at,created_by,updated_at) "
        "VALUES (%s,%s,%s,%s,'CONTRIBUTOR','ACTIVE',NOW(6),'test',NOW(6))",
        (str(uuid.uuid4()), fixture["project"], fixture["tenant_a"], user_id),
    )
    await _sql(
        "INSERT INTO office_project_grant (project_id,user_id,grant_role,audience,status,granted_by) "
        "VALUES (%s,%s,'COLLABORATOR','INTERNAL','ACTIVE',%s)",
        (fixture["project"], user_id, fixture["actor"]),
    )
    return user_id


@pytest.mark.parametrize(
    ("role_case", "global_role", "grant_role", "professional"),
    (
        ("office_root", "OFFICE_SUPERUSER", "COLLABORATOR", False),
        ("general_admin", "GENERAL_ADMIN", "COLLABORATOR", False),
        ("project_admin", "COLLABORATOR", "PROJECT_ADMIN", False),
        ("professional_approver", "COLLABORATOR", "COLLABORATOR", True),
    ),
)
def test_privileged_view_requires_current_verified_proof_but_not_recency(
    client, role_case, global_role, grant_role, professional,
):
    async def scenario():
        fixture = await _seed_office_fixture()
        if role_case == "office_root":
            actor = fixture["outsider"]
            await _sql(
                "INSERT INTO office_account_profile (user_id,global_role,status) VALUES (%s,%s,'ACTIVE')",
                (actor, global_role),
            )
        else:
            actor = fixture["actor"] if role_case == "project_admin" else await _add_internal_office_user(
                fixture, global_role=global_role,
            )
            await _sql(
                "UPDATE office_project_grant SET grant_role=%s WHERE project_id=%s AND user_id=%s",
                (grant_role, fixture["project"], actor),
            )
        if professional:
            await _sql(
                "INSERT INTO office_professional_grant "
                "(project_id,user_id,capability,basis,evidence_ref,status,granted_by) "
                "VALUES (%s,%s,'PROFESSIONAL_ACTION','test-matrix','test-matrix','ACTIVE',%s)",
                (fixture["project"], actor, fixture["actor"]),
            )
        # Real enrollment/STEP_UP establishes the user and versions; the old
        # fixture has a durable, schema-valid event timestamped by MariaDB.
        await _real_mfa_proof(actor, step_up=True)
        old_proof_id = await _insert_controlled_step_up_proof(actor, timestamp="old")
        denied = await authorize_office(
            None, actor, OfficeOperation.VIEW_PROJECT, fixture["project"],
        )
        assert denied.code == "office_mfa_required", role_case
        allowed = await authorize_office(
            None, actor, OfficeOperation.VIEW_PROJECT, fixture["project"],
            **_with_proof(type("Proof", (), {"event_id": old_proof_id})()),
        )
        assert allowed.allowed is True, role_case
    client.portal.call(scenario)


def test_old_current_proof_cannot_authorize_mutations_and_false_flag_cannot_downgrade(client):
    async def scenario():
        fixture = await _seed_office_fixture()
        # Ordinary collaborator view remains open, but explicit recent MFA
        # hardens that same view.
        assert (await authorize_office(
            None, fixture["actor"], OfficeOperation.VIEW_PROJECT, fixture["project"],
        )).allowed is True
        hardened = await authorize_office(
            None, fixture["actor"], OfficeOperation.VIEW_PROJECT, fixture["project"],
            require_recent_mfa=True,
        )
        assert hardened.code == "office_mfa_required"
        await _sql(
            "UPDATE office_project_grant SET grant_role='PROJECT_ADMIN' "
            "WHERE project_id=%s AND user_id=%s", (fixture["project"], fixture["actor"]),
        )
        await _real_mfa_proof(fixture["actor"], step_up=True)
        old_proof_id = await _insert_controlled_step_up_proof(fixture["actor"], timestamp="old")
        old_proof = type("Proof", (), {"event_id": old_proof_id})()
        # The compatibility flag never lowers the derived privileged-view bar.
        assert (await authorize_office(
            None, fixture["actor"], OfficeOperation.VIEW_PROJECT, fixture["project"],
            require_recent_mfa=False,
        )).code == "office_mfa_required"
        assert (await authorize_office(
            None, fixture["actor"], OfficeOperation.VIEW_PROJECT, fixture["project"],
            **_with_proof(old_proof),
        )).allowed is True
        assert (await authorize_office(
            None, fixture["actor"], OfficeOperation.MANAGE_PROJECT, fixture["project"],
            **_with_proof(old_proof),
        )).code == "office_mfa_required"
        root = fixture["outsider"]
        await _sql(
            "INSERT INTO office_account_profile (user_id,global_role,status) VALUES (%s,'OFFICE_SUPERUSER','ACTIVE')",
            (root,),
        )
        await _real_mfa_proof(root, step_up=True)
        root_old = type("Proof", (), {"event_id": await _insert_controlled_step_up_proof(root, timestamp="old")})()
        assert (await authorize_office(
            None, root, OfficeOperation.ADMINISTER_OFFICE_IDENTITY_SECURITY,
            **_with_proof(root_old),
        )).code == "office_mfa_required"
    client.portal.call(scenario)


@pytest.mark.parametrize(
    "case", ("wrong_subject", "non_verified", "future", "old_token", "old_mfa"),
)
def test_privileged_view_rejects_untrusted_or_version_stale_durable_proofs(client, case):
    async def scenario():
        fixture = await _seed_office_fixture()
        await _sql(
            "UPDATE office_project_grant SET grant_role='PROJECT_ADMIN' "
            "WHERE project_id=%s AND user_id=%s", (fixture["project"], fixture["actor"]),
        )
        await _real_mfa_proof(fixture["actor"], step_up=True)
        if case == "wrong_subject":
            other = await _add_internal_office_user(fixture)
            await _real_mfa_proof(other, step_up=True)
            proof_id = await _insert_controlled_step_up_proof(other)
        else:
            proof_id = await _insert_controlled_step_up_proof(
                fixture["actor"],
                timestamp="future" if case == "future" else "current",
                result="DENIED" if case == "non_verified" else "VERIFIED",
                token_version_delta=-1 if case == "old_token" else 0,
                mfa_version_delta=-1 if case == "old_mfa" else 0,
            )
        decision = await authorize_office(
            None, fixture["actor"], OfficeOperation.VIEW_PROJECT, fixture["project"],
            **_with_proof(type("Proof", (), {"event_id": proof_id})()),
        )
        assert decision.code == "office_mfa_required", case
    client.portal.call(scenario)


def test_cursor_authority_re_reads_profile_and_grant_revocation_before_deciding(client):
    async def scenario():
        fixture = await _seed_office_fixture()
        root = fixture["outsider"]
        await _sql(
            "INSERT INTO office_account_profile (user_id,global_role,status) VALUES (%s,'OFFICE_SUPERUSER','ACTIVE')",
            (root,),
        )
        root_proof = await _real_mfa_proof(root, step_up=True)
        async with transaccion() as cur:
            await cur.execute("UPDATE office_account_profile SET status='REVOKED' WHERE user_id=%s", (root,))
            decision = await authorize_office_in_cursor(
                cur, root, OfficeOperation.ADMINISTER_OFFICE_IDENTITY_SECURITY,
                **_with_proof(root_proof),
            )
            assert decision.code == "office_profile_missing"
        await _sql(
            "UPDATE office_project_grant SET grant_role='PROJECT_ADMIN',status='ACTIVE' "
            "WHERE project_id=%s AND user_id=%s", (fixture["project"], fixture["actor"]),
        )
        project_proof = await _real_mfa_proof(fixture["actor"], step_up=True)
        async with transaccion() as cur:
            await cur.execute(
                "UPDATE office_project_grant SET status='REVOKED' WHERE project_id=%s AND user_id=%s",
                (fixture["project"], fixture["actor"]),
            )
            decision = await authorize_office_in_cursor(
                cur, fixture["actor"], OfficeOperation.VIEW_PROJECT, fixture["project"],
                **_with_proof(project_proof),
            )
            assert decision.code == "office_grant_missing"
    client.portal.call(scenario)
