"""Office-only directory and global-role administration.

This surface deliberately operates only on an existing permanent Office
profile.  Creating an identity or binding an unprofiled canonical user has a
separate own-credential proof contract and remains in the invitation/bootstrap
slice; a directory route must never become an account-enablement shortcut.
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field

from auth.middleware import get_current_user
from auth.models import AuthUser
from db.transaccion import AISLAMIENTO_ADMIN, transaccion
from virtual_office.audit import record_authorization_event
from virtual_office.authority import authorize_office_in_cursor
from virtual_office.capabilities import OfficeOperation
from virtual_office.mfa import load_mfa_config_from_environment
from virtual_office.bootstrap import credential_self_control_in_cursor


router = APIRouter(prefix="/api/virtual-office/people")
_ROLES = Literal["COLLABORATOR", "GENERAL_ADMIN", "OFFICE_SUPERUSER"]


class GlobalRoleUpdate(BaseModel):
    global_role: _ROLES
    expected_version: int = Field(ge=1)


class DelegationUpdate(BaseModel):
    expected_version: int = Field(ge=0)


class ProfileEnable(BaseModel):
    """Bind (or reactivate) an existing self-controlled Office identity."""
    expected_version: int = Field(ge=0)


def _actor_id(user: AuthUser) -> int:
    try:
        return int(user.user_id)
    except (TypeError, ValueError) as error:
        raise HTTPException(status_code=403, detail="office_people_access_denied") from error


async def _current_admin(cur, *, actor_user_id: int, proof_event_id: int | None):
    decision = await authorize_office_in_cursor(
        cur, actor_user_id, OfficeOperation.ADMINISTER_OFFICE,
        require_recent_mfa=True, mfa_proof_event_id=proof_event_id,
        mfa_config=load_mfa_config_from_environment(),
    )
    if not decision.allowed:
        raise HTTPException(status_code=403, detail="office_people_access_denied")
    return decision


async def _directory_reader(cur, *, actor_user_id: int):
    decision = await authorize_office_in_cursor(
        cur, actor_user_id, OfficeOperation.VIEW_OFFICE_DIRECTORY,
    )
    if not decision.allowed:
        raise HTTPException(status_code=403, detail="office_people_access_denied")
    return decision


async def _lock_actor_target_tenants(cur, *, actor_user_id: int, target_user_id: int,
                                     proof_event_id: int | None) -> int:
    """Authorize the actor before target state can shape an API response."""
    await cur.execute("SELECT tenant_id FROM jax_users WHERE user_id=%s", (actor_user_id,))
    actor = await cur.fetchone()
    await cur.execute("SELECT tenant_id FROM jax_users WHERE user_id=%s", (target_user_id,))
    target = await cur.fetchone()
    # Target is an untrusted lock-order hint only.  An INTERNAL user without
    # an Office profile must receive the same opaque denial whether it names
    # a real user or an absent one.
    if actor is None:
        raise HTTPException(status_code=403, detail="office_people_access_denied")
    target_tenant_id = None if target is None else int(target[0])
    tenant_hints = {int(actor[0])}
    if target_tenant_id is not None:
        tenant_hints.add(target_tenant_id)
    tenant_rows: set[int] = set()
    for tenant_id in sorted(tenant_hints):
        await cur.execute("SELECT tenant_id FROM jax_tenants WHERE tenant_id=%s FOR UPDATE", (tenant_id,))
        row = await cur.fetchone()
        if row is not None:
            tenant_rows.add(int(row[0]))
    # Hints establish only a deterministic tenant-lock order.  Re-read both
    # identities while locked so a tenant reassignment cannot turn the later
    # profile/delegation mutation into cross-tenant authority.
    await cur.execute("SELECT tenant_id FROM jax_users WHERE user_id=%s FOR UPDATE", (actor_user_id,))
    actor_locked = await cur.fetchone()
    if actor_locked is None or int(actor_locked[0]) != int(actor[0]):
        raise HTTPException(status_code=409, detail="office_delegation_target_drift")
    if int(actor[0]) not in tenant_rows:
        raise HTTPException(status_code=403, detail="office_people_access_denied")
    decision = await _current_admin(cur, actor_user_id=actor_user_id, proof_event_id=proof_event_id)
    # Every target-taking people route is root-only.  Reject before target
    # discovery so a delegated/general Office administrator cannot enumerate
    # identities through their different target outcomes.
    if decision.global_role != "OFFICE_SUPERUSER":
        raise HTTPException(status_code=403, detail="office_people_access_denied")
    if target_tenant_id is None or target_tenant_id not in tenant_rows:
        raise HTTPException(status_code=404, detail="office_delegation_target_not_found")
    await cur.execute("SELECT tenant_id FROM jax_users WHERE user_id=%s FOR UPDATE", (target_user_id,))
    target_locked = await cur.fetchone()
    if target_locked is None or int(target_locked[0]) != target_tenant_id:
        raise HTTPException(status_code=409, detail="office_delegation_target_drift")
    return target_tenant_id


@router.put("/{target_user_id}/profile")
async def enable_existing_profile(target_user_id: int, payload: ProfileEnable,
                                  user: AuthUser = Depends(get_current_user)):
    """Enable only an existing identity that has proved control of its own credential.

    This deliberately has no password input.  An administrator knowing or
    resetting a password cannot create Office access for its subject.
    """
    actor_user_id = _actor_id(user)
    if target_user_id < 1:
        raise HTTPException(status_code=404, detail="office_person_not_found")
    async with transaccion(AISLAMIENTO_ADMIN) as cur:
        await _lock_actor_target_tenants(cur, actor_user_id=actor_user_id, target_user_id=target_user_id,
                                         proof_event_id=user.mfa_proof_event_id)
        decision = await _current_admin(cur, actor_user_id=actor_user_id,
                                        proof_event_id=user.mfa_proof_event_id)
        if decision.global_role != "OFFICE_SUPERUSER":
            raise HTTPException(status_code=403, detail="office_profile_enable_denied")
        await cur.execute(
            "SELECT u.status,u.account_class,t.status,p.status,p.version "
            "FROM jax_users u JOIN jax_tenants t ON t.tenant_id=u.tenant_id "
            "LEFT JOIN office_account_profile p ON p.user_id=u.user_id "
            "WHERE u.user_id=%s FOR UPDATE", (target_user_id,))
        target = await cur.fetchone()
        credential_audit_id = await credential_self_control_in_cursor(cur, user_id=target_user_id)
        if (target is None or target[0] != "active" or target[2] != "active"
                or target[1] not in {"INTERNAL", "OFFICE_INTERNAL"}
                or credential_audit_id is None):
            raise HTTPException(status_code=403, detail="office_profile_self_control_required")
        status = target[3]
        version = None if target[4] is None else int(target[4])
        if status is None:
            if payload.expected_version != 0:
                raise HTTPException(status_code=409, detail="office_profile_version_conflict")
            await cur.execute(
                "INSERT INTO office_account_profile "
                "(user_id,global_role,status,version,bound_by_user_id,credential_audit_id,created_at,updated_at) "
                "VALUES (%s,'COLLABORATOR','ACTIVE',1,%s,%s,UTC_TIMESTAMP(6),UTC_TIMESTAMP(6))",
                (target_user_id, actor_user_id, credential_audit_id))
            next_version, replayed = 1, False
        elif status == "ACTIVE":
            if payload.expected_version not in {0, version}:
                raise HTTPException(status_code=409, detail="office_profile_version_conflict")
            next_version, replayed = version, True
        else:
            # A profile binding is permanent evidence.  Revocation never
            # becomes an implicit re-enrolment path, even for a later root.
            raise HTTPException(status_code=409, detail="office_profile_revoked")
        decision = await _current_admin(cur, actor_user_id=actor_user_id,
                                        proof_event_id=user.mfa_proof_event_id)
        if decision.global_role != "OFFICE_SUPERUSER":
            raise HTTPException(status_code=403, detail="office_profile_enable_denied")
        await record_authorization_event(cur, decision)
    return {"user_id": target_user_id, "status": "ACTIVE", "version": next_version, "replayed": replayed}


@router.delete("/{target_user_id}/profile")
async def revoke_existing_profile(target_user_id: int, expected_version: int = Query(ge=1),
                                  user: AuthUser = Depends(get_current_user)):
    """Revoke a permanent profile without deleting its security history."""
    actor_user_id = _actor_id(user)
    async with transaccion(AISLAMIENTO_ADMIN) as cur:
        await _lock_actor_target_tenants(cur, actor_user_id=actor_user_id, target_user_id=target_user_id,
                                         proof_event_id=user.mfa_proof_event_id)
        decision = await _current_admin(cur, actor_user_id=actor_user_id,
                                        proof_event_id=user.mfa_proof_event_id)
        if decision.global_role != "OFFICE_SUPERUSER" or target_user_id == actor_user_id:
            raise HTTPException(status_code=403, detail="office_profile_revoke_denied")
        await cur.execute("SELECT global_role,status,version FROM office_account_profile WHERE user_id=%s FOR UPDATE", (target_user_id,))
        profile = await cur.fetchone()
        if profile is None:
            raise HTTPException(status_code=404, detail="office_person_not_found")
        if profile[0] == "OFFICE_SUPERUSER":
            raise HTTPException(status_code=409, detail="office_root_demotion_requires_recovery")
        if int(profile[2]) != expected_version:
            raise HTTPException(status_code=409, detail="office_profile_version_conflict")
        if profile[1] == "REVOKED":
            return {"user_id": target_user_id, "status": "REVOKED", "version": expected_version, "replayed": True}
        # Keep a durable explanation before changing the profile state.  Both
        # rows share this transaction, so a failed CAS also removes the event.
        authorization_event_id = await record_authorization_event(cur, decision)
        await cur.execute(
            "INSERT INTO office_profile_revocation_event "
            "(actor_user_id,target_user_id,action,version_before,version_after,reason_code,authorization_event_id,occurred_at) "
            "VALUES (%s,%s,'REVOKE_PROFILE',%s,%s,'office_profile_revoke',%s,UTC_TIMESTAMP(6))",
            (actor_user_id, target_user_id, expected_version, expected_version + 1, authorization_event_id),
        )
        await cur.execute("UPDATE office_account_profile SET status='REVOKED',version=version+1,updated_at=UTC_TIMESTAMP(6) WHERE user_id=%s AND status='ACTIVE' AND version=%s", (target_user_id, expected_version))
        if cur.rowcount != 1:
            raise HTTPException(status_code=409, detail="office_profile_version_conflict")
    return {"user_id": target_user_id, "status": "REVOKED", "version": expected_version + 1, "replayed": False}


@router.get("")
async def list_people(
    response: Response,
    tenant_id: int | None = Query(default=None, gt=0),
    limit: int = Query(default=50, ge=1, le=100),
    after_user_id: int | None = Query(default=None, gt=0),
    user: AuthUser = Depends(get_current_user),
):
    """Return directory metadata, never credentials, tokens, or audit detail."""
    actor_user_id = _actor_id(user)
    async with transaccion(AISLAMIENTO_ADMIN) as cur:
        decision = await _directory_reader(cur, actor_user_id=actor_user_id)
        # A delegated general admin remains tenant scoped.  Root can cross
        # Office tenants, but only has access to active canonical profiles.
        if decision.global_role != "OFFICE_SUPERUSER":
            if tenant_id is not None and tenant_id != decision.tenant_id:
                raise HTTPException(status_code=403, detail="office_people_access_denied")
            target_tenant_id = decision.tenant_id
        else:
            target_tenant_id = tenant_id
        sql = (
            "SELECT u.user_id,u.tenant_id,u.email,u.account_class,p.global_role,p.status,p.version "
            "FROM office_account_profile p JOIN jax_users u ON u.user_id=p.user_id "
            "JOIN jax_tenants t ON t.tenant_id=u.tenant_id "
            "WHERE u.status='active' AND t.status='active' AND p.status='ACTIVE'"
        )
        params: list[object] = []
        if target_tenant_id is not None:
            sql += " AND u.tenant_id=%s"
            params.append(target_tenant_id)
        if after_user_id is not None:
            sql += " AND u.user_id>%s"
            params.append(after_user_id)
        sql += " ORDER BY u.user_id LIMIT %s"
        params.append(limit + 1)
        await cur.execute(sql, tuple(params))
        rows = await cur.fetchall()
    output = [
        {"user_id": int(row[0]), "tenant_id": int(row[1]), "email": row[2],
         "account_class": row[3], "global_role": row[4],
         "status": row[5], "version": int(row[6])}
        for row in rows[:limit]
    ]
    response.headers["Cache-Control"] = "no-store"
    response.headers["Vary"] = "Authorization"
    return {"people": output, "next_after_user_id": output[-1]["user_id"] if len(rows) > limit else None}


@router.get("/assignable-projects")
async def list_assignable_projects(response: Response,
                                   limit: int = Query(default=50, ge=1, le=100),
                                   after_project_id: int = Query(default=0, ge=0),
                                   user: AuthUser = Depends(get_current_user)):
    """Return names of tenant projects a delegated administrator can staff."""
    async with transaccion(AISLAMIENTO_ADMIN) as cur:
        decision = await _directory_reader(cur, actor_user_id=_actor_id(user))
        if decision.global_role == "OFFICE_SUPERUSER":
            raise HTTPException(status_code=403, detail="office_people_access_denied")
        await cur.execute(
            "SELECT s.project_id,p.name FROM jax_project_scope s "
            "FORCE INDEX (idx_jax_project_scope_tenant_status) "
            "JOIN projects p ON p.id=s.project_id "
            "WHERE s.tenant_id=%s AND s.status='ACTIVE' "
            "AND s.authorization_namespace='OFFICE_CONFIDENTIAL' "
            "AND s.project_id>%s ORDER BY s.project_id LIMIT %s",
            (decision.tenant_id, after_project_id, limit + 1),
        )
        rows = await cur.fetchall()
    page = rows[:limit]
    response.headers["Cache-Control"] = "no-store"
    response.headers["Vary"] = "Authorization"
    return {"projects": [{"id": int(row[0]), "name": row[1], "tenant_id": decision.tenant_id} for row in page],
            "next_after_id": int(page[-1][0]) if len(rows) > limit else None}


@router.get("/{target_user_id}/projects")
async def list_person_projects(target_user_id: int, response: Response,
                               limit: int = Query(default=50, ge=1, le=100),
                               after_project_id: int = Query(default=0, ge=0),
                               user: AuthUser = Depends(get_current_user)):
    """Show one Office person's project grants without exposing other tenants."""
    if target_user_id < 1:
        raise HTTPException(status_code=404, detail="office_person_not_found")
    async with transaccion(AISLAMIENTO_ADMIN) as cur:
        decision = await _directory_reader(cur, actor_user_id=_actor_id(user))
        await cur.execute(
            "SELECT u.tenant_id,u.account_class FROM jax_users u "
            "JOIN office_account_profile a ON a.user_id=u.user_id "
            "JOIN jax_tenants t ON t.tenant_id=u.tenant_id "
            "WHERE u.user_id=%s AND u.status='active' AND a.status='ACTIVE' AND t.status='active'",
            (target_user_id,),
        )
        target = await cur.fetchone()
        if target is None or (decision.global_role != "OFFICE_SUPERUSER"
                              and int(target[0]) != decision.tenant_id):
            raise HTTPException(status_code=404, detail="office_person_not_found")
        audience = "CLIENT_PUBLISHED" if target[1] == "EXTERNAL_CLIENT" else "INTERNAL"
        await cur.execute(
            "SELECT g.project_id,p.name,g.grant_role,g.version "
            "FROM office_project_grant g FORCE INDEX (idx_office_project_grant_list) "
            "JOIN jax_project_scope s ON s.project_id=g.project_id "
            "JOIN projects p ON p.id=g.project_id "
            "JOIN jax_project_membership m ON m.project_id=g.project_id AND m.user_id=g.user_id "
            "WHERE g.user_id=%s AND g.status='ACTIVE' AND g.audience=%s "
            "AND g.project_id>%s AND s.tenant_id=%s "
            "AND s.authorization_namespace='OFFICE_CONFIDENTIAL' AND s.status='ACTIVE' "
            "AND m.status='ACTIVE' ORDER BY g.project_id LIMIT %s",
            (target_user_id, audience, after_project_id, int(target[0]), limit + 1),
        )
        rows = await cur.fetchall()
    page = rows[:limit]
    response.headers["Cache-Control"] = "no-store"
    response.headers["Vary"] = "Authorization"
    return {"user_id": target_user_id, "projects": [
        {"project_id": int(row[0]), "name": row[1], "grant_role": row[2],
         "version": int(row[3]), "audience": audience}
        for row in page], "next_after_project_id": int(page[-1][0]) if len(rows) > limit else None}


