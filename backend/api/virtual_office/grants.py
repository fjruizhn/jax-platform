"""Root-controlled professional capability grants for existing project members."""
from __future__ import annotations

import hashlib
import json
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field

from auth.middleware import get_current_user
from auth.models import AuthUser
from db.connection import get_pool
from db.transaccion import AISLAMIENTO_ADMIN, transaccion
from virtual_office.audit import record_authorization_event
from virtual_office.authority import authorize_office_in_cursor
from virtual_office.capabilities import OfficeOperation
from virtual_office.mfa import load_mfa_config_from_environment


router = APIRouter(prefix="/api/virtual-office/projects")


class ProfessionalGrantUpdate(BaseModel):
    basis: str = Field(min_length=1, max_length=128, pattern=r".*\S.*")
    evidence_ref: str = Field(min_length=1, max_length=255, pattern=r".*\S.*")
    expected_version: int = Field(ge=0)


class ProjectGrantUpdate(BaseModel):
    grant_role: Literal["PROJECT_ADMIN", "COLLABORATOR"]
    audience: Literal["INTERNAL", "CLIENT_PUBLISHED"]
    expected_version: int = Field(ge=0)


def _membership_receipt_digest(*, operation: str, actor_user_id: int,
                               target_tenant_id: int, project_id: int,
                               target_user_id: int, expected_version: int,
                               grant_role: str | None, audience: str | None,
                               execution_kind: str = "INTERACTIVE_USER") -> bytes:
    """Hash the complete Office request, including its optimistic version.

    The JAX request digest intentionally has a narrower responsibility.  This
    one binds the mutable Office companion so a reused UUID cannot turn a
    lost-response retry into a different grant transition.
    """
    canonical = json.dumps({
        "actor_user_id": actor_user_id, "audience": audience,
        "domain": "office-project-membership/v1", "execution_kind": execution_kind,
        "expected_version": expected_version, "grant_role": grant_role,
        "operation": operation, "project_id": project_id,
        "target_tenant_id": target_tenant_id, "target_user_id": target_user_id,
    }, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(canonical).digest()


async def _locked_membership_receipt(cur, *, idempotency_key: str):
    await cur.execute(
        "SELECT request_digest,jax_membership_id,jax_event_id,office_grant_version,office_grant_status,"
        "external_relation_id,external_relation_version,recipient_group_id,grant_role,audience "
        "FROM office_membership_mutation_receipt WHERE namespace='OFFICE_CONFIDENTIAL' "
        "AND idempotency_key=%s FOR UPDATE", (idempotency_key,))
    return await cur.fetchone()


async def _assert_receipt_companion(cur, *, project_id: int, target_user_id: int, receipt) -> None:
    """A receipt replay is valid only while its saved Office companion matches."""
    await cur.execute(
        "SELECT version,status FROM office_project_grant WHERE project_id=%s AND user_id=%s FOR UPDATE",
        (project_id, target_user_id))
    grant = await cur.fetchone()
    if grant is None or int(grant[0]) != int(receipt[3]) or str(grant[1]) != str(receipt[4]):
        raise HTTPException(status_code=503, detail="office_project_grant_retryable")
    if receipt[5] is not None:
        await cur.execute(
            "SELECT relation_id,version,recipient_group_id FROM office_external_relation "
            "WHERE project_id=%s AND user_id=%s FOR UPDATE", (project_id, target_user_id))
        relation = await cur.fetchone()
        if (relation is None or int(relation[0]) != int(receipt[5])
                or int(relation[1]) != int(receipt[6]) or int(relation[2]) != int(receipt[7])):
            raise HTTPException(status_code=503, detail="office_project_grant_retryable")


async def _write_membership_receipt(cur, *, idempotency_key: str, digest: bytes,
                                    operation: str, actor_user_id: int, target_tenant_id: int,
                                    project_id: int, target_user_id: int, expected_version: int,
                                    grant_role: str | None, audience: str | None, result,
                                    office_grant_version: int, office_grant_status: str,
                                    authorization_event_id: int,
                                    relation: tuple[object, ...] | None = None) -> None:
    """Append terminal evidence after all companion writes have succeeded."""
    relation_id = relation_version = recipient_group_id = None
    if relation is not None:
        relation_id, relation_version, recipient_group_id = map(int, relation)
    await cur.execute(
        "INSERT INTO office_membership_mutation_receipt "
        "(namespace,idempotency_key,request_digest,operation,actor_user_id,target_tenant_id,project_id,target_user_id,expected_version,grant_role,audience,execution_kind,jax_membership_id,jax_event_id,office_grant_version,office_grant_status,external_relation_id,external_relation_version,recipient_group_id,authorization_event_id,created_at) "
        "VALUES ('OFFICE_CONFIDENTIAL',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'INTERACTIVE_USER',%s,%s,%s,%s,%s,%s,%s,%s,UTC_TIMESTAMP(6))",
        (idempotency_key, digest, operation, actor_user_id, target_tenant_id,
         project_id, target_user_id, expected_version, grant_role, audience,
         result.membership_id, result.event_id, office_grant_version, office_grant_status,
         relation_id, relation_version, recipient_group_id, authorization_event_id),
    )



class _OfficeMembershipAuthorizer:
    """Trusted JXP callback for JAX's caller-owned membership mutation."""

    def __init__(self, *, proof_event_id: int | None, mfa_config, grant_role: str | None):
        self._proof_event_id = proof_event_id
        self._mfa_config = mfa_config
        self._grant_role = grant_role

    async def authorize_membership_in_transaction(self, cursor, *, actor_user_id: int,
                                                  target_tenant_id: int, project_id: int,
                                                  target_user_id: int, namespace, operation,
                                                  member_kind, idempotency_key: str, execution) -> None:
        from jax.memory.project_authority import AuthorizationDenied
        decision = await authorize_office_in_cursor(
            cursor, actor_user_id, OfficeOperation.ADMINISTER_PROJECT_MEMBERS, project_id,
            require_recent_mfa=True, mfa_proof_event_id=self._proof_event_id,
            mfa_config=self._mfa_config,
        )
        if not decision.allowed:
            raise AuthorizationDenied("Office project-member authority is required")
        # A project administrator may manage only collaborators.  It can never
        # mint the Office-side PROJECT_ADMIN authority.
        if self._grant_role == "PROJECT_ADMIN" and decision.global_role != "OFFICE_SUPERUSER":
            raise AuthorizationDenied("only an Office root may grant project administration")


def _actor_id(user: AuthUser) -> int:
    try:
        return int(user.user_id)
    except (TypeError, ValueError) as error:
        raise HTTPException(status_code=403, detail="office_professional_grant_denied") from error


async def _current_project_grant_decision(cur, *, actor_user_id: int, project_id: int,
                                          proof_event_id: int | None, config):
    decision = await authorize_office_in_cursor(
        cur, actor_user_id, OfficeOperation.ADMINISTER_PROJECT_MEMBERS, project_id,
        require_recent_mfa=True, mfa_proof_event_id=proof_event_id, mfa_config=config,
    )
    if not decision.allowed:
        raise HTTPException(status_code=403, detail="office_project_grant_denied")
    return decision


async def _lock_grant_identities(cur, *, actor_user_id: int, target_user_id: int,
                                 audience: str, cleanup: bool = False) -> int:
    """Lock the target's canonical identity/profile before touching JAX membership.

    A membership without a usable Office profile is an access pre-grant.  The
    profile and recipient relation are therefore part of the same transaction
    as the JAX callback, never repaired afterwards.
    """
    await cur.execute("SELECT tenant_id FROM jax_users WHERE user_id=%s", (actor_user_id,))
    actor_hint = await cur.fetchone()
    await cur.execute("SELECT tenant_id FROM jax_users WHERE user_id=%s", (target_user_id,))
    target_hint = await cur.fetchone()
    # These are lock-order hints only.  In particular, do not report a target
    # miss until the actor is locked and has passed the opaque Office identity
    # gate below; otherwise this mutator becomes a directory oracle.
    if actor_hint is None:
        raise HTTPException(status_code=403, detail="office_project_grant_denied")
    tenant_hints = {int(actor_hint[0])}
    if target_hint is not None:
        tenant_hints.add(int(target_hint[0]))
    tenant_rows: dict[int, str] = {}
    for tenant_id in sorted(tenant_hints):
        await cur.execute("SELECT tenant_id,status FROM jax_tenants WHERE tenant_id=%s FOR UPDATE", (tenant_id,))
        tenant = await cur.fetchone()
        if tenant is not None:
            tenant_rows[int(tenant[0])] = str(tenant[1])
    # Actor lock precedes the target.  Validate a deliberately narrow Office
    # identity predicate before observing target/profile/project state.  The
    # trusted JAX callback later performs the operation-specific authority
    # and recent-MFA checks under the same transaction.
    await cur.execute(
        "SELECT u.tenant_id,u.status,u.account_class,t.status,p.status,p.global_role "
        "FROM jax_users u JOIN jax_tenants t ON t.tenant_id=u.tenant_id "
        "LEFT JOIN office_account_profile p ON p.user_id=u.user_id "
        "WHERE u.user_id=%s FOR UPDATE", (actor_user_id,))
    actor = await cur.fetchone()
    if (actor is None or int(actor[0]) != int(actor_hint[0]) or actor[1] != "active"
            or actor[3] != "active" or actor[2] not in {"INTERNAL", "OFFICE_INTERNAL"}
            or actor[4] != "ACTIVE" or actor[5] not in {"COLLABORATOR", "GENERAL_ADMIN", "OFFICE_SUPERUSER"}
            or tenant_rows.get(int(actor_hint[0])) != "active"):
        raise HTTPException(status_code=403, detail="office_project_grant_denied")
    if target_hint is None:
        raise HTTPException(status_code=404, detail="office_project_grant_target_not_found")
    target_hint_tenant = int(target_hint[0])
    # Revocation is cleanup: JAX deliberately accepts a target tenant that
    # has become inactive, while grant remains active-tenant only.
    if target_hint_tenant not in tenant_rows or (not cleanup and tenant_rows[target_hint_tenant] != "active"):
        raise HTTPException(status_code=404, detail="office_project_grant_target_not_found")
    await cur.execute(
        "SELECT u.tenant_id,u.status,u.account_class,t.status,p.status,p.global_role "
        "FROM jax_users u JOIN jax_tenants t ON t.tenant_id=u.tenant_id "
        "LEFT JOIN office_account_profile p ON p.user_id=u.user_id WHERE u.user_id=%s FOR UPDATE",
        (target_user_id,),
    )
    target = await cur.fetchone()
    if target is None:
        raise HTTPException(status_code=409, detail="office_project_grant_profile_required")
    if int(target[0]) != target_hint_tenant:
        raise HTTPException(status_code=409, detail="office_project_grant_target_drift")
    expected_classes = ({"INTERNAL", "OFFICE_INTERNAL"} if audience == "INTERNAL" else {"EXTERNAL_CLIENT"})
    if not cleanup and (target[1] != "active" or target[3] != "active" or target[4] != "ACTIVE"
                        or target[2] not in expected_classes
                        or (target[2] == "EXTERNAL_CLIENT" and target[5] != "COLLABORATOR")):
        raise HTTPException(status_code=409, detail="office_project_grant_profile_required")
    return int(target[0])


async def _lock_active_client_group(cur, *, project_id: int) -> int:
    await cur.execute(
        "SELECT recipient_group_id FROM office_recipient_group WHERE project_id=%s "
        "AND group_kind='CLIENT' AND status='ACTIVE' FOR UPDATE", (project_id,))
    group = await cur.fetchone()
    if group is None:
        raise HTTPException(status_code=409, detail="office_project_grant_recipient_group_required")
    return int(group[0])


async def _upsert_external_relation(cur, *, project_id: int, target_user_id: int,
                                    recipient_group_id: int) -> None:
    await cur.execute(
        "SELECT status,version,recipient_group_id FROM office_external_relation "
        "WHERE project_id=%s AND user_id=%s FOR UPDATE", (project_id, target_user_id))
    relation = await cur.fetchone()
    if relation is None:
        await cur.execute(
            "INSERT INTO office_external_relation (project_id,user_id,recipient_group_id,status) "
            "VALUES (%s,%s,%s,'ACTIVE')", (project_id, target_user_id, recipient_group_id))
    elif relation[0] != "ACTIVE" or int(relation[2]) != recipient_group_id:
        await cur.execute(
            "UPDATE office_external_relation SET recipient_group_id=%s,status='ACTIVE',version=version+1 "
            "WHERE project_id=%s AND user_id=%s AND version=%s",
            (recipient_group_id, project_id, target_user_id, int(relation[1])))
        if cur.rowcount != 1:
            raise HTTPException(status_code=409, detail="office_project_grant_version_conflict")


@router.put("/{project_id}/members/{target_user_id}")
async def grant_project_member(project_id: int, target_user_id: int, payload: ProjectGrantUpdate,
                               idempotency_key: str = Header(alias="Idempotency-Key"),
                               user: AuthUser = Depends(get_current_user)):
    """Atomically create canonical JAX membership and its Office audience grant."""
    actor_user_id = _actor_id(user)
    if project_id < 1 or target_user_id < 1:
        raise HTTPException(status_code=404, detail="office_project_grant_target_not_found")
    if payload.audience == "CLIENT_PUBLISHED" and payload.grant_role != "COLLABORATOR":
        raise HTTPException(status_code=422, detail="office_project_grant_invalid_audience_role")
    config = load_mfa_config_from_environment()
    from jax.memory.b9_mariadb import MariaDBB9Store
    from jax.memory.project_authority import (
        AlreadyMember, AuthorizationDenied, IdempotencyKeyConflict,
        InvalidIdempotencyKey, NamespacedMemberKind, ProjectAuthorityAdmin,
        ProjectAuthorityError, ProjectNamespace,
    )
    member_kind = (NamespacedMemberKind.INTERNAL if payload.audience == "INTERNAL"
                   else NamespacedMemberKind.EXTERNAL_CLIENT)
    try:
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            target_tenant_id = await _lock_grant_identities(
                cur, actor_user_id=actor_user_id, target_user_id=target_user_id, audience=payload.audience)
            result = await ProjectAuthorityAdmin(MariaDBB9Store(await get_pool())).grant_namespaced_member_in_transaction(
                cur, actor_user_id=actor_user_id, target_tenant_id=target_tenant_id,
                project_id=project_id, target_user_id=target_user_id,
                namespace=ProjectNamespace.OFFICE_CONFIDENTIAL, member_kind=member_kind,
                idempotency_key=idempotency_key,
                authorizer=_OfficeMembershipAuthorizer(
                    proof_event_id=user.mfa_proof_event_id, mfa_config=config,
                    grant_role=payload.grant_role),
            )
            digest = _membership_receipt_digest(
                operation="GRANT_OFFICE_MEMBER", actor_user_id=actor_user_id,
                target_tenant_id=target_tenant_id, project_id=project_id,
                target_user_id=target_user_id, expected_version=payload.expected_version,
                grant_role=payload.grant_role, audience=payload.audience)
            receipt = await _locked_membership_receipt(cur, idempotency_key=idempotency_key)
            if receipt is not None:
                if (not result.replayed or receipt[0] != digest
                        or str(receipt[1]) != result.membership_id or str(receipt[2]) != result.event_id):
                    raise HTTPException(status_code=409, detail="office_project_grant_idempotency_conflict")
                await _assert_receipt_companion(
                    cur, project_id=project_id, target_user_id=target_user_id, receipt=receipt)
                return {"project_id": project_id, "user_id": target_user_id,
                        "grant_role": payload.grant_role, "audience": payload.audience,
                        "version": int(receipt[3]), "replayed": True,
                        "membership_replayed": True}
            if result.replayed:
                # A JAX result without its terminal Office receipt is never a
                # successful replay: serving it would hide a torn companion.
                raise HTTPException(status_code=503, detail="office_project_grant_retryable")
            # Scope/request/membership are locked by JAX before the Office
            # recipient group.  Taking the group earlier forms a cycle with
            # project provisioning (scope -> JAX request -> group).
            recipient_group_id = (
                await _lock_active_client_group(cur, project_id=project_id)
                if payload.audience == "CLIENT_PUBLISHED" else None
            )
            await cur.execute(
                "SELECT grant_role,audience,status,version FROM office_project_grant "
                "WHERE project_id=%s AND user_id=%s FOR UPDATE", (project_id, target_user_id))
            existing = await cur.fetchone()
            if existing is None:
                if payload.expected_version != 0:
                    raise HTTPException(status_code=409, detail="office_project_grant_version_conflict")
                await cur.execute(
                    "INSERT INTO office_project_grant "
                    "(project_id,user_id,grant_role,audience,status,granted_by) VALUES (%s,%s,%s,%s,'ACTIVE',%s)",
                    (project_id, target_user_id, payload.grant_role, payload.audience, actor_user_id))
                version, replayed = 1, False
            else:
                role, audience, status, version = existing
                version = int(version)
                if status == "ACTIVE" and role == payload.grant_role and audience == payload.audience:
                    if payload.expected_version not in {0, version}:
                        raise HTTPException(status_code=409, detail="office_project_grant_version_conflict")
                    replayed = True
                else:
                    if payload.expected_version != version:
                        raise HTTPException(status_code=409, detail="office_project_grant_version_conflict")
                    await cur.execute(
                        "UPDATE office_project_grant SET grant_role=%s,audience=%s,status='ACTIVE',granted_by=%s,"
                        "version=version+1,updated_at=UTC_TIMESTAMP(6) WHERE project_id=%s AND user_id=%s AND version=%s",
                        (payload.grant_role, payload.audience, actor_user_id, project_id, target_user_id, version))
                    if cur.rowcount != 1:
                        raise HTTPException(status_code=409, detail="office_project_grant_version_conflict")
                    version, replayed = version + 1, False
            if recipient_group_id is not None:
                await _upsert_external_relation(
                    cur, project_id=project_id, target_user_id=target_user_id,
                    recipient_group_id=recipient_group_id)
            relation = None
            if recipient_group_id is not None:
                await cur.execute(
                    "SELECT relation_id,version,recipient_group_id FROM office_external_relation "
                    "WHERE project_id=%s AND user_id=%s FOR UPDATE", (project_id, target_user_id))
                relation = await cur.fetchone()
                if relation is None:
                    raise HTTPException(status_code=503, detail="office_project_grant_retryable")
            decision = await _current_project_grant_decision(
                cur, actor_user_id=actor_user_id, project_id=project_id,
                proof_event_id=user.mfa_proof_event_id, config=config,
            )
            if payload.grant_role == "PROJECT_ADMIN" and decision.global_role != "OFFICE_SUPERUSER":
                raise HTTPException(status_code=403, detail="office_project_grant_denied")
            authorization_event_id = await record_authorization_event(cur, decision)
            await _write_membership_receipt(
                cur, idempotency_key=idempotency_key, digest=digest,
                operation="GRANT_OFFICE_MEMBER", actor_user_id=actor_user_id,
                target_tenant_id=target_tenant_id, project_id=project_id,
                target_user_id=target_user_id, expected_version=payload.expected_version,
                grant_role=payload.grant_role, audience=payload.audience, result=result,
                office_grant_version=version, office_grant_status="ACTIVE",
                authorization_event_id=authorization_event_id, relation=relation)
    except HTTPException:
        raise
    except InvalidIdempotencyKey as error:
        raise HTTPException(status_code=422, detail="office_project_grant_idempotency_invalid") from error
    except IdempotencyKeyConflict as error:
        raise HTTPException(status_code=409, detail="office_project_grant_idempotency_conflict") from error
    except (AlreadyMember, AuthorizationDenied) as error:
        raise HTTPException(status_code=403, detail="office_project_grant_denied") from error
    except ProjectAuthorityError as error:
        raise HTTPException(status_code=503, detail="office_project_grant_retryable") from error
    except Exception as error:
        raise HTTPException(status_code=503, detail="office_project_grant_retryable") from error
    return {"project_id": project_id, "user_id": target_user_id, "grant_role": payload.grant_role,
            "audience": payload.audience, "version": version, "replayed": replayed,
            "membership_replayed": result.replayed}


@router.delete("/{project_id}/members/{target_user_id}")
async def revoke_project_member(project_id: int, target_user_id: int,
                                expected_version: int = Query(ge=1),
                                idempotency_key: str = Header(alias="Idempotency-Key"),
                                user: AuthUser = Depends(get_current_user)):
    """Atomically revoke the canonical membership and its Office grant."""
    actor_user_id = _actor_id(user)
    if project_id < 1 or target_user_id < 1:
        raise HTTPException(status_code=404, detail="office_project_grant_not_found")
    config = load_mfa_config_from_environment()
    from jax.memory.b9_mariadb import MariaDBB9Store
    from jax.memory.project_authority import (
        AuthorizationDenied, IdempotencyKeyConflict, InvalidIdempotencyKey,
        ProjectAuthorityAdmin, ProjectAuthorityError, ProjectNamespace,
    )
    try:
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            target_tenant_id = await _lock_grant_identities(
                cur, actor_user_id=actor_user_id, target_user_id=target_user_id,
                audience="INTERNAL", cleanup=True)
            result = await ProjectAuthorityAdmin(MariaDBB9Store(await get_pool())).revoke_namespaced_member_in_transaction(
                cur, actor_user_id=actor_user_id, target_tenant_id=target_tenant_id,
                project_id=project_id, target_user_id=target_user_id,
                namespace=ProjectNamespace.OFFICE_CONFIDENTIAL, idempotency_key=idempotency_key,
                authorizer=_OfficeMembershipAuthorizer(
                    proof_event_id=user.mfa_proof_event_id, mfa_config=config, grant_role=None),
            )
            receipt = await _locked_membership_receipt(cur, idempotency_key=idempotency_key)
            if receipt is not None:
                digest = _membership_receipt_digest(
                    operation="REVOKE_OFFICE_MEMBER", actor_user_id=actor_user_id,
                    target_tenant_id=target_tenant_id, project_id=project_id,
                    target_user_id=target_user_id, expected_version=expected_version,
                    grant_role=str(receipt[8]), audience=str(receipt[9]))
                if (not result.replayed or receipt[0] != digest
                        or str(receipt[1]) != result.membership_id or str(receipt[2]) != result.event_id):
                    raise HTTPException(status_code=409, detail="office_project_grant_idempotency_conflict")
                await _assert_receipt_companion(
                    cur, project_id=project_id, target_user_id=target_user_id, receipt=receipt)
                return {"project_id": project_id, "user_id": target_user_id,
                        "version": int(receipt[3]), "replayed": True,
                        "membership_replayed": True}
            if result.replayed:
                raise HTTPException(status_code=503, detail="office_project_grant_retryable")
            await cur.execute(
                "SELECT grant_role,audience,status,version FROM office_project_grant WHERE project_id=%s AND user_id=%s FOR UPDATE",
                (project_id, target_user_id))
            grant = await cur.fetchone()
            if grant is None:
                raise HTTPException(status_code=404, detail="office_project_grant_not_found")
            grant_role, audience, grant_status, grant_version = grant
            digest = _membership_receipt_digest(
                operation="REVOKE_OFFICE_MEMBER", actor_user_id=actor_user_id,
                target_tenant_id=target_tenant_id, project_id=project_id,
                target_user_id=target_user_id, expected_version=expected_version,
                grant_role=str(grant_role), audience=str(audience))
            if grant_status == "REVOKED":
                # JAX replay proves this same idempotency request already
                # revoked canonical membership.  Its Office companion has
                # therefore advanced exactly once from expected_version.
                if not result.replayed or int(grant_version) != expected_version + 1:
                    raise HTTPException(status_code=409, detail="office_project_grant_version_conflict")
                version, replayed = int(grant_version), True
            else:
                if int(grant_version) != expected_version:
                    raise HTTPException(status_code=409, detail="office_project_grant_version_conflict")
                await cur.execute(
                    "UPDATE office_project_grant SET status='REVOKED',version=version+1,updated_at=UTC_TIMESTAMP(6) "
                    "WHERE project_id=%s AND user_id=%s AND version=%s",
                    (project_id, target_user_id, expected_version))
                if cur.rowcount != 1:
                    raise HTTPException(status_code=409, detail="office_project_grant_version_conflict")
                version, replayed = expected_version + 1, False
            if str(audience) == "CLIENT_PUBLISHED":
                await cur.execute(
                    "UPDATE office_external_relation SET status='REVOKED',version=version+1 "
                    "WHERE project_id=%s AND user_id=%s AND status='ACTIVE'",
                    (project_id, target_user_id))
            relation = None
            if str(audience) == "CLIENT_PUBLISHED":
                await cur.execute(
                    "SELECT relation_id,version,recipient_group_id FROM office_external_relation "
                    "WHERE project_id=%s AND user_id=%s FOR UPDATE", (project_id, target_user_id))
                relation = await cur.fetchone()
                if relation is None:
                    raise HTTPException(status_code=503, detail="office_project_grant_retryable")
            decision = await _current_project_grant_decision(
                cur, actor_user_id=actor_user_id, project_id=project_id,
                proof_event_id=user.mfa_proof_event_id, config=config,
            )
            authorization_event_id = await record_authorization_event(cur, decision)
            await _write_membership_receipt(
                cur, idempotency_key=idempotency_key, digest=digest,
                operation="REVOKE_OFFICE_MEMBER", actor_user_id=actor_user_id,
                target_tenant_id=target_tenant_id, project_id=project_id,
                target_user_id=target_user_id, expected_version=expected_version,
                grant_role=str(grant_role), audience=str(audience), result=result,
                office_grant_version=version, office_grant_status="REVOKED",
                authorization_event_id=authorization_event_id, relation=relation)
    except HTTPException:
        raise
    except InvalidIdempotencyKey as error:
        raise HTTPException(status_code=422, detail="office_project_grant_idempotency_invalid") from error
    except IdempotencyKeyConflict as error:
        raise HTTPException(status_code=409, detail="office_project_grant_idempotency_conflict") from error
    except AuthorizationDenied as error:
        raise HTTPException(status_code=403, detail="office_project_grant_denied") from error
    except ProjectAuthorityError as error:
        raise HTTPException(status_code=503, detail="office_project_grant_retryable") from error
    except Exception as error:
        raise HTTPException(status_code=503, detail="office_project_grant_retryable") from error
    return {"project_id": project_id, "user_id": target_user_id, "version": version,
            "replayed": replayed, "membership_replayed": result.replayed}


@router.put("/{project_id}/professionals/{target_user_id}")
async def grant_professional_capability(project_id: int, target_user_id: int,
                                        payload: ProfessionalGrantUpdate,
                                        user: AuthUser = Depends(get_current_user)):
    """Grant the sole V1 professional capability to an existing staff member.

    Project administrators may manage collaborators but cannot mint approval
    authority.  The root check is separate from the project membership check
    and is repeated after the target/grant locks are acquired.
    """
    actor_user_id = _actor_id(user)
    if project_id < 1 or target_user_id < 1:
        raise HTTPException(status_code=404, detail="office_professional_target_not_found")
    config = load_mfa_config_from_environment()
    async with transaccion(AISLAMIENTO_ADMIN) as cur:
        decision = await authorize_office_in_cursor(
            cur, actor_user_id, OfficeOperation.ADMINISTER_PROJECT_MEMBERS, project_id,
            require_recent_mfa=True, mfa_proof_event_id=user.mfa_proof_event_id, mfa_config=config,
        )
        if not decision.allowed or decision.global_role != "OFFICE_SUPERUSER":
            raise HTTPException(status_code=403, detail="office_professional_grant_denied")
        await cur.execute(
            "SELECT u.tenant_id,u.status,u.account_class,t.status,p.status "
            "FROM jax_users u JOIN jax_tenants t ON t.tenant_id=u.tenant_id "
            "JOIN office_account_profile p ON p.user_id=u.user_id WHERE u.user_id=%s FOR UPDATE",
            (target_user_id,),
        )
        target = await cur.fetchone()
        if (target is None or target[1] != "active" or target[3] != "active" or target[4] != "ACTIVE"
                or target[2] not in {"INTERNAL", "OFFICE_INTERNAL"}):
            raise HTTPException(status_code=404, detail="office_professional_target_not_found")
        await cur.execute(
            "SELECT g.status,m.status FROM office_project_grant g JOIN jax_project_membership m "
            "ON m.project_id=g.project_id AND m.user_id=g.user_id "
            "WHERE g.project_id=%s AND g.user_id=%s AND g.audience='INTERNAL' FOR UPDATE",
            (project_id, target_user_id),
        )
        membership = await cur.fetchone()
        if membership is None or membership[0] != "ACTIVE" or membership[1] != "ACTIVE":
            raise HTTPException(status_code=409, detail="office_professional_member_required")
        await cur.execute(
            "SELECT professional_grant_id,status,version,basis,evidence_ref FROM office_professional_grant "
            "WHERE project_id=%s AND user_id=%s AND capability='PROFESSIONAL_ACTION' FOR UPDATE",
            (project_id, target_user_id),
        )
        existing = await cur.fetchone()
        if existing is None:
            if payload.expected_version != 0:
                raise HTTPException(status_code=409, detail="office_professional_grant_version_conflict")
            await cur.execute(
                "INSERT INTO office_professional_grant "
                "(project_id,user_id,capability,basis,evidence_ref,status,granted_by) "
                "VALUES (%s,%s,'PROFESSIONAL_ACTION',%s,%s,'ACTIVE',%s)",
                (project_id, target_user_id, payload.basis, payload.evidence_ref, actor_user_id),
            )
            version, replayed = 1, False
        else:
            _grant_id, status, version, basis, evidence_ref = existing
            version = int(version)
            if (status == "ACTIVE" and basis == payload.basis and evidence_ref == payload.evidence_ref):
                if payload.expected_version not in {0, version}:
                    raise HTTPException(status_code=409, detail="office_professional_grant_version_conflict")
                replayed = True
            else:
                if payload.expected_version != version:
                    raise HTTPException(status_code=409, detail="office_professional_grant_version_conflict")
                await cur.execute(
                    "UPDATE office_professional_grant SET basis=%s,evidence_ref=%s,status='ACTIVE',"
                    "granted_by=%s,version=version+1 WHERE project_id=%s AND user_id=%s "
                    "AND capability='PROFESSIONAL_ACTION' AND version=%s",
                    (payload.basis, payload.evidence_ref, actor_user_id, project_id, target_user_id, version),
                )
                if cur.rowcount != 1:
                    raise HTTPException(status_code=409, detail="office_professional_grant_version_conflict")
                version, replayed = version + 1, False
        decision = await authorize_office_in_cursor(
            cur, actor_user_id, OfficeOperation.ADMINISTER_PROJECT_MEMBERS, project_id,
            require_recent_mfa=True, mfa_proof_event_id=user.mfa_proof_event_id, mfa_config=config,
        )
        if not decision.allowed or decision.global_role != "OFFICE_SUPERUSER":
            raise HTTPException(status_code=403, detail="office_professional_grant_denied")
        await record_authorization_event(cur, decision)
    return {"project_id": project_id, "user_id": target_user_id,
            "capability": "PROFESSIONAL_ACTION", "version": version, "replayed": replayed}


@router.delete("/{project_id}/professionals/{target_user_id}")
async def revoke_professional_capability(project_id: int, target_user_id: int,
                                         expected_version: int = Query(ge=1),
                                         user: AuthUser = Depends(get_current_user)):
    """Revoke only the Office professional capability; membership survives."""
    actor_user_id = _actor_id(user)
    if project_id < 1 or target_user_id < 1:
        raise HTTPException(status_code=404, detail="office_professional_grant_not_found")
    config = load_mfa_config_from_environment()
    async with transaccion(AISLAMIENTO_ADMIN) as cur:
        decision = await authorize_office_in_cursor(
            cur, actor_user_id, OfficeOperation.ADMINISTER_PROJECT_MEMBERS, project_id,
            require_recent_mfa=True, mfa_proof_event_id=user.mfa_proof_event_id, mfa_config=config,
        )
        if not decision.allowed or decision.global_role != "OFFICE_SUPERUSER":
            raise HTTPException(status_code=403, detail="office_professional_grant_denied")
        await cur.execute(
            "SELECT status,version FROM office_professional_grant WHERE project_id=%s AND user_id=%s "
            "AND capability='PROFESSIONAL_ACTION' FOR UPDATE",
            (project_id, target_user_id),
        )
        grant = await cur.fetchone()
        if grant is None:
            raise HTTPException(status_code=404, detail="office_professional_grant_not_found")
        if int(grant[1]) != expected_version:
            raise HTTPException(status_code=409, detail="office_professional_grant_version_conflict")
        if grant[0] == "REVOKED":
            return {"project_id": project_id, "user_id": target_user_id,
                    "capability": "PROFESSIONAL_ACTION", "version": expected_version, "replayed": True}
        await cur.execute(
            "UPDATE office_professional_grant SET status='REVOKED',version=version+1 "
            "WHERE project_id=%s AND user_id=%s AND capability='PROFESSIONAL_ACTION' AND version=%s",
            (project_id, target_user_id, expected_version),
        )
        if cur.rowcount != 1:
            raise HTTPException(status_code=409, detail="office_professional_grant_version_conflict")
        decision = await authorize_office_in_cursor(
            cur, actor_user_id, OfficeOperation.ADMINISTER_PROJECT_MEMBERS, project_id,
            require_recent_mfa=True, mfa_proof_event_id=user.mfa_proof_event_id, mfa_config=config,
        )
        if not decision.allowed or decision.global_role != "OFFICE_SUPERUSER":
            raise HTTPException(status_code=403, detail="office_professional_grant_denied")
        await record_authorization_event(cur, decision)
    return {"project_id": project_id, "user_id": target_user_id,
            "capability": "PROFESSIONAL_ACTION", "version": expected_version + 1, "replayed": False}
