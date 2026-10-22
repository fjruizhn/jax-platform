from __future__ import annotations

from contextlib import asynccontextmanager

from db.connection import get_pool

from .capabilities import ADMIN_OPERATIONS, OfficeOperation, grant_permits, operation_requires_project
from .models import OfficeDecision
from . import repository
from .mfa import MFAConfig, MFADenied, reload_current_self_mfa_proof_in_cursor, reload_current_self_mfa_verified_in_cursor


def _decision(allowed: bool, code: str, actor: int, operation: OfficeOperation, project: int | None, tenant=None, role=None,
              audience: str | None = None, grant_role: str | None = None, recipient_group_id: int | None = None,
              canonical_project_id: int | None = None):
    return OfficeDecision(allowed, code, actor, operation.value, project, canonical_project_id, tenant, role, audience, grant_role, recipient_group_id)


def _validate(actor_user_id, operation, project_id, recipient_group_id):
    if isinstance(actor_user_id, bool) or not isinstance(actor_user_id, int) or actor_user_id < 1:
        raise ValueError("actor_user_id must be a positive integer")
    if not isinstance(operation, OfficeOperation):
        raise ValueError("operation must be an OfficeOperation")
    needs_project = operation_requires_project(operation)
    if needs_project != (project_id is not None):
        raise ValueError("project_id presence does not match operation")
    if project_id is not None and (isinstance(project_id, bool) or not isinstance(project_id, int) or project_id < 1):
        raise ValueError("project_id must be a positive integer")
    if recipient_group_id is not None and (isinstance(recipient_group_id, bool) or not isinstance(recipient_group_id, int) or recipient_group_id < 1):
        raise ValueError("recipient_group_id must be a positive integer")


@asynccontextmanager
async def _cursor_from(pool):
    actual_pool = pool or await get_pool()
    async with actual_pool.acquire() as conn:
        async with conn.cursor() as cur:
            yield cur


async def authorize_office(pool, actor_user_id: int, operation: OfficeOperation, project_id: int | None = None,
                           recipient_group_id: int | None = None, require_recent_mfa: bool = False,
                           mfa_proof_event_id: int | None = None, mfa_config: MFAConfig | None = None) -> OfficeDecision:
    """Resolve a fresh, immutable Office-only decision from database state."""
    _validate(actor_user_id, operation, project_id, recipient_group_id)
    async with _cursor_from(pool) as cur:
        decision = await _authorize(cur, actor_user_id, operation, project_id, recipient_group_id, require_recent_mfa, False)
        return await _apply_mfa_requirement(cur, decision, require_recent_mfa, mfa_proof_event_id, mfa_config)


async def authorize_office_in_cursor(cur, actor_user_id: int, operation: OfficeOperation, project_id: int | None = None,
                                     recipient_group_id: int | None = None, require_recent_mfa: bool = False,
                                     mfa_proof_event_id: int | None = None, mfa_config: MFAConfig | None = None) -> OfficeDecision:
    """Re-resolve under the caller's transaction locks before any mutation."""
    _validate(actor_user_id, operation, project_id, recipient_group_id)
    decision = await _authorize(cur, actor_user_id, operation, project_id, recipient_group_id, require_recent_mfa, True)
    return await _apply_mfa_requirement(cur, decision, require_recent_mfa, mfa_proof_event_id, mfa_config)


async def authorize_invitation_issuer_in_cursor(cur, *, issuer_user_id: int,
                                                 project_id: int, proof_event_id: int) -> OfficeDecision:
    """Revalidate an issuance proof for delegated acceptance without its age.

    This narrow resolver is intentionally separate from the general MFA
    switch: only the immutable invitation proof may be older than the normal
    interactive recency window, and its user token/MFA versions plus present
    Office authority are still reread under the caller transaction locks.
    """
    _validate(issuer_user_id, OfficeOperation.ADMINISTER_PROJECT_MEMBERS, project_id, None)
    decision = await _authorize(cur, issuer_user_id, OfficeOperation.ADMINISTER_PROJECT_MEMBERS,
                                project_id, None, False, True)
    if not decision.allowed:
        return decision
    proof = await reload_current_self_mfa_verified_in_cursor(
        cur, actor_user_id=issuer_user_id, proof_event_id=proof_event_id)
    if isinstance(proof, MFADenied):
        return _decision(False, "office_invitation_issuer_proof_invalid", issuer_user_id,
                         OfficeOperation.ADMINISTER_PROJECT_MEMBERS, project_id,
                         decision.tenant_id, decision.global_role, decision.audience,
                         decision.grant_role, decision.recipient_group_id, decision.project_id)
    return decision


