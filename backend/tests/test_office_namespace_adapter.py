"""MariaDB integration checks for the trusted Office/JAX namespace adapter."""
from __future__ import annotations

import uuid

import pytest

from db.connection import get_pool
from db.transaccion import transaccion
from virtual_office.mfa import MFAConfig
from virtual_office.namespace_adapter import OfficeNamespaceAuthorizer, OfficeNamespaceMembershipAuthorizer


_MFA_CONFIG = MFAConfig(120, 3, 300, 300, 3)


async def _sql(statement, params=(), fetch=False):
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(statement, params)
            return await cur.fetchall() if fetch else cur.lastrowid


async def _root_and_tenants():
    tag = uuid.uuid4().hex[:12]
    actor_tenant = await _sql("INSERT INTO jax_tenants (name,plan,status) VALUES (%s,'test','active')", (f"office-root-{tag}",))
    target_tenant = await _sql("INSERT INTO jax_tenants (name,plan,status) VALUES (%s,'test','active')", (f"office-target-{tag}",))
    actor = await _sql(
        "INSERT INTO jax_users (tenant_id,email,password_hash,role,status,account_class) "
        "VALUES (%s,%s,'x','operator','active','OFFICE_INTERNAL')",
        (actor_tenant, f"office-root-{tag}@example.invalid"))
    await _sql("INSERT INTO office_account_profile (user_id,global_role,status) VALUES (%s,'OFFICE_SUPERUSER','ACTIVE')", (actor,))
    return actor, actor_tenant, target_tenant


async def _current_step_up_proof(user_id: int) -> int:
    """Create the durable current proof required for an Office mutation."""
    versions = await _sql(
        "SELECT token_version,mfa_version FROM jax_users WHERE user_id=%s",
        (user_id,), True,
    )
    token_version, mfa_version = versions[0]
    return await _sql(
        "INSERT INTO office_mfa_security_event "
        "(event_kind,actor_user_id,principal_kind,challenge_id,subject_user_id,operation,purpose,"
        "token_version,mfa_version,previous_token_version,previous_mfa_version,proof_event_id,"
        "authorization_event_id,result,server_correlation_id,fence_key,occurred_at) "
        "VALUES ('MFA_PROOF',%s,'USER_SESSION',NULL,%s,'MFA_PROOF','STEP_UP',%s,%s,NULL,NULL,"
        "NULL,NULL,'VERIFIED',%s,NULL,UTC_TIMESTAMP(6))",
        (user_id, user_id, int(token_version), int(mfa_version), uuid.uuid4().bytes),
    )


def test_root_creates_same_tenant_office_scope_and_audits_canonical_target(client):
    async def scenario():
        from jax.memory.b9_mariadb import MariaDBB9Store
        from jax.memory.project_authority import ProjectAuthorityAdmin, ProjectNamespace

        actor, actor_tenant, _other_tenant = await _root_and_tenants()
        proof_id = await _current_step_up_proof(actor)
        admin = ProjectAuthorityAdmin(MariaDBB9Store(await get_pool()))
        created = await admin.create_namespaced_project(
            actor_user_id=actor, target_tenant_id=actor_tenant,
            namespace=ProjectNamespace.OFFICE_CONFIDENTIAL, name="Office adapter test",
            description=None, idempotency_key=str(uuid.uuid4()),
            authorizer=OfficeNamespaceAuthorizer(mfa_proof_event_id=proof_id, mfa_config=_MFA_CONFIG))
        assert created.created is True
        assert await _sql(
            "SELECT tenant_id,authorization_namespace,status FROM jax_project_scope WHERE project_id=%s",
            (created.project_id,), True) == ((actor_tenant, "OFFICE_CONFIDENTIAL", "ACTIVE"),)
        audit = await _sql(
            "SELECT actor_user_id,operation,requested_project_id,project_id,tenant_id,result,reason_code "
            "FROM office_authorization_event WHERE actor_user_id=%s AND operation='CREATE_OFFICE_PROJECT' "
            "ORDER BY event_id DESC LIMIT 1", (actor,), True)
        assert audit == ((actor, "CREATE_OFFICE_PROJECT", None, None, actor_tenant, "ALLOWED", "office_namespace_create"),)
    client.portal.call(scenario)


def test_root_cannot_create_cross_tenant_office_scope(client):
    async def scenario():
        from jax.memory.b9_mariadb import MariaDBB9Store
        from jax.memory.project_authority import AuthorizationDenied, ProjectAuthorityAdmin, ProjectNamespace

        actor, _actor_tenant, target_tenant = await _root_and_tenants()
        admin = ProjectAuthorityAdmin(MariaDBB9Store(await get_pool()))
        with pytest.raises((AuthorizationDenied, PermissionError)):
            await admin.create_namespaced_project(
                actor_user_id=actor, target_tenant_id=target_tenant,
                namespace=ProjectNamespace.OFFICE_CONFIDENTIAL, name="Cross tenant denied",
                description=None, idempotency_key=str(uuid.uuid4()), authorizer=OfficeNamespaceAuthorizer())
        assert await _sql(
            "SELECT project_id FROM jax_project_scope WHERE tenant_id=%s AND authorization_namespace='OFFICE_CONFIDENTIAL'",
            (target_tenant,), True) == ()
    client.portal.call(scenario)