@router.put("/{target_user_id}/global-role")
async def update_global_role(target_user_id: int, payload: GlobalRoleUpdate,
                             user: AuthUser = Depends(get_current_user)):
    """Root-only mutation of an existing Office profile.

    The profile is permanent; this endpoint changes its role/version but does
    not create, delete, reactivate, or convert a canonical identity.
    """
    actor_user_id = _actor_id(user)
    if target_user_id < 1:
        raise HTTPException(status_code=404, detail="office_person_not_found")
    async with transaccion(AISLAMIENTO_ADMIN) as cur:
        target_tenant_hint = await _lock_actor_target_tenants(
            cur, actor_user_id=actor_user_id, target_user_id=target_user_id,
            proof_event_id=user.mfa_proof_event_id)
        decision = await _current_admin(cur, actor_user_id=actor_user_id,
                                        proof_event_id=user.mfa_proof_event_id)
        if decision.global_role != "OFFICE_SUPERUSER":
            raise HTTPException(status_code=403, detail="office_global_role_denied")
        await cur.execute(
            "SELECT u.tenant_id,u.status,u.account_class,t.status,p.global_role,p.status,p.version "
            "FROM jax_users u JOIN jax_tenants t ON t.tenant_id=u.tenant_id "
            "LEFT JOIN office_account_profile p ON p.user_id=u.user_id "
            "WHERE u.user_id=%s FOR UPDATE",
            (target_user_id,),
        )
        target = await cur.fetchone()
        if (target is None or int(target[0]) != target_tenant_hint or target[1] != "active" or target[3] != "active"
                or target[4] is None or target[5] != "ACTIVE"):
            raise HTTPException(status_code=404, detail="office_person_not_found")
        if target[2] == "EXTERNAL_CLIENT" and payload.global_role != "COLLABORATOR":
            raise HTTPException(status_code=409, detail="office_external_role_incompatible")
        if int(target[6]) != payload.expected_version:
            raise HTTPException(status_code=409, detail="office_profile_version_conflict")
        # Removing an Office root is the separate recovery/continuity
        # ceremony, not an ordinary profile edit.  In particular, two roots
        # must never be able to concurrently demote each other and leave no
        # authority capable of performing the recovery contract.
        if target[4] == "OFFICE_SUPERUSER" and payload.global_role != "OFFICE_SUPERUSER":
            raise HTTPException(status_code=409, detail="office_root_demotion_requires_recovery")
        # Reread current authority after the target profile lock.  A revoked
        # root can never use a decision taken before this write point.
        decision = await _current_admin(cur, actor_user_id=actor_user_id,
                                        proof_event_id=user.mfa_proof_event_id)
        if decision.global_role != "OFFICE_SUPERUSER":
            raise HTTPException(status_code=403, detail="office_global_role_denied")
        await cur.execute(
            "UPDATE office_account_profile SET global_role=%s,version=version+1,updated_at=UTC_TIMESTAMP(6) "
            "WHERE user_id=%s AND version=%s AND status='ACTIVE'",
            (payload.global_role, target_user_id, payload.expected_version),
        )
        if cur.rowcount != 1:
            raise HTTPException(status_code=409, detail="office_profile_version_conflict")
        await record_authorization_event(cur, decision)
    return {"user_id": target_user_id, "global_role": payload.global_role,
            "version": payload.expected_version + 1}