async def _apply_mfa_requirement(cur, decision: OfficeDecision, require_recent_mfa: bool,
                                 proof_event_id: int | None, config: MFAConfig | None) -> OfficeDecision:
    """Apply the role-derived MFA rule after canonical grants are resolved.

    ``require_recent_mfa`` is retained for compatible callers, but only adds a
    requirement.  It cannot turn a role-derived requirement off.
    """
    if not decision.allowed:
        return decision
    privileged_view = (
        decision.operation == OfficeOperation.VIEW_PROJECT.value
        and (decision.global_role in {"OFFICE_SUPERUSER", "GENERAL_ADMIN"}
             or decision.grant_role == "PROJECT_ADMIN")
    )
    # A professional approver is privileged even when their ordinary project
    # grant says COLLABORATOR.  Resolve this current, indexed grant rather
    # than allowing the global-role shortcut to hide it.
    professional_view = False
    if decision.operation == OfficeOperation.VIEW_PROJECT.value and decision.project_id is not None:
        professional = await repository.fetch_professional_grant(
            cur, decision.project_id, decision.actor_user_id,
            OfficeOperation.PROFESSIONAL_ACTION, for_update=False,
        )
        professional_view = professional is not None and professional["status"] == "ACTIVE"
    derived_recent = decision.operation != OfficeOperation.VIEW_PROJECT.value
    # An explicit caller requirement hardens even an otherwise ordinary
    # collaborator view.  It is additive, like the role-derived conditions;
    # callers can never use ``False`` to lower either rule.
    need_verified = privileged_view or professional_view or derived_recent or require_recent_mfa
    need_recent = derived_recent or require_recent_mfa
    if not need_verified:
        return decision
    if config is None or type(proof_event_id) is not int or proof_event_id < 1:
        return _decision(False, "office_mfa_required", decision.actor_user_id,
                         OfficeOperation(decision.operation), decision.requested_project_id,
                         decision.tenant_id, decision.global_role,
                         decision.audience, decision.grant_role,
                         decision.recipient_group_id, decision.project_id)
    proof = await (
        reload_current_self_mfa_proof_in_cursor(
            cur, actor_user_id=decision.actor_user_id, proof_event_id=proof_event_id, config=config,
        ) if need_recent else reload_current_self_mfa_verified_in_cursor(
            cur, actor_user_id=decision.actor_user_id, proof_event_id=proof_event_id,
        )
    )
    if isinstance(proof, MFADenied):
        return _decision(False, "office_mfa_required", decision.actor_user_id,
                         OfficeOperation(decision.operation), decision.requested_project_id,
                         decision.tenant_id, decision.global_role,
                         decision.audience, decision.grant_role,
                         decision.recipient_group_id, decision.project_id)
    return decision