def test_non_root_cannot_create_office_scope_or_leave_audit(client):
    async def scenario():
        from jax.memory.b9_mariadb import MariaDBB9Store
        from jax.memory.project_authority import AuthorizationDenied, ProjectAuthorityAdmin, ProjectNamespace

        actor, _actor_tenant, target_tenant = await _root_and_tenants()
        await _sql("UPDATE office_account_profile SET global_role='GENERAL_ADMIN' WHERE user_id=%s", (actor,))
        admin = ProjectAuthorityAdmin(MariaDBB9Store(await get_pool()))
        with pytest.raises((AuthorizationDenied, PermissionError)):
            await admin.create_namespaced_project(
                actor_user_id=actor, target_tenant_id=target_tenant,
                namespace=ProjectNamespace.OFFICE_CONFIDENTIAL, name="Denied Office adapter test",
                description=None, idempotency_key=str(uuid.uuid4()), authorizer=OfficeNamespaceAuthorizer())
        assert await _sql("SELECT project_id FROM jax_project_scope WHERE tenant_id=%s AND authorization_namespace='OFFICE_CONFIDENTIAL'", (target_tenant,), True) == ()
        assert await _sql("SELECT event_id FROM office_authorization_event WHERE actor_user_id=%s AND operation='CREATE_OFFICE_PROJECT'", (actor,), True) == ()
    client.portal.call(scenario)


def test_membership_callback_uses_typed_jax_contract_and_current_root_authority(client):
    async def scenario():
        from jax.memory.project_authority import (
            NamespacedMemberKind, ProjectAuthorityAdmin, ProjectNamespace,
        )

        actor, _actor_tenant, target_tenant = await _root_and_tenants()
        proof_id = await _current_step_up_proof(actor)
        target = await _sql(
            "INSERT INTO jax_users (tenant_id,email,password_hash,role,status,account_class) "
            "VALUES (%s,%s,'x','operator','active','OFFICE_INTERNAL')",
            (target_tenant, f"office-member-{uuid.uuid4().hex[:12]}@example.invalid"))
        project = await _sql("INSERT INTO projects (project_uuid,name,status) VALUES (UUID(),%s,'active')", ("Membership adapter test",))
        await _sql(
            "INSERT INTO jax_project_scope (project_id,tenant_id,authorization_namespace,status,created_at,created_by,updated_at) "
            "VALUES (%s,%s,'OFFICE_CONFIDENTIAL','ACTIVE',NOW(6),'test',NOW(6))", (project, target_tenant))
        async with transaccion() as cursor:
            result = await ProjectAuthorityAdmin(None).grant_namespaced_member_in_transaction(
                cursor, actor_user_id=actor, target_tenant_id=target_tenant, project_id=project,
                target_user_id=target, namespace=ProjectNamespace.OFFICE_CONFIDENTIAL,
                member_kind=NamespacedMemberKind.INTERNAL, idempotency_key=str(uuid.uuid4()),
                authorizer=OfficeNamespaceMembershipAuthorizer(
                    mfa_proof_event_id=proof_id, mfa_config=_MFA_CONFIG))
        assert (result.project_id, result.target_user_id, result.status, result.replayed) == (project, target, "ACTIVE", False)
        assert await _sql("SELECT project_role,status FROM jax_project_membership WHERE project_id=%s AND user_id=%s", (project, target), True) == (("CONTRIBUTOR", "ACTIVE"),)
        assert await _sql(
            "SELECT result,reason_code FROM office_authorization_event WHERE actor_user_id=%s "
            "AND operation='ADMINISTER_PROJECT_MEMBERS' ORDER BY event_id DESC LIMIT 1", (actor,), True) == (("ALLOWED", "office_superuser"),)
    client.portal.call(scenario)


def test_invitation_execution_fails_closed_without_task3_issuer_proof(client):
    async def scenario():
        from jax.memory.project_authority import (
            NamespacedMemberKind, NamespaceMembershipExecution,
            NamespaceMembershipExecutionKind, ProjectAuthorityAdmin, ProjectNamespace,
        )

        actor, _actor_tenant, target_tenant = await _root_and_tenants()
        target = await _sql(
            "INSERT INTO jax_users (tenant_id,email,password_hash,role,status,account_class) "
            "VALUES (%s,%s,'x','operator','active','OFFICE_INTERNAL')",
            (target_tenant, f"office-invite-target-{uuid.uuid4().hex[:12]}@example.invalid"))
        project = await _sql("INSERT INTO projects (project_uuid,name,status) VALUES (UUID(),%s,'active')", ("Invitation denied test",))
        await _sql(
            "INSERT INTO jax_project_scope (project_id,tenant_id,authorization_namespace,status,created_at,created_by,updated_at) "
            "VALUES (%s,%s,'OFFICE_CONFIDENTIAL','ACTIVE',NOW(6),'test',NOW(6))", (project, target_tenant))
        invitation_id = str(uuid.uuid4())
        with pytest.raises(PermissionError, match="acceptance proof"):
            async with transaccion() as cursor:
                await ProjectAuthorityAdmin(None).grant_namespaced_member_in_transaction(
                    cursor, actor_user_id=actor, target_tenant_id=target_tenant, project_id=project,
                    target_user_id=target, namespace=ProjectNamespace.OFFICE_CONFIDENTIAL,
                    member_kind=NamespacedMemberKind.INTERNAL, idempotency_key=str(uuid.uuid4()),
                    execution=NamespaceMembershipExecution(
                        NamespaceMembershipExecutionKind.INVITATION_ACCEPTANCE, invitation_id),
                    authorizer=OfficeNamespaceMembershipAuthorizer())
        assert await _sql("SELECT membership_id FROM jax_project_membership WHERE project_id=%s AND user_id=%s", (project, target), True) == ()
        assert await _sql("SELECT event_id FROM office_authorization_event WHERE actor_user_id=%s AND operation='ADMINISTER_PROJECT_MEMBERS'", (actor,), True) == ()
    client.portal.call(scenario)