@router.put("/{target_user_id}/delegation")
async def grant_tenant_administration(target_user_id: int, payload: DelegationUpdate,
                                      user: AuthUser = Depends(get_current_user)):
    """Grant tenant-scoped general administration to an existing GENERAL_ADMIN."""
    actor_user_id = _actor_id(user)
    config = load_mfa_config_from_environment()
    async with transaccion(AISLAMIENTO_ADMIN) as cur:
        target_tenant_hint = await _lock_actor_target_tenants(
            cur, actor_user_id=actor_user_id, target_user_id=target_user_id,
            proof_event_id=user.mfa_proof_event_id)
        decision = await _current_admin(cur, actor_user_id=actor_user_id,
                                        proof_event_id=user.mfa_proof_event_id)
        if decision.global_role != "OFFICE_SUPERUSER":
            raise HTTPException(status_code=403, detail="office_delegation_denied")
        await cur.execute(
            "SELECT u.tenant_id,u.status,t.status,p.global_role,p.status FROM jax_users u "
            "JOIN jax_tenants t ON t.tenant_id=u.tenant_id JOIN office_account_profile p ON p.user_id=u.user_id "
            "WHERE u.user_id=%s FOR UPDATE", (target_user_id,))
        target = await cur.fetchone()
        if target is None or target[1] != "active" or target[2] != "active" or target[3] != "GENERAL_ADMIN" or target[4] != "ACTIVE":
            raise HTTPException(status_code=404, detail="office_delegation_target_not_found")
        tenant_id = int(target[0])
        if tenant_id != target_tenant_hint:
            raise HTTPException(status_code=409, detail="office_delegation_target_drift")
        await cur.execute("SELECT delegation_id,status,version FROM office_admin_delegation WHERE tenant_id=%s AND user_id=%s AND capability='ADMINISTER_OFFICE' FOR UPDATE", (tenant_id, target_user_id))
        existing = await cur.fetchone()
        if existing is None:
            if payload.expected_version != 0:
                raise HTTPException(status_code=409, detail="office_delegation_version_conflict")
            await cur.execute("INSERT INTO office_admin_delegation (tenant_id,user_id,capability,status,granted_by) VALUES (%s,%s,'ADMINISTER_OFFICE','ACTIVE',%s)", (tenant_id, target_user_id, actor_user_id))
            version, replayed = 1, False
        else:
            status, version = existing[1], int(existing[2])
            if status == "ACTIVE":
                if payload.expected_version not in {0, version}:
                    raise HTTPException(status_code=409, detail="office_delegation_version_conflict")
                replayed = True
            else:
                if payload.expected_version != version:
                    raise HTTPException(status_code=409, detail="office_delegation_version_conflict")
                await cur.execute("UPDATE office_admin_delegation SET status='ACTIVE',granted_by=%s,version=version+1 WHERE tenant_id=%s AND user_id=%s AND capability='ADMINISTER_OFFICE' AND version=%s", (actor_user_id, tenant_id, target_user_id, version))
                version, replayed = version + 1, False
        decision = await _current_admin(cur, actor_user_id=actor_user_id, proof_event_id=user.mfa_proof_event_id)
        if decision.global_role != "OFFICE_SUPERUSER":
            raise HTTPException(status_code=403, detail="office_delegation_denied")
        await record_authorization_event(cur, decision)
    return {"user_id": target_user_id, "tenant_id": tenant_id, "capability": "ADMINISTER_OFFICE", "version": version, "replayed": replayed}