async def _authorize(cur, actor, operation, project, recipient_group, require_recent_mfa, locked):
    actor_row = await repository.fetch_actor(cur, actor, for_update=locked)
    if actor_row is None:
        return _decision(False, "office_actor_missing", actor, operation, project)
    tenant = actor_row["tenant_id"]
    user_status = actor_row["user_status"]
    account_class = actor_row["account_class"]
    tenant_status = actor_row["tenant_status"]
    global_role = actor_row["global_role"]
    profile_status = actor_row["profile_status"]
    if user_status != "active":
        return _decision(False, "office_actor_inactive", actor, operation, project, tenant)
    if tenant_status != "active":
        return _decision(False, "office_tenant_inactive", actor, operation, project, tenant)
    if account_class not in {"INTERNAL", "OFFICE_INTERNAL", "EXTERNAL_CLIENT"}:
        return _decision(False, "office_account_class_denied", actor, operation, project, tenant)
    if profile_status != "ACTIVE" or global_role not in {"COLLABORATOR", "GENERAL_ADMIN", "OFFICE_SUPERUSER"}:
        return _decision(False, "office_profile_missing", actor, operation, project, tenant)
    if project is not None:
        scope = await repository.fetch_project_scope(cur, project, for_update=locked)
        if scope is None:
            return _decision(False, "office_project_missing", actor, operation, project, tenant, global_role)
        project_tenant = scope["tenant_id"]
        project_status = scope["status"]
        namespace = scope["namespace"]
        # Membership administration has cleanup semantics: revocation may
        # remove stale access from an archived Office scope.  It is deliberately
        # distinct from content management, which remains ACTIVE-only.
        cleanup_operation = operation is OfficeOperation.ADMINISTER_PROJECT_MEMBERS
        if namespace != "OFFICE_CONFIDENTIAL" or (project_status != "ACTIVE" and not cleanup_operation):
            return _decision(False, "office_namespace_denied", actor, operation, project, project_tenant, global_role, canonical_project_id=project)
        # An external account may only consume a CLIENT_PUBLISHED view through
        # an active recipient relation.  It never receives a role bypass.
        if account_class == "EXTERNAL_CLIENT":
            if global_role != "COLLABORATOR" or operation is not OfficeOperation.VIEW_PROJECT or recipient_group is None:
                return _decision(False, "office_account_class_denied", actor, operation, project, project_tenant, global_role, canonical_project_id=project)
            grant = await repository.fetch_external_grant(cur, project, actor, recipient_group, for_update=locked)
            if (grant is None or grant["grant_status"] != "ACTIVE" or grant["relation_status"] != "ACTIVE"
                    or grant["group_status"] != "ACTIVE"):
                return _decision(False, "office_external_relation_missing", actor, operation, project, project_tenant, global_role, canonical_project_id=project)
            if (grant["audience"] != "CLIENT_PUBLISHED" or grant["grant_role"] != "COLLABORATOR"
                    or not grant_permits(operation, grant["grant_role"], grant["audience"])):
                return _decision(False, "office_grant_insufficient", actor, operation, project, project_tenant, global_role, canonical_project_id=project)
            return _decision(True, "office_external_grant", actor, operation, project, project_tenant, global_role,
                             grant["audience"], grant["grant_role"], grant["recipient_group_id"], project)
        if global_role == "OFFICE_SUPERUSER":
            return _decision(True, "office_superuser", actor, operation, project, project_tenant, global_role, canonical_project_id=project)
        if account_class in {"INTERNAL", "OFFICE_INTERNAL"}:
            if tenant != project_tenant:
                return _decision(False, "office_tenant_denied", actor, operation, project, project_tenant, global_role, canonical_project_id=project)
            if operation is OfficeOperation.ADMINISTER_PROJECT_MEMBERS and global_role == "GENERAL_ADMIN":
                delegation = await repository.fetch_delegation(
                    cur, actor, tenant, OfficeOperation.ADMINISTER_OFFICE, for_update=locked)
                if project_status == "ACTIVE" and delegation is not None and delegation["status"] == "ACTIVE":
                    return _decision(True, "office_delegated_member_admin", actor, operation, project,
                                     project_tenant, global_role, canonical_project_id=project)
            grant = await repository.fetch_internal_grant(cur, project, actor, for_update=locked)
            if grant is None or grant["grant_status"] != "ACTIVE" or grant["membership_status"] != "ACTIVE":
                return _decision(False, "office_grant_missing", actor, operation, project, project_tenant, global_role, canonical_project_id=project)
            if grant["audience"] != "INTERNAL":
                return _decision(False, "office_grant_insufficient", actor, operation, project, project_tenant, global_role, canonical_project_id=project)
            if operation is OfficeOperation.ADMINISTER_PROJECT_MEMBERS:
                if grant["grant_role"] == "PROJECT_ADMIN":
                    return _decision(True, "office_project_admin_members", actor, operation, project, project_tenant,
                                     global_role, grant["audience"], grant["grant_role"], canonical_project_id=project)
                return _decision(False, "office_grant_insufficient", actor, operation, project, project_tenant,
                                 global_role, canonical_project_id=project)
            if not grant_permits(operation, grant["grant_role"], grant["audience"]):
                if operation is OfficeOperation.PROFESSIONAL_ACTION:
                    professional = await repository.fetch_professional_grant(
                        cur, project, actor, operation, for_update=locked)
                    if professional is not None and professional["status"] == "ACTIVE":
                        return _decision(True, "office_professional_grant", actor, operation, project, project_tenant, global_role,
                                         grant["audience"], grant["grant_role"], canonical_project_id=project)
                    return _decision(False, "office_professional_grant_missing", actor, operation, project, project_tenant, global_role, canonical_project_id=project)
                return _decision(False, "office_grant_insufficient", actor, operation, project, project_tenant, global_role, canonical_project_id=project)
            return _decision(True, "office_grant", actor, operation, project, project_tenant, global_role,
                             grant["audience"], grant["grant_role"], canonical_project_id=project)
        return _decision(False, "office_account_class_denied", actor, operation, project, project_tenant, global_role, canonical_project_id=project)
    if account_class not in {"INTERNAL", "OFFICE_INTERNAL"}:
        return _decision(False, "office_account_class_denied", actor, operation, None, tenant, global_role)
    if global_role == "OFFICE_SUPERUSER":
        return _decision(True, "office_superuser", actor, operation, None, tenant, global_role)
    # Identity-security authority is root-only.  A general Office
    # administrator must never inherit another user's credentials/MFA power.
    if operation is OfficeOperation.ADMINISTER_OFFICE and global_role == "GENERAL_ADMIN":
        delegation = await repository.fetch_delegation(cur, actor, tenant, operation, for_update=locked)
        if delegation is not None and delegation["status"] == "ACTIVE":
            return _decision(True, "office_delegation", actor, operation, None, tenant, global_role)
    return _decision(False, "office_delegation_missing", actor, operation, None, tenant, global_role)