@router.delete("/{target_user_id}/delegation")
async def revoke_tenant_administration(target_user_id: int, expected_version: int = Query(ge=1),
                                       user: AuthUser = Depends(get_current_user)):
    actor_user_id = _actor_id(user)
    async with transaccion(AISLAMIENTO_ADMIN) as cur:
        target_tenant_hint = await _lock_actor_target_tenants(
            cur, actor_user_id=actor_user_id, target_user_id=target_user_id,
            proof_event_id=user.mfa_proof_event_id)
        decision = await _current_admin(cur, actor_user_id=actor_user_id,
                                        proof_event_id=user.mfa_proof_event_id)
        if decision.global_role != "OFFICE_SUPERUSER":
            raise HTTPException(status_code=403, detail="office_delegation_denied")
        await cur.execute("SELECT tenant_id FROM jax_users WHERE user_id=%s FOR UPDATE", (target_user_id,))
        target = await cur.fetchone()
        if target is None:
            raise HTTPException(status_code=404, detail="office_delegation_target_not_found")
        tenant_id = int(target[0])
        if tenant_id != target_tenant_hint:
            raise HTTPException(status_code=409, detail="office_delegation_target_drift")
        await cur.execute("SELECT status,version FROM office_admin_delegation WHERE tenant_id=%s AND user_id=%s AND capability='ADMINISTER_OFFICE' FOR UPDATE", (tenant_id, target_user_id))
        delegation = await cur.fetchone()
        if delegation is None:
            raise HTTPException(status_code=404, detail="office_delegation_not_found")
        if int(delegation[1]) != expected_version:
            raise HTTPException(status_code=409, detail="office_delegation_version_conflict")
        if delegation[0] == "REVOKED":
            return {"user_id": target_user_id, "tenant_id": tenant_id, "capability": "ADMINISTER_OFFICE", "version": expected_version, "replayed": True}
        await cur.execute("UPDATE office_admin_delegation SET status='REVOKED',version=version+1 WHERE tenant_id=%s AND user_id=%s AND capability='ADMINISTER_OFFICE' AND version=%s", (tenant_id, target_user_id, expected_version))
        if cur.rowcount != 1:
            raise HTTPException(status_code=409, detail="office_delegation_version_conflict")
        decision = await _current_admin(cur, actor_user_id=actor_user_id, proof_event_id=user.mfa_proof_event_id)
        if decision.global_role != "OFFICE_SUPERUSER":
            raise HTTPException(status_code=403, detail="office_delegation_denied")
        await record_authorization_event(cur, decision)
    return {"user_id": target_user_id, "tenant_id": tenant_id, "capability": "ADMINISTER_OFFICE", "version": expected_version + 1, "replayed": False}
